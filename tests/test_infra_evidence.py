"""Synthetic evidence validation only; these tests never contact or modify hosts."""

import copy
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from srs.cluster_probe import cluster_probe_script, parse_cluster_evidence, validate_probe_spec

from infra.catalog import COMPONENTS, ROOT, deployment_review, render_deployment
from infra.evidence import deployment_probe_script, parse_deployment_evidence, validate_deployment_spec


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def deployment_fixture(component="platform"):
    node = {"id": "225", "inventory_revision": "fixture-r1", "host_key_sha256": "SHA256:fixture",
            "ssh_username": "fixture", "hardware": {"gpus": []}}
    evidence = {"schema_version": 1, "component": component, "execution_id": "fixture-execution",
                "observed_at": datetime.now(UTC).isoformat(),
                "host": {key: node[key] for key in ("id", "inventory_revision", "host_key_sha256")},
                "os": {"id": "ubuntu", "version_id": "24.04", "architecture": "amd64"},
                "requirements": {key: {"status": "verified", "execution_id": "fixture-execution",
                                        "detail": "SYNTHETIC isolated contract fixture; no real host evidence"}
                                 for key in deployment_review(component)["required_evidence"]},
                "packages": [], "files": {}, "staged_files": {}}
    if component == "platform":
        for source, destination in COMPONENTS[component]["destinations"].items():
            text = (ROOT / source).read_text()
            evidence["files"][destination] = {"exists": True, "content": "# previous unit\n", "sha256": digest("# previous unit\n")}
            evidence["staged_files"][destination] = {"path": "/srv/srs/staging/" + source.rsplit("/", 1)[-1],
                                                       "sha256": digest(text)}
    if component == "apptainer":
        evidence["packages"] = [{"path": "/srv/srs/staging/apptainer_1.5.5_amd64.deb", "sha256": "a" * 64,
                                 "name": "apptainer", "version": "1.5.5", "architecture": "amd64",
                                 "source_url": "https://example.invalid/audited-package.deb", "source_sha256": "a" * 64,
                                 "non_setuid": True}]
    return node, evidence


def test_complete_platform_evidence_renders_exact_executable_argv_and_diff():
    node, evidence = deployment_fixture()
    review = render_deployment("platform", node, evidence)
    assert review["status"] == "ready", review["issues"]
    assert len(review["steps"]) == 3
    assert review["automatic_execution"] is False
    for step in review["steps"]:
        assert step["argv"] == ["/bin/sh", "-c", step["script"]]
        assert "@@" not in step["script"]
        assert "--credentials-file /etc/server-remote-system/hosts.env" in next(
            change["after"] for change in review["file_changes"] if change["destination"].endswith("srs-worker.service"))
        assert step["file_change"]["diff"].startswith("--- ")
        assert step["file_change"]["backup_path"].endswith(".srs-backup-" + digest("# previous unit\n")[:16])
        subprocess.run(["/bin/sh", "-n"], input=step["script"], text=True, check=True)


@pytest.mark.parametrize("mutation", [
    lambda e: e["host"].update(inventory_revision="different"),
    lambda e: e.update(observed_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat()),
    lambda e: e.update(observed_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat()),
    lambda e: e["requirements"].clear(),
    lambda e: next(iter(e["files"].values())).update(content="tampered"),
    lambda e: next(iter(e["staged_files"].values())).update(sha256="0" * 64),
    lambda e: next(iter(e["staged_files"].values())).update(path="/srv/srs/staging/x'; touch /tmp/unwanted"),
])
def test_deployment_cannot_be_ready_with_stale_or_unverified_evidence(mutation):
    node, evidence = deployment_fixture()
    mutation(evidence)
    review = render_deployment("platform", node, evidence)
    assert review["status"] == "blocked"
    assert review["issues"]


@pytest.mark.parametrize("changes", [
    {"sha256": "b" * 64}, {"version": "1.5.4"}, {"architecture": "arm64"},
    {"non_setuid": False}, {"source_url": "http://example.invalid/package.deb"},
    {"name": "nvidia-driver-600"}, {"path": "/tmp/package.deb"},
])
def test_wrong_binary_provenance_version_or_scope_blocks_package(changes):
    node, evidence = deployment_fixture("apptainer")
    assert render_deployment("apptainer", node, evidence)["status"] == "ready"
    evidence["packages"][0].update(changes)
    assert render_deployment("apptainer", node, evidence)["status"] == "blocked"


