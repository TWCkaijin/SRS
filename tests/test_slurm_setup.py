import json
import sys
import time
from types import SimpleNamespace

import pytest
from srs import slurm_setup as setup
from srs.models import SSHHost
from srs.slurm_bootstrap import BOOTSTRAP, EXTRA_PROBE, PACKAGE_PLAN
from test_ssh_hosts import platform as platform
from test_ssh_hosts import save


def facts(hostname="node1"):
    return {
        "hostname": hostname,
        "system": "Linux",
        "cpus": 8,
        "memory_mb": 16384,
        "gpus": [{"index": 0, "uuid": "GPU-fixture1", "name": "GPU"}],
        "os": {"ID": "ubuntu", "VERSION_ID": "24.04"},
        "systemd": True,
        "cgroup2": True,
        "existing_config": False,
        "munge_key_exists": False,
        "policy_exists": False,
        "services": {},
        "accounts": {"researcher": {"uid": 1000, "gid": 1000}},
        "reserved_ids": {},
        "observed_time": time.time(),
        "package_error": None,
        "packages": [
            {
                "name": "slurmd",
                "version": "23.11.4",
                "sha256": "a" * 64,
                "uri": "http://archive.ubuntu.com/test.deb",
                "size": 10,
            }
        ],
    }


def hosts(platform):
    client, app, _ = platform
    first = save(client, hostname="192.0.2.1", display_name="node1")
    second = save(client, hostname="192.0.2.2", display_name="node2")
    with app.state.factory.begin() as db:
        for h in (first, second):
            db.get(SSHHost, h["node_id"]).host_key_sha256 = "SHA256:" + "A" * 43
    return first, second


def payload(first, second):
    return {
        "controller_id": first["node_id"],
        "training_user": "researcher",
        "cluster": "lab",
        "partition": "gpu",
        "nodes": [
            {
                "host_id": host["node_id"],
                "name": "node" + str(i),
                "address": host["hostname"],
                "cpus": 8,
                "memory_mb": 15000,
                "gpu_indices": [0] if i == 1 else [],
            }
            for i, host in enumerate((first, second), 1)
        ],
    }


class Connector:
    def __init__(self, fail=None, blocked=False):
        self.calls = []
        self.fail = fail
        self.blocked = blocked

    async def __call__(self, host, settings):
        outer = self
        address = host["hostname"]

        class Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def run(self, command, **kwargs):
                outer.calls.append((address, command, kwargs))
                if "sys.argv[1]" in command:
                    data = facts("node1" if address.endswith(".1") else "node2")
                    if outer.blocked:
                        data["existing_config"] = True
                    return SimpleNamespace(stdout=json.dumps(data))
                if "/usr/bin/id" in command:
                    if outer.fail == "privilege":
                        raise RuntimeError("secret-sudo-error")
                    return SimpleNamespace(stdout="0\n")
                if command.startswith("sinfo"):
                    return SimpleNamespace(stdout="node1|idle\nnode2|idle\n")
                body = json.loads(kwargs["input"].splitlines()[-1])
                if outer.fail == (address, body["phase"]):
                    raise RuntimeError("secret-sudo-error")
                return SimpleNamespace(
                    stdout=json.dumps(
                        {
                            "phase": {
                                "install": "installed",
                                "configure": "configured",
                                "activate": "activated",
                            }[body["phase"]]
                        }
                    )
                )

        return Connection()


def wait_done(client, token):
    for _ in range(100):
        response = client.get("/api/v1/slurm/status/" + token)
        assert response.status_code == 200
        if response.json()["status"] != "running":
            return response.json()
        time.sleep(0.01)
    pytest.fail("batch did not finish")


def test_render_binds_node_addresses_and_only_selected_gpu():
    a = {"node_id": "a", "hostname": "192.0.2.1"}
    b = {"node_id": "b", "hostname": "192.0.2.2"}
    body = setup.SlurmPreview.model_validate(payload(a, b))
    result = setup.render_configuration(body, {"a": facts("physical1"), "b": facts("physical2")})
    conf = result["a"]["/etc/slurm/slurm.conf"]
    assert conf == result["b"]["/etc/slurm/slurm.conf"]
    assert "SlurmctldHost=physical1(192.0.2.1)" in conf
    assert "NodeName=node1 NodeHostname=physical1 NodeAddr=192.0.2.1" in conf
    assert "/dev/nvidia0" in result["a"]["/etc/slurm/gres.conf"]
    assert "/dev/nvidia" not in result["b"]["/etc/slurm/gres.conf"]
    assert "-N node2" in result["b"]["/etc/systemd/system/slurmd.service.d/srs.conf"]
    body.nodes[0].memory_mb = 16384
    with pytest.raises(ValueError, match="512"):
        setup.render_configuration(body, {"a": facts(), "b": facts("node2")})


def test_slurm_plan_exact_phases_and_no_secrets(platform, monkeypatch):
    client, _, _ = platform
    first, second = hosts(platform)
    connector = Connector()
    monkeypatch.setattr(setup, "connect", connector)
    result = client.post("/api/v1/slurm/preview", json=payload(first, second))
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["ready"]
    assert len(connector.calls) == 2
    request = {
        "token": plan["token"],
        "content_hash": plan["content_hash"],
        "sudo_passwords": {first["node_id"]: "private-sudo", second["node_id"]: "private-sudo"},
    }
    assert client.post("/api/v1/slurm/apply", json={**request, "content_hash": "0" * 64}).status_code == 409
    response = client.post("/api/v1/slurm/apply", json=request)
    assert response.status_code == 202, response.text
    state = wait_done(client, plan["token"])
    assert state["status"] == "ready" and state["verification"]["ready"]
    calls = [
        (addr, json.loads(kw["input"].splitlines()[-1]))
        for addr, cmd, kw in connector.calls
        if "input" in kw and "json.loads(sys.stdin" in cmd
    ]
    assert [p["phase"] for _, p in calls] == [
        "install",
        "install",
        "configure",
        "configure",
        "activate",
        "activate",
    ]
    assert len({p["munge_key"] for _, p in calls}) == 1
    assert all("private-sudo" not in cmd for _, cmd, _ in connector.calls)
    assert "private-sudo" not in json.dumps(state) and calls[0][1]["munge_key"] not in json.dumps(plan)
    assert client.post("/api/v1/slurm/apply", json=request).status_code == 409


