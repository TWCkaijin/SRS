"""Job payload safety and evidence gates; no SSH, Slurm or socket calls."""

import copy
import shlex
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from srs.config import Settings
from srs.models import Draft, Execution, Node, Project, now
from srs.planner import build_job_steps, content_hash, job_script, node_binding


def fixtures():
    project = Project(id="project", name="Synthetic", path="/srv/srs/projects/synthetic",
                      entrypoint="workloads/train.py", container="/srv/srs/images/synthetic.sif",
                      revision="a" * 40, owner="admin", modes=["cpu", "single", "ddp", "fsdp2"])
    nodes = [Node(id=str(number), alias=f"AI_Lab_{number}", display_name=str(number), hostname=f"192.0.2.{index}",
                  port=22, ssh_username="operator", enabled=True, inventory_revision=1,
                  host_key_sha256="SHA256:test", hardware={}, facts={"projects": {project.id: {
                      "container_sha256": "b" * 64, "revision": {"stdout": project.revision}}}})
             for index, number in enumerate((225, 250), 1)]
    return project, nodes


class FakeDatabase:
    def __init__(self, project, nodes, execution=None):
        self.data = {(Project, project.id): project, **{(Node, node.id): node for node in nodes}}
        if execution:
            self.data[(Execution, execution.id)] = execution

    def get(self, model, identifier):
        return self.data.get((model, identifier))


@pytest.mark.parametrize("arguments", [
    ["--output-dir", "/tmp/other"], ["--profile=cpu"], ["--device", "cpu"],
    ["--resume-from", "/srv/srs/results/other/checkpoint"], ["--steps", "5", "--steps", "7"],
    ["--steps", "1000"], ["--steps", "1;touch /tmp/unwanted"], ["--global-batch", "3"],
    ["--steps"], ["--width", "33"], ["--layers", "-1"],
])
def test_training_args_cannot_override_execution_contract(arguments):
    project, nodes = fixtures()
    with pytest.raises(HTTPException) as error:
        job_script(project, {"profile": "ddp", "args": arguments}, nodes, "b" * 64)
    assert error.value.status_code == 422


def test_payload_preserves_rank_mask_and_fresh_checkpoint_directory():
    project, nodes = fixtures()
    script = job_script(project, {"profile": "ddp", "args": ["--steps=3", "--global-batch", "4"]}, nodes, "b" * 64)
    subprocess.run(["/bin/bash", "-n"], input=script, text=True, check=True)
    mkdir_lines = [line for line in script.splitlines() if line.startswith("mkdir")]
    assert not any("checkpoint" in line for line in mkdir_lines)
    assert "--kill-on-bad-exit=1" in script
    assert "APPTAINERENV_RANK" in script and "SLURM_PROCID" in script
    assert "APPTAINERENV_CUDA_VISIBLE_DEVICES" in script
    assert "--cleanenv" in script
    assert "--steps 3" in script


def test_project_data_cannot_turn_into_executable_shell():
    project, nodes = fixtures()
    project.path = "/srv/srs/projects/name'; touch /tmp/unwanted; '"
    script = job_script(project, {"profile": "cpu"}, [nodes[0]], "b" * 64)
    line = next(value for value in script.splitlines() if value.startswith("git -C "))
    tokens = shlex.split(line)
    assert tokens[2] == project.path
    assert tokens[3] == "archive"
    subprocess.run(["/bin/bash", "-n"], input=script, text=True, check=True)


def build(monkeypatch, mode, project, nodes, execution=None):
    monkeypatch.setattr("srs.planner.add_manifest", lambda *args: None)
    draft = Draft(id="draft", title="Two rank run", operation="submit_job", host_ids=["225", "250"],
                  parameters={"project_id": project.id, "profile": "ddp", "args": []}, owner="admin")
    settings = Settings(mode=mode)
    return build_job_steps(FakeDatabase(project, nodes, execution), draft, nodes, settings, "plan-test")


def valid_evidence(project, nodes):
    document = {"inventory": [node_binding(node) for node in nodes], "purpose": "approved pilot connectivity validation"}
    execution = SimpleNamespace(id="proof-execution", status="succeeded", mode="remote-slurm", owner="admin",
                                plan_document=document, approved_hash=content_hash(document))
    for index, node in enumerate(nodes):
        node.facts["cluster"] = {"execution_id": execution.id, "validated_at": now(),
                                 "shared_storage": {"verified": True, "root": "/srv/srs",
                                                    "peer_ids": ["225", "250"], "probe_id": "shared-round"},
                                 "network": {"verified": True, "address": f"10.88.0.{index + 1}",
                                             "interface": f"enp{index + 1}s0", "peer_ids": ["225", "250"],
                                             "probe_id": "network-round"}}
    return execution


def test_remote_multinode_requires_real_shared_storage_and_network_evidence(monkeypatch):
    project, nodes = fixtures()
    _, issues = build(monkeypatch, "remote-slurm", project, nodes)
    assert any("真實共享" in issue for issue in issues)
    assert any("訓練網路" in issue for issue in issues)
    assert any("已批准執行" in issue for issue in issues)


def test_demo_remains_usable_without_claiming_remote_validation(monkeypatch):
    project, nodes = fixtures()
    _, issues = build(monkeypatch, "demo", project, nodes)
    assert not issues


def test_bound_current_evidence_selects_each_nodes_actual_interface(monkeypatch):
    project, nodes = fixtures()
    execution = valid_evidence(project, nodes)
    steps, issues = build(monkeypatch, "remote-slurm", project, nodes, execution)
    assert not issues
    script = steps[-1]["script"]
    assert "MASTER_ADDR=10.88.0.1" in script
    assert "enp1s0" in script and "enp2s0" in script
    assert "wg0" not in script


@pytest.mark.parametrize("change", ["stale", "different-round", "changed-key", "unapproved", "loopback"])
def test_stale_or_unbound_cluster_claim_cannot_authorize_training(monkeypatch, change):
    project, nodes = fixtures()
    execution = valid_evidence(project, nodes)
    if change == "stale":
        nodes[0].facts["cluster"]["validated_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    elif change == "different-round":
        nodes[1].facts["cluster"]["shared_storage"]["probe_id"] = "different"
    elif change == "changed-key":
        nodes[0].host_key_sha256 = "SHA256:changed"
    elif change == "unapproved":
        execution.plan_document = copy.deepcopy(execution.plan_document)
        execution.plan_document["purpose"] = "tampered"
    elif change == "loopback":
        nodes[0].facts["cluster"]["network"]["address"] = "127.0.0.1"
    _, issues = build(monkeypatch, "remote-slurm", project, nodes, execution)
    assert issues