def cluster_fixture():
    probe_id = "a" * 32
    witness = {"probe_id": probe_id, "peer_ids": ["225", "250"], "source": "10.10.0.1:/srv/srs", "nonce": "b" * 64}
    spec = {"schema_version": 1, "probe_id": probe_id,
            "storage": {"root": "/srv/srs", "source": witness["source"],
                        "witness_path": f"/srv/srs/.srs-probes/{probe_id}.json", "witness_sha256": digest(json.dumps(witness))},
            "peers": [{"id": "225", "address": "10.10.0.225", "interface": "enp0s1", "port": 6818},
                      {"id": "250", "address": "10.10.0.250", "interface": "enp0s2", "port": 6818}]}
    spec_sha = digest(json.dumps(validate_probe_spec(spec), sort_keys=True, separators=(",", ":")))
    observations = {}
    for mine in spec["peers"]:
        peer = next(item for item in spec["peers"] if item != mine)
        observations[mine["id"]] = {
            "kind": "cluster_probe", "schema_version": 1, "node_id": mine["id"], "probe_id": probe_id, "spec_sha256": spec_sha,
            "mount": {"exit_code": 0, "stdout": json.dumps({"filesystems": [{"target": "/srv/srs", "source": witness["source"],
                                                                              "fstype": "nfs4", "options": "rw,relatime"}]})},
            "witness": {"sha256": spec["storage"]["witness_sha256"], "content": witness},
            "addresses": {"exit_code": 0, "stdout": json.dumps([{"ifname": mine["interface"],
                                                                  "addr_info": [{"local": mine["address"], "family": "inet"}]}])},
            "connections": {peer["id"]: {"address": peer["address"], "port": 6818, "peer_address": peer["address"],
                                         "local_address": mine["address"], "connected": True,
                                         "route": {"exit_code": 0, "stdout": json.dumps([{"dev": mine["interface"], "prefsrc": mine["address"]}])}}},
        }
    return spec, observations


def parse(observations, spec):
    return parse_cluster_evidence(observations, spec, execution_id="fixture-execution", validated_at=datetime.now(UTC).isoformat())


def test_same_approved_probe_has_narrow_honest_evidence_scope():
    spec, observations = cluster_fixture()
    facts = parse(observations, spec)
    assert set(facts) == {"225", "250"}
    for node_id, fact in facts.items():
        assert fact["shared_storage"]["verified"] is True
        assert fact["shared_storage"]["write_visibility_tested"] is False
        assert fact["network"]["nccl_collective_tested"] is False
        assert fact["network"]["tested_tcp_ports"] == [6818]
        script = cluster_probe_script(spec, node_id)
        compile(script, "<approved-cluster-probe>", "exec")
        assert "os.O_RDONLY" in script and "os.O_CREAT" not in script


def test_same_named_local_directories_do_not_prove_shared_storage():
    spec, observations = cluster_fixture()
    observation = observations["250"]
    mount = json.loads(observation["mount"]["stdout"])
    mount["filesystems"][0].update(fstype="ext4", source="/dev/sda1")
    observation["mount"]["stdout"] = json.dumps(mount)
    with pytest.raises(ValueError, match="NFS"):
        parse(observations, spec)


@pytest.mark.parametrize("mutation", [
    lambda o: o.pop("250"),
    lambda o: o["250"].update(probe_id="c" * 32),
    lambda o: o["250"]["witness"].update(sha256="d" * 64),
    lambda o: o["250"]["connections"]["225"].update(connected=False),
    lambda o: o["250"]["connections"]["225"].update(local_address="10.10.0.251"),
    lambda o: o["250"]["connections"]["225"]["route"].update(stdout='[{"dev":"wrong-interface"}]'),
])
def test_cluster_requires_complete_matching_bilateral_observations(mutation):
    spec, observations = cluster_fixture()
    mutation(observations)
    with pytest.raises(ValueError):
        parse(observations, spec)


