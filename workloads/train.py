#!/usr/bin/env python3
"""Bounded synthetic training. CUDA jobs require an existing Slurm allocation.

CPU: python -m workloads.train --profile cpu --output-dir /tmp/run-unique
DDP: torchrun --standalone --nproc-per-node=2 -m workloads.train --profile ddp ...
GPU: use the reviewed Slurm templates; this program never SSHs or submits jobs.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workloads.contract import (
    COMMUNICATION_TIMEOUT_SECONDS,
    TORCH_VERSION,
    RunSpec,
    checkpoint_identity,
    local_batch_slice,
    rank_environment,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    for name, default in RunSpec().as_dict().items():
        result.add_argument("--" + name.replace("_", "-"), type=type(default), default=default)
    result.add_argument("--output-dir", required=True, type=Path)
    result.add_argument("--checkpoint-dir", type=Path)
    result.add_argument("--resume-from", type=Path)
    return result


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def make_model(torch, spec: RunSpec):
    nn = torch.nn

    class Block(nn.Module):
        def __init__(self):
            super().__init__()
            self.norm1 = nn.LayerNorm(spec.width)
            self.qkv = nn.Linear(spec.width, spec.width * 3)
            self.projection = nn.Linear(spec.width, spec.width)
            self.norm2 = nn.LayerNorm(spec.width)
            self.feed_forward = nn.Sequential(nn.Linear(spec.width, spec.width * 4), nn.GELU(),
                                              nn.Linear(spec.width * 4, spec.width))

        def forward(self, inputs):
            batch, length, width = inputs.shape
            qkv = self.qkv(self.norm1(inputs)).reshape(batch, length, 3, spec.heads, width // spec.heads)
            query, key, value = qkv.permute(2, 0, 3, 1, 4).unbind(0)
            # Explicit FP32 attention avoids optional fused kernels and BF16 assumptions.
            attention = ((query @ key.transpose(-2, -1)) / (width // spec.heads) ** 0.5).softmax(-1)
            output = (attention @ value).transpose(1, 2).reshape(batch, length, width)
            residual = inputs + self.projection(output)
            return residual + self.feed_forward(self.norm2(residual))

    class TinyTransformer(nn.Module):
        def __init__(self):
            super().__init__()
            self.embedding = nn.Embedding(spec.vocabulary, spec.width)
            self.blocks = nn.ModuleList(Block() for _ in range(spec.layers))
            self.output = nn.Linear(spec.width, spec.vocabulary)

        def forward(self, tokens):
            hidden = self.embedding(tokens)
            for block in self.blocks:
                hidden = block(hidden)
            return self.output(hidden)

    return TinyTransformer()


def synthetic_batch(torch, spec: RunSpec, step: int, rank: int, world: int, device):
    generator = torch.Generator(device="cpu").manual_seed(spec.seed + step + 1)
    whole = torch.randint(spec.vocabulary, (spec.global_batch, spec.sequence_length + 1), generator=generator)
    selected = whole[local_batch_slice(spec.global_batch, rank, world)]
    return selected[:, :-1].to(device), selected[:, 1:].to(device)


def verify_shared_checkpoint_directory(path: Path, dist, rank: int, world: int) -> None:
    """Reject two same-named local directories before DCP claims a valid save."""
    path.mkdir(parents=True, exist_ok=True)
    token = [uuid.uuid4().hex if rank == 0 else None]
    dist.broadcast_object_list(token, src=0)
    probe = path / (".srs-shared-" + token[0])
    probe.mkdir(exist_ok=True)
    (probe / str(rank)).write_text(str(rank))
    dist.barrier()
    accessible = all((probe / str(index)).is_file() for index in range(world))
    all_accessible = [None] * world
    dist.all_gather_object(all_accessible, accessible)
    # Each participant removes only its own test file, including on separate disks.
    (probe / str(rank)).unlink(missing_ok=True)
    dist.barrier()
    try:
        probe.rmdir()
    except OSError:
        pass
    if not all(all_accessible):
        raise RuntimeError("FSDP2 checkpoint needs a genuinely shared directory on every rank")


def main() -> int:
    args = parser().parse_args()
    spec = RunSpec(**{key: getattr(args, key) for key in RunSpec().as_dict()})
    rank, world, local_rank = rank_environment(os.environ)
    spec.validate(world)
    if spec.phase == "resume" and args.resume_from is None:
        raise ValueError("resume requires --resume-from")
    if spec.phase == "train" and args.resume_from is not None:
        raise ValueError("--resume-from requires --phase resume")
    if args.checkpoint_dir and args.checkpoint_dir.exists():
        raise ValueError("New checkpoint directory must not exist; existing checkpoints are never overwritten")
    if spec.device == "cuda" and (not os.environ.get("SLURM_JOB_ID") or
                                  not os.environ.get("CUDA_VISIBLE_DEVICES")):
        raise RuntimeError("CUDA execution requires a Slurm job with assigned visible devices")
    import torch
    import torch.distributed as dist

    if torch.__version__.split("+")[0] != TORCH_VERSION:
        raise RuntimeError(f"Expected torch {TORCH_VERSION}; received {torch.__version__}")
    torch.set_num_threads(1)
    torch.manual_seed(spec.seed)
    distributed = world > 1
    device = torch.device("cpu")
    if spec.device == "cuda":
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1 or local_rank != 0:
            raise RuntimeError("v1 GPU jobs require one visible GPU and one process per node")
        device = torch.device("cuda", 0)
        torch.cuda.set_device(device)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
    backend = "nccl" if spec.device == "cuda" else "gloo"
    if distributed:
        dist.init_process_group(backend=backend, rank=rank, world_size=world,
                                timeout=timedelta(seconds=COMMUNICATION_TIMEOUT_SECONDS))
    try:
        return run(torch, dist, spec, args, rank, world, device, backend)
    finally:
        if distributed and dist.is_initialized():
            dist.destroy_process_group()


def run(torch, dist, spec, args, rank, world, device, backend) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / f"rank-{rank}.json"
    if report_path.exists():
        raise ValueError("Output already contains this rank; use a fresh run directory")
    model = make_model(torch, spec).to(device)
    full_parameter_count = sum(parameter.numel() for parameter in model.parameters())
    if spec.profile == "fsdp2":
        from torch.distributed.device_mesh import init_device_mesh
        from torch.distributed.fsdp import fully_shard

        mesh = init_device_mesh("cuda", (world,))
        for block in model.blocks:
            fully_shard(block, mesh=mesh, reshard_after_forward=True)
        fully_shard(model, mesh=mesh, reshard_after_forward=True)
    elif spec.profile == "ddp":
        model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[0] if device.type == "cuda" else None)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, foreach=False)
    identity = checkpoint_identity(spec, world)
    progress = {"step": torch.tensor(0, dtype=torch.int64)}
    if args.resume_from:
        saved_identity = json.loads((args.resume_from / "contract.json").read_text())
        if saved_identity != identity or not (args.resume_from / "COMPLETE").is_file():
            raise ValueError("Checkpoint is incomplete or has a different workload contract")
        if spec.profile == "fsdp2":
            verify_shared_checkpoint_directory(args.resume_from, dist, rank, world)
            import torch.distributed.checkpoint as dcp

            dcp.load({"training": checkpoint_state(model, optimizer), "progress": progress},
                     checkpoint_id=args.resume_from / "dcp")
        else:
            saved = torch.load(args.resume_from / "training.pt", map_location=device, weights_only=True)
            underlying = model.module if spec.profile == "ddp" else model
            underlying.load_state_dict(saved["model"])
            optimizer.load_state_dict(saved["optimizer"])
            progress["step"].fill_(saved["step"])
    start_step = int(progress["step"])
    durations, losses = [], []
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    for iteration in range(spec.warmup + spec.steps):
        step = int(progress["step"])
        inputs, targets = synthetic_batch(torch, spec, step, rank, world, device)
        if world > 1:
            dist.barrier()
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        started = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        logits = model(inputs)
        loss = torch.nn.functional.cross_entropy(logits.reshape(-1, spec.vocabulary), targets.reshape(-1))
        if not torch.isfinite(loss):
            raise RuntimeError("Non-finite synthetic training loss")
        loss.backward()
        optimizer.step()
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        elapsed = torch.tensor(time.perf_counter() - started, dtype=torch.float64, device=device)
        mean_loss = loss.detach().clone()
        if world > 1:
            dist.all_reduce(elapsed, op=dist.ReduceOp.MAX)
            dist.all_reduce(mean_loss, op=dist.ReduceOp.SUM)
            mean_loss /= world
        progress["step"] += 1
        if iteration >= spec.warmup:
            durations.append(float(elapsed))
            losses.append(float(mean_loss))
    # Snapshot training peaks before checkpoint/evaluation allocations.
    peak = {"allocated_bytes": torch.cuda.max_memory_allocated(device),
            "reserved_bytes": torch.cuda.max_memory_reserved(device)} if device.type == "cuda" else None
    parameter_details = []
    for name, parameter in model.named_parameters():
        local = parameter.to_local() if hasattr(parameter, "to_local") else parameter
        parameter_details.append({"name": name, "global_numel": parameter.numel(), "local_numel": local.numel(),
                                  "placements": [str(value) for value in getattr(parameter, "placements", [])]})
    if args.checkpoint_dir:
        if world > 1:
            verify_shared_checkpoint_directory(args.checkpoint_dir, dist, rank, world)
        if spec.profile == "fsdp2":
            import torch.distributed.checkpoint as dcp

            dcp.save({"training": checkpoint_state(model, optimizer), "progress": progress},
                     checkpoint_id=args.checkpoint_dir / "dcp")
        elif rank == 0:
            args.checkpoint_dir.mkdir(parents=True, exist_ok=world > 1)
            underlying = model.module if spec.profile == "ddp" else model
            torch.save({"model": underlying.state_dict(), "optimizer": optimizer.state_dict(),
                        "step": int(progress["step"])}, args.checkpoint_dir / "training.pt")
        if world > 1:
            dist.barrier()
        if rank == 0:
            atomic_json(args.checkpoint_dir / "contract.json", identity)
            (args.checkpoint_dir / "COMPLETE").write_text("Checkpoint save completed on all ranks.\n")
        if world > 1:
            dist.barrier()
    # All ranks participate because FSDP2 gathers parameters during forward.
    model.eval()
    with torch.no_grad():
        probe_inputs, _ = synthetic_batch(torch, spec, 100000, rank, world, device)
        probe = model(probe_inputs).detach().cpu()
    torch.save(probe, args.output_dir / f"rank-{rank}-probe.pt")
    result = {"schema_version": 1, "profile": spec.profile, "phase": spec.phase, "spec": spec.as_dict(),
              "rank": rank, "world_size": world, "backend": backend if world > 1 else None,
              "device": str(device), "host_architecture": platform.machine(), "torch_version": torch.__version__,
              "cuda_runtime": torch.version.cuda, "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
              "start_step": start_step, "completed_step": int(progress["step"]),
              "training_losses": losses, "step_seconds": durations,
              "median_step_seconds": statistics.median(durations),
              "samples_per_second": spec.global_batch / statistics.median(durations),
              "training_peak_memory": peak, "global_parameter_count": full_parameter_count,
              "local_parameter_count": sum(value["local_numel"] for value in parameter_details),
              "parameter_shards": parameter_details, "checkpoint_saved": bool(args.checkpoint_dir),
              "evidence_level": "remote-multi" if device.type == "cuda" and world > 1 else
                                "remote-single" if device.type == "cuda" else "local-cpu",
              "limits": ["Synthetic correctness workload, not a large-model capacity claim.",
                         "GPU memory is per device; capacities are not a single pooled allocation.",
                         "Throughput alone does not establish speedup without the matching single-GPU baseline."]}
    atomic_json(report_path, result)
    print(json.dumps({key: result[key] for key in ("rank", "profile", "completed_step", "median_step_seconds",
                                                 "local_parameter_count", "global_parameter_count")}))
    return 0


def checkpoint_state(model, optimizer):
    """DCP Stateful adapter: restores both sharded model and optimizer state."""
    from torch.distributed.checkpoint.state_dict import get_state_dict, set_state_dict

    class TrainingState:
        def state_dict(self):
            model_state, optimizer_state = get_state_dict(model, optimizer)
            return {"model": model_state, "optimizer": optimizer_state}

        def load_state_dict(self, state_dict):
            set_state_dict(model, optimizer, model_state_dict=state_dict["model"],
                           optim_state_dict=state_dict["optimizer"])

    return TrainingState()


if __name__ == "__main__":
    raise SystemExit(main())
