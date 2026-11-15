"""Pure-Python workload validation shared by the runner and local tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

TORCH_VERSION = "2.14.1"
COMMUNICATION_TIMEOUT_SECONDS = 120
JOB_TIME_LIMIT_SECONDS = 300


@dataclass(frozen=True)
class RunSpec:
    profile: str = "cpu"
    device: str = "cpu"
    steps: int = 5
    warmup: int = 0
    global_batch: int = 4
    sequence_length: int = 32
    width: int = 128
    layers: int = 2
    heads: int = 4
    vocabulary: int = 256
    seed: int = 20261107
    phase: str = "train"

    def validate(self, world_size: int) -> None:
        if self.profile not in {"cpu", "single", "ddp", "fsdp2"}:
            raise ValueError("Unknown workload profile")
        if self.device not in {"cpu", "cuda"} or self.phase not in {"train", "resume"}:
            raise ValueError("Invalid device or phase")
        if self.profile == "cpu" and self.device != "cpu":
            raise ValueError("The cpu profile requires CPU execution")
        if self.profile in {"single", "fsdp2"} and self.device != "cuda":
            raise ValueError("single and fsdp2 require a real CUDA GPU")
        if world_size != (2 if self.profile in {"ddp", "fsdp2"} else 1):
            raise ValueError("v1 requires exactly two ranks for ddp/fsdp2, otherwise one")
        if not 1 <= self.steps <= 100 or not 0 <= self.warmup <= 10:
            raise ValueError("steps must be 1..100 and warmup 0..10")
        if not 1 <= self.global_batch <= 32 or self.global_batch % world_size:
            raise ValueError("global_batch must be 1..32 and divisible by world size")
        if not 1 <= self.sequence_length <= 128:
            raise ValueError("sequence_length must be 1..128")
        if self.width not in {32, 64, 128, 256, 512} or self.heads not in {1, 2, 4, 8}:
            raise ValueError("Unsupported synthetic model dimensions")
        if self.width % self.heads or not 1 <= self.layers <= 4 or not 16 <= self.vocabulary <= 1024:
            raise ValueError("Invalid model dimensions")

    def as_dict(self) -> dict:
        return asdict(self)


def rank_environment(environment: Mapping[str, str]) -> tuple[int, int, int]:
    """Support torchrun and srun without guessing a multi-node rendezvous address."""
    rank = int(environment.get("RANK", environment.get("SLURM_PROCID", "0")))
    world = int(environment.get("WORLD_SIZE", environment.get("SLURM_NTASKS", "1")))
    local = int(environment.get("LOCAL_RANK", environment.get("SLURM_LOCALID", "0")))
    if world < 1 or rank < 0 or rank >= world or local < 0:
        raise ValueError("Invalid distributed rank environment")
    if world > 1:
        if not environment.get("MASTER_ADDR"):
            raise ValueError("MASTER_ADDR must identify a reachable allocated node")
        port = int(environment.get("MASTER_PORT", "0"))
        if not 1024 <= port <= 65535:
            raise ValueError("MASTER_PORT must be an explicit unprivileged port")
    return rank, world, local


def local_batch_slice(global_batch: int, rank: int, world_size: int) -> slice:
    if global_batch < world_size or global_batch % world_size or not 0 <= rank < world_size:
        raise ValueError("Cannot divide batch into equal, disjoint rank samples")
    per_rank = global_batch // world_size
    return slice(rank * per_rank, (rank + 1) * per_rank)


def checkpoint_identity(spec: RunSpec, world_size: int) -> dict:
    """Reject accidental resume with different math; steps and warmup may change."""
    keys = ("profile", "device", "global_batch", "sequence_length", "width", "layers", "heads", "vocabulary", "seed")
    values = spec.as_dict()
    return {"schema_version": 1, "torch_version": TORCH_VERSION, "world_size": world_size,
            "model": {key: values[key] for key in keys}}