def test_probe_spec_is_not_an_arbitrary_path_port_or_command_channel():
    spec, _ = cluster_fixture()
    for path, value in [("witness_path", "/etc/shadow"), ("source", "10.10.0.1:/srv/../etc")]:
        changed = copy.deepcopy(spec)
        changed["storage"][path] = value
        with pytest.raises(ValueError):
            cluster_probe_script(changed, "225")
    spec["peers"][0]["port"] = 443
    with pytest.raises(ValueError):
        cluster_probe_script(spec, "225")


def captured_deployment_fixture():
    node, evidence = deployment_fixture("apptainer")
    timestamp = datetime.now(UTC)
    package = evidence["packages"][0]
    spec = {"packages": [{key: package[key] for key in ("path", "source_sha256", "source_url")}],
            "maintenance_window": {"starts_at": (timestamp - timedelta(minutes=5)).isoformat(),
                                   "expires_at": (timestamp + timedelta(minutes=30)).isoformat()}}
    spec_sha = digest(json.dumps(validate_deployment_spec("apptainer", node, spec), sort_keys=True, separators=(",", ":")))
    observation = {"kind": "deployment_probe", "schema_version": 1, "component": "apptainer",
                   "host": evidence["host"], "spec_sha256": spec_sha, "os": evidence["os"], "uid": 0,
                   "packages": [{**package, "metadata": {"exit_code": 0, "stdout": "Package: apptainer\nVersion: 1.5.5\nArchitecture: amd64\n"},
                                  "contents": {"exit_code": 0, "stdout": "-rwxr-xr-x root/root 100 2026-11-07 00:00 ./usr/bin/apptainer\n"}}],
                   "files": {}, "staged_files": {}, "workloads": {"exit_code": 0, "stdout": ""},
                   "userns": {"exit_code": 0}, "fuse": {"exit_code": 0},
                   "dependency_simulation": {"exit_code": 0, "stdout": "Inst apptainer (1.5.5 local-deb [amd64])\n"}}
    return node, spec, observation, timestamp.isoformat()


def test_fixed_probe_can_supply_cpu_only_apptainer_review_without_imported_verified_flags():
    node, spec, observation, timestamp = captured_deployment_fixture()
    observation["requirements"] = {"arbitrary": {"status": "verified"}}
    result = parse_deployment_evidence(observation, "apptainer", node, spec,
                                       execution_id="fixture-execution", observed_at=timestamp)
    assert "arbitrary" not in result["requirements"]
    review = render_deployment("apptainer", node, result)
    assert review["status"] == "ready", review["issues"]
    script = deployment_probe_script("apptainer", node, spec)
    compile(script, "<approved-deployment-probe>", "exec")
    assert '"--simulate", "--no-download"' in script
    assert "os.O_RDONLY" in script and "os.O_CREAT" not in script


@pytest.mark.parametrize("mutation", [
    lambda o: o.update(uid=1000),
    lambda o: o["userns"].update(exit_code=1),
    lambda o: o["fuse"].update(exit_code=1),
    lambda o: o["workloads"].update(stdout="12345\n"),
    lambda o: o["dependency_simulation"].update(stdout="Inst not-staged (1.0 repository)\n"),
    lambda o: o["dependency_simulation"].update(stdout="Inst apptainer (1.6.0 repository)\n"),
    lambda o: o["dependency_simulation"].update(stdout="Remv existing-package [1.0]\n"),
    lambda o: o["packages"][0]["contents"].update(stdout="-rwsr-xr-x root/root 100 2026-11-07 00:00 ./usr/bin/suid\n"),
])
def test_unmet_objective_deployment_prerequisites_stay_blocked(mutation):
    node, spec, observation, timestamp = captured_deployment_fixture()
    mutation(observation)
    result = parse_deployment_evidence(observation, "apptainer", node, spec,
                                       execution_id="fixture-execution", observed_at=timestamp)
    assert render_deployment("apptainer", node, result)["status"] == "blocked"