@pytest.mark.parametrize("failure", ["privilege", ("192.0.2.2", "install")])
def test_failure_stops_before_later_phases(platform, monkeypatch, failure):
    client, _, _ = platform
    first, second = hosts(platform)
    connector = Connector(fail=failure)
    monkeypatch.setattr(setup, "connect", connector)
    plan = client.post("/api/v1/slurm/preview", json=payload(first, second)).json()
    assert (
        client.post(
            "/api/v1/slurm/apply", json={"token": plan["token"], "content_hash": plan["content_hash"]}
        ).status_code
        == 202
    )
    state = wait_done(client, plan["token"])
    assert state["status"] == "unconfirmed"
    assert "secret-sudo-error" not in json.dumps(state)
    commands = [cmd for _, cmd, _ in connector.calls]
    if failure == "privilege":
        assert not any("json.loads(sys.stdin" in cmd for cmd in commands)
    phases = [
        json.loads(kw["input"].splitlines()[-1])["phase"]
        for _, cmd, kw in connector.calls
        if "json.loads(sys.stdin" in cmd
    ]
    assert "configure" not in phases and "activate" not in phases


def test_existing_cluster_permissions_and_revision_block(platform, monkeypatch):
    client, app, _ = platform
    first, second = hosts(platform)
    connector = Connector(blocked=True)
    monkeypatch.setattr(setup, "connect", connector)
    plan = client.post("/api/v1/slurm/preview", json=payload(first, second)).json()
    assert not plan["ready"]
    assert (
        client.post(
            "/api/v1/slurm/apply", json={"token": plan["token"], "content_hash": plan["content_hash"]}
        ).status_code
        == 409
    )
    connector.blocked = False
    plan = client.post("/api/v1/slurm/preview", json=payload(first, second)).json()
    with app.state.factory.begin() as db:
        db.get(SSHHost, first["node_id"]).revision += 1
    assert (
        client.post(
            "/api/v1/slurm/apply", json={"token": plan["token"], "content_hash": plan["content_hash"]}
        ).status_code
        == 409
    )
    app.state.settings.permissions = "read,hosts.terminal"
    assert (
        client.post(
            "/api/v1/slurm/apply", json={"token": plan["token"], "content_hash": plan["content_hash"]}
        ).status_code
        == 403
    )
    client.headers.pop("X-CSRF-Token")
    assert (
        client.post(
            "/api/v1/slurm/inspect", json={"host_ids": [first["node_id"]], "training_user": "researcher"}
        ).status_code
        == 403
    )


def test_configuration_rejects_newline_and_duplicate_node_names():
    a = {"node_id": "a", "hostname": "192.0.2.1"}
    b = {"node_id": "b", "hostname": "192.0.2.2"}
    p = payload(a, b)
    p["nodes"][0]["address"] = "192.0.2.1\nProlog=/bin/true"
    with pytest.raises(ValueError):
        setup.SlurmPreview.model_validate(p)
    p = payload(a, b)
    p["nodes"][1]["name"] = "node1"
    with pytest.raises(ValueError):
        setup.SlurmPreview.model_validate(p)
    p = payload(a, b)
    p["nodes"][0]["name"] = "node1\n"
    with pytest.raises(ValueError):
        setup.SlurmPreview.model_validate(p)


def test_packages_require_trusted_ubuntu_sha_and_no_upgrades(monkeypatch):
    pkg = SimpleNamespace(
        name="slurmd",
        is_installed=False,
        marked_delete=False,
        candidate=SimpleNamespace(
            origins=[SimpleNamespace(trusted=True, origin="Ubuntu")],
            record={"SHA256": "a" * 64},
            version="23.11.4",
            size=10,
            uri="http://archive.ubuntu.com/test.deb",
        ),
        mark_install=lambda **kw: None,
    )

    class Cache(dict):
        def __init__(self):
            super().__init__({name: pkg for name in ("slurmctld", "slurmd", "slurm-client", "munge")})

        def get_changes(self):
            return [pkg]

    monkeypatch.setitem(sys.modules, "apt", SimpleNamespace(Cache=Cache))
    namespace = {}
    exec(PACKAGE_PLAN, namespace)
    assert namespace["package_plan"]()[0][0]["sha256"] == "a" * 64
    pkg.is_installed = True
    with pytest.raises(RuntimeError, match="existing"):
        namespace["package_plan"]()
    pkg.is_installed = False
    pkg.candidate.origins[0].trusted = False
    with pytest.raises(RuntimeError, match="untrusted"):
        namespace["package_plan"]()
    pkg.candidate.origins[0].trusted = True
    pkg.candidate.record["SHA256"] = ""
    with pytest.raises(RuntimeError, match="digest"):
        namespace["package_plan"]()


def test_fixed_remote_programs_compile():
    compile(BOOTSTRAP, "bootstrap", "exec")
    compile(setup.PROBE.replace("print(json.dumps(r))", "") + PACKAGE_PLAN + EXTRA_PROBE, "probe", "exec")
