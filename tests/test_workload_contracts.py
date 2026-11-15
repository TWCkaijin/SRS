"""Offline contracts and, when explicitly provided, real CPU training recovery."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from infra.catalog import deployment_review  # noqa: E402
from workloads.contract import RunSpec, checkpoint_identity, local_batch_slice, rank_environment  # noqa: E402
from workloads.slurm.render import render_job  # noqa: E402


def test_distributed_samples_are_disjoint_and_cover_exact_global_batch():
    samples = list(range(12))
    rank_zero = samples[local_batch_slice(12, 0, 2)]
    rank_one = samples[local_batch_slice(12, 1, 2)]
    assert set(rank_zero).isdisjoint(rank_one)
    assert rank_zero + rank_one == samples
    with pytest.raises(ValueError):
        local_batch_slice(3, 0, 2)


def test_rank_context_does_not_invent_a_remote_rendezvous():
    with pytest.raises(ValueError, match="MASTER_ADDR"):
        rank_environment({"SLURM_NTASKS": "2", "SLURM_PROCID": "0"})
    assert rank_environment({"SLURM_NTASKS": "2", "SLURM_PROCID": "1", "SLURM_LOCALID": "0",
                             "MASTER_ADDR": "pilot225", "MASTER_PORT": "29500"}) == (1, 2, 0)


@pytest.mark.parametrize("spec,world", [(RunSpec(profile="fsdp2"), 2),
                                      (RunSpec(profile="ddp"), 1),
                                      (RunSpec(steps=101), 1),
                                      (RunSpec(profile="ddp", global_batch=3), 2)])
def test_invalid_or_unbounded_workloads_fail_before_importing_torch(spec, world):
    with pytest.raises(ValueError):
        spec.validate(world)


def test_checkpoint_contract_allows_continuation_but_not_different_model():
    original = checkpoint_identity(RunSpec(steps=5), 1)
    continuation = checkpoint_identity(RunSpec(phase="resume", steps=1), 1)
    assert original == continuation
    assert original != checkpoint_identity(RunSpec(width=64), 1)
    assert original != checkpoint_identity(RunSpec(), 2)


def job_arguments(tmp_path):
    return {"node_names": ["pilot225", "pilot250"], "project_dir": "/srv/srs/projects/synthetic",
            "python_executable": "/srv/srs/venv/bin/python", "output_dir": str(tmp_path / "run"),
            "interfaces": {"pilot225": "enp6s0", "pilot250": "enp3s0"}}


def test_two_gpu_job_is_bounded_and_interface_mapping_runs_inside_srun(tmp_path):
    script = render_job(RunSpec(profile="ddp", device="cuda"), **job_arguments(tmp_path))
    assert "#SBATCH --time=00:05:00" in script
    assert "#SBATCH --ntasks-per-node=1" in script
    assert "#SBATCH --gpus-per-task=1" in script
    assert "#SBATCH --nodelist=pilot225,pilot250" in script
    assert "srun --kill-on-bad-exit=1" in script
    assert "${SLURMD_NODENAME:?}" in script
    subprocess.run(["/bin/bash", "-n"], input=script, text=True, check=True)


def test_unapproved_node_or_shell_injection_cannot_change_sbatch_directives(tmp_path):
    values = job_arguments(tmp_path)
    values["node_names"] = ["pilot225\n#SBATCH --exclusive", "pilot250"]
    with pytest.raises(ValueError):
        render_job(RunSpec(profile="ddp", device="cuda"), **values)
    values = job_arguments(tmp_path)
    values["interfaces"] = None
    with pytest.raises(ValueError, match="approved LAN"):
        render_job(RunSpec(profile="ddp", device="cuda"), **values)


def test_project_path_is_quoted_as_data_not_shell_program(tmp_path):
    values = job_arguments(tmp_path)
    values["project_dir"] = "/srv/research/project'; touch /tmp/unintended; '"
    script = render_job(RunSpec(profile="ddp", device="cuda"), **values)
    # Parse the cd line rather than executing any submitted workload.
    import shlex

    cd_line = next(line for line in script.splitlines() if line.startswith("cd "))
    assert shlex.split(cd_line) == ["cd", values["project_dir"]]
    subprocess.run(["/bin/bash", "-n"], input=script, text=True, check=True)


def test_deployment_catalog_never_treats_unverified_files_as_approved():
    first = deployment_review("slurm")
    second = deployment_review("slurm")
    assert first["catalog_hash"] == second["catalog_hash"]
    assert first["automatic_execution"] is False
    assert first["issues"]
    assert all(change["diff_verified"] is False for change in first["file_changes"])
    assert all(step["unresolved_template"] for step in first["steps"])
    assert all(len(file["sha256"]) == 64 for file in first["files"])


def test_review_cli_prints_blocked_json_without_contacting_hosts():
    result = subprocess.run([sys.executable, str(ROOT / "infra/plan_deployment.py"), "--component", "platform"],
                            capture_output=True, text=True, check=True, cwd=ROOT)
    document = json.loads(result.stdout)
    assert document["status"] == "blocked_template"
    assert "host_id" not in document


def test_real_cpu_checkpoint_resume_matches_uninterrupted_training(tmp_path):
    executable = os.environ.get("SRS_TEST_TORCH_PYTHON")
    if not executable:
        pytest.skip("Set SRS_TEST_TORCH_PYTHON to an isolated Python with torch 2.14.1; not a fake training test")
    common = [executable, "-m", "workloads.train", "--profile", "cpu", "--width", "32", "--layers", "1",
              "--sequence-length", "8", "--vocabulary", "32", "--global-batch", "2"]
    runs = [(["--steps", "3", "--output-dir", str(tmp_path / "reference")]),
            (["--steps", "2", "--output-dir", str(tmp_path / "first"), "--checkpoint-dir", str(tmp_path / "checkpoint")]),
            (["--steps", "1", "--phase", "resume", "--resume-from", str(tmp_path / "checkpoint"),
              "--output-dir", str(tmp_path / "resumed")])]
    for arguments in runs:
        subprocess.run(common + arguments, cwd=ROOT, check=True, capture_output=True, text=True, timeout=60)
    result = subprocess.run([executable, "-m", "workloads.compare_resume", str(tmp_path / "reference"),
                             str(tmp_path / "resumed")], cwd=ROOT, check=True, capture_output=True, text=True, timeout=30)
    assert json.loads(result.stdout)["passed"]