def local_installer(tmp_path):
    """Run the exact installer logic in tmpdir, adapting only root-owned path policy.

    This is a local filesystem test, not evidence of Linux sudo/ownership setup.
    Production allowlist, staging root, and ownership checks have no runtime flag.
    """
    template = (ROOT / "infra/deploy/install-platform-file.sh.in").read_text()
    payload = template.split("<<'SRS_INSTALL_FILE'\n", 1)[1].rsplit("\nSRS_INSTALL_FILE", 1)[0]
    namespace = {"__name__": "installer_test"}
    exec(compile(payload, "<exact-file-installer>", "exec"), namespace)
    staging = tmp_path / "staging"
    staging.mkdir()
    source, destination = staging / "unit.service", tmp_path / "unit.service"
    source.write_text("new reviewed bytes\n")
    namespace["DESTINATIONS"] = {str(destination)}
    namespace["STAGING"] = staging

    def local_path_policy(path):
        assert path == tmp_path or tmp_path in path.parents
        for candidate in (path, *path.parents):
            if candidate.is_symlink():
                raise ValueError("symlink rejected in local fixture")
            if candidate == tmp_path:
                break

    namespace["secure_path"] = local_path_policy
    return namespace, source, destination


def test_exact_installer_preserves_original_and_publishes_new_bytes_atomically(tmp_path):
    namespace, source, destination = local_installer(tmp_path)
    destination.write_text("original bytes\n")
    before_inode = destination.stat().st_ino
    old_sha = digest(destination.read_text())
    backup = str(destination) + ".srs-backup-" + old_sha[:16]
    result = namespace["install_file"](str(source), str(destination), digest(source.read_text()), old_sha, backup)
    assert Path(backup).read_text() == "original bytes\n"
    assert destination.read_text() == "new reviewed bytes\n"
    assert destination.stat().st_ino != before_inode
    assert result["backup_path"] == backup
    assert not list(tmp_path.glob(".srs-stage-*"))


@pytest.mark.parametrize("backup_kind", ["existing", "symlink"])
def test_exact_installer_never_overwrites_an_existing_backup(tmp_path, backup_kind):
    namespace, source, destination = local_installer(tmp_path)
    destination.write_text("original bytes\n")
    old_sha = digest(destination.read_text())
    backup = Path(str(destination) + ".srs-backup-" + old_sha[:16])
    if backup_kind == "existing":
        backup.write_text("previous recovery evidence\n")
    else:
        backup.symlink_to(tmp_path / "missing-target")
    with pytest.raises(FileExistsError):
        namespace["install_file"](str(source), str(destination), digest(source.read_text()), old_sha, str(backup))
    assert destination.read_text() == "original bytes\n"
    if backup_kind == "existing":
        assert backup.read_text() == "previous recovery evidence\n"
    else:
        assert backup.is_symlink()


def test_exact_installer_rechecks_old_hash_before_any_backup_or_replace(tmp_path):
    namespace, source, destination = local_installer(tmp_path)
    destination.write_text("changed after approval\n")
    old_sha = digest("original approved bytes\n")
    backup = str(destination) + ".srs-backup-" + old_sha[:16]
    with pytest.raises(ValueError, match="changed after approval"):
        namespace["install_file"](str(source), str(destination), digest(source.read_text()), old_sha, backup)
    assert destination.read_text() == "changed after approval\n"
    assert not Path(backup).exists()


def test_exact_installer_new_destination_has_no_backup_and_rejects_create_race(tmp_path):
    namespace, source, destination = local_installer(tmp_path)
    original_check = namespace["matches_destination"]
    calls = 0

    def inject_new_destination(path, expected):
        nonlocal calls
        calls += 1
        if calls == 2:
            destination.write_text("concurrently created\n")
        return original_check(path, expected)

    namespace["matches_destination"] = inject_new_destination
    with pytest.raises(ValueError, match="appeared after approval"):
        namespace["install_file"](str(source), str(destination), digest(source.read_text()), "absent", "")
    assert destination.read_text() == "concurrently created\n"
    assert not list(tmp_path.glob("*.srs-backup-*"))
    assert not list(tmp_path.glob(".srs-stage-*"))


def test_exact_installer_creates_absent_destination_without_backup(tmp_path):
    namespace, source, destination = local_installer(tmp_path)
    result = namespace["install_file"](str(source), str(destination), digest(source.read_text()), "absent", "")
    assert result["backup_path"] is None
    assert destination.read_bytes() == source.read_bytes()
    assert not list(tmp_path.glob("*.srs-backup-*"))
