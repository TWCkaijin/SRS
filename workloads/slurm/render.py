"""Produce the exact bounded sbatch payload for an approval plan, never submit it."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from workloads.contract import RunSpec

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


def _path(value: str) -> str:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(ord(character) < 32 for character in value):
        raise ValueError("Use an absolute path without traversal or control characters")
    return str(path)


def render_job(
    spec: RunSpec,
    *,
    node_names: list[str],
    project_dir: str,
    python_executable: str,
    output_dir: str,
    partition: str = "srs-pilot",
    cpus_per_task: int = 1,
    memory_mb: int = 2048,
    master_port: int = 29500,
    interfaces: dict[str, str] | None = None,
    checkpoint_dir: str | None = None,
    resume_from: str | None = None,
) -> str:
    world = 2 if spec.profile in {"ddp", "fsdp2"} else 1
    spec.validate(world)
    if len(node_names) != world or len(set(node_names)) != world:
        raise ValueError("Provide exactly one distinct allocated node per rank")
    if any(not TOKEN.fullmatch(value) for value in [*node_names, partition]):
        raise ValueError("Invalid Slurm node or partition name")
    if not 1 <= cpus_per_task <= 4 or not 512 <= memory_mb <= 8192:
        raise ValueError("Pilot jobs are bounded to 1..4 CPUs and 512..8192 MiB per node")
    if not 1024 <= master_port <= 65535:
        raise ValueError("Invalid rendezvous port")
    if spec.device == "cuda" and world > 1:
        if not interfaces or set(interfaces) != set(node_names):
            raise ValueError("Cross-node CUDA requires an approved LAN interface per node")
        if any(not TOKEN.fullmatch(value) for value in interfaces.values()):
            raise ValueError("Invalid approved network interface")
    project_dir, python_executable, output_dir = map(_path, (project_dir, python_executable, output_dir))
    # Slurm parses directives itself; shlex quoting is not a safe escape for every parser.
    if any(character.isspace() or character in "'\"#%" for character in output_dir):
        raise ValueError("Slurm output directory must not contain whitespace, quotes, # or %")
    directives = [f"#SBATCH --job-name=srs-{spec.profile}", f"#SBATCH --partition={partition}",
                  f"#SBATCH --nodes={world}", f"#SBATCH --ntasks={world}", "#SBATCH --ntasks-per-node=1",
                  f"#SBATCH --nodelist={','.join(node_names)}", f"#SBATCH --cpus-per-task={cpus_per_task}",
                  f"#SBATCH --mem={memory_mb}M", "#SBATCH --time=00:05:00",
                  f"#SBATCH --output={output_dir}/slurm-%j.out", f"#SBATCH --error={output_dir}/slurm-%j.err"]
    if spec.device == "cuda":
        directives.append("#SBATCH --gpus-per-task=1")
    argv = [python_executable, "-m", "workloads.train"]
    for key, value in spec.as_dict().items():
        argv.extend(["--" + key.replace("_", "-"), str(value)])
    argv.extend(["--output-dir", output_dir])
    if checkpoint_dir:
        argv.extend(["--checkpoint-dir", _path(checkpoint_dir)])
    if resume_from:
        argv.extend(["--resume-from", _path(resume_from)])
    if (spec.phase == "resume") != bool(resume_from):
        raise ValueError("resume phase and resume_from must be provided together")
    launch = "exec " + shlex.join(argv)
    if spec.device == "cuda" and world > 1:
        cases = [f"{node}) export NCCL_SOCKET_IFNAME={shlex.quote('=' + interfaces[node])} ;;"
                 for node in node_names]
        launch = "case \"${SLURMD_NODENAME:?}\" in\n" + "\n".join(cases) + "\n*) exit 64 ;;\nesac\n" + launch
    rendezvous = ""
    if world > 1:
        # Node names must resolve over the approved private network on both workers.
        rendezvous = f"export MASTER_ADDR={shlex.quote(node_names[0])}\nexport MASTER_PORT={master_port}"
    text = Path(__file__).with_name("job.sbatch.in").read_text()
    replacements = {"@@SBATCH_DIRECTIVES@@": "\n".join(directives), "@@RENDEZVOUS@@": rendezvous,
                    "@@PROJECT_DIRECTORY@@": shlex.quote(project_dir),
                    "@@SRUN_COMMAND@@": "srun --kill-on-bad-exit=1 --export=ALL --cpu-bind=cores /bin/bash -c " + shlex.quote(launch)}
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text
