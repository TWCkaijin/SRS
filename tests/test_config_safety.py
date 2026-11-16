"""Execute reviewed config scripts only against isolated local temporary paths."""

import hashlib
import os
import stat
from types import SimpleNamespace

import pytest
from srs import planner
from srs.config import Settings


def config_plan(monkeypatch, tmp_path, *, exists=True, evidence=None):
    root = tmp_path.resolve()
    path = root / "etc" / "srs" / "approved-config.json"
    original = b'{"old": true}\n'
    if exists:
        path.parent.mkdir(parents=True)
        path.write_bytes(original)
    monkeypatch.setattr(planner, "CONFIG_PATH", str(path))
    if evidence is None:
        evidence = ({"exists": True, "content": original.decode(),
                     "sha256": hashlib.sha256(original).hexdigest()} if exists else {"exists": False})
    node = SimpleNamespace(
        id="101", alias="Test_Node_101", hostname="test.invalid", port=22,
        ssh_username="admin", enabled=True, inventory_revision=1,
        host_key_sha256="SHA256:" + "A" * 43, hardware={"gpus": []},
        facts={"files": {str(path): evidence}}, observed_at="2026-11-07T00:00:00Z",
    )
    draft = SimpleNamespace(parameters={"content": {"new": "核准設定"}})
    settings = Settings(mode="demo", data_dir=root, _env_file=None)
    steps, issues = planner.build_config_step(draft, node, settings)
    step = steps[0]
    assert step["argv"][-1] == step["script"]
    return path, original, step, issues


def execute(step):
    exec(compile(step["script"], "<approved-config-step>", "exec"), {"__name__": "__main__"})


def backup_for(path, step):
    return path.with_name(os.path.basename(step["file_change"]["backup_path"]))


def test_existing_config_is_backed_up_then_atomically_replaced(monkeypatch, tmp_path):
    path, original, step, issues = config_plan(monkeypatch, tmp_path)
    assert not issues
    execute(step)
    assert path.read_text() == step["file_change"]["after"]
    backup = backup_for(path, step)
    assert backup.read_bytes() == original
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert not list(path.parent.glob(".srs-config-*"))


def test_absent_config_creates_safe_parent_and_no_backup(monkeypatch, tmp_path):
    path, _, step, issues = config_plan(monkeypatch, tmp_path, exists=False)
    assert not issues
    execute(step)
    assert path.read_text() == step["file_change"]["after"]
    assert not backup_for(path, step).exists()


@pytest.mark.parametrize("dangling", [False, True])
def test_target_symlink_is_rejected_without_following_it(monkeypatch, tmp_path, dangling):
    path, _, step, _ = config_plan(monkeypatch, tmp_path)
    victim = path.parent / "victim"
    if not dangling:
        victim.write_text("keep victim")
    path.unlink()
    path.symlink_to(victim)
    with pytest.raises(OSError):
        execute(step)
    assert path.is_symlink()
    assert not backup_for(path, step).exists()
    assert not victim.exists() if dangling else victim.read_text() == "keep victim"


@pytest.mark.parametrize("ancestor", ["srs", "etc"])
def test_parent_symlink_is_rejected(monkeypatch, tmp_path, ancestor):
    path, original, step, _ = config_plan(monkeypatch, tmp_path)
    parent = path.parent if ancestor == "srs" else path.parent.parent
    moved = parent.with_name(parent.name + "-real")
    parent.rename(moved)
    parent.symlink_to(moved, target_is_directory=True)
    with pytest.raises(OSError):
        execute(step)
    assert path.read_bytes() == original
    assert not backup_for(path, step).exists()


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling_symlink"])
def test_existing_backup_is_never_overwritten(monkeypatch, tmp_path, kind):
    path, original, step, _ = config_plan(monkeypatch, tmp_path)
    backup = backup_for(path, step)
    victim = path.parent / "backup-victim"
    if kind == "file":
        backup.write_text("older backup")
    else:
        if kind == "symlink":
            victim.write_text("keep victim")
        backup.symlink_to(victim)
    with pytest.raises(FileExistsError):
        execute(step)
    assert path.read_bytes() == original
    if kind == "file":
        assert backup.read_text() == "older backup"
    else:
        assert backup.is_symlink()
        assert victim.read_text() == "keep victim" if kind == "symlink" else not victim.exists()
    assert not list(path.parent.glob(".srs-config-*"))


@pytest.mark.parametrize("exists", [False, True])
def test_drift_before_execution_does_not_create_backup(monkeypatch, tmp_path, exists):
    path, _, step, _ = config_plan(monkeypatch, tmp_path, exists=exists)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unapproved content")
    with pytest.raises(RuntimeError, match="appeared|SHA256"):
        execute(step)
    assert path.read_text() == "unapproved content"
    assert not backup_for(path, step).exists()


@pytest.mark.parametrize("exists", [False, True])
def test_hash_and_absence_rechecked_immediately_before_replace(monkeypatch, tmp_path, exists):
    path, original, step, _ = config_plan(monkeypatch, tmp_path, exists=exists)
    chmod = os.fchmod

    def concurrent_write(fd, mode):
        chmod(fd, mode)
        path.write_text("concurrent change")

    monkeypatch.setattr(os, "fchmod", concurrent_write)
    with pytest.raises(RuntimeError, match="appeared|SHA256"):
        execute(step)
    assert path.read_text() == "concurrent change"
    if exists:
        assert backup_for(path, step).read_bytes() == original
    assert not list(path.parent.glob(".srs-config-*"))


def test_same_content_replacement_is_detected_before_commit(monkeypatch, tmp_path):
    path, original, step, _ = config_plan(monkeypatch, tmp_path)
    chmod = os.fchmod

    def concurrent_replace(fd, mode):
        chmod(fd, mode)
        replacement = path.with_name("replacement")
        replacement.write_bytes(original)
        replacement.replace(path)

    monkeypatch.setattr(os, "fchmod", concurrent_replace)
    with pytest.raises(RuntimeError, match="identity changed"):
        execute(step)
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".srs-config-*"))


def test_parent_swapped_during_write_is_rejected(monkeypatch, tmp_path):
    path, original, step, _ = config_plan(monkeypatch, tmp_path)
    moved = path.parent.with_name("moved-srs")
    victim_dir = path.parent.with_name("victim")
    victim_dir.mkdir()
    victim = victim_dir / path.name
    victim.write_text("keep victim")
    chmod = os.fchmod

    def swap_parent(fd, mode):
        chmod(fd, mode)
        path.parent.rename(moved)
        path.parent.symlink_to(victim_dir, target_is_directory=True)

    monkeypatch.setattr(os, "fchmod", swap_parent)
    with pytest.raises(RuntimeError, match="parent directory changed"):
        execute(step)
    assert victim.read_text() == "keep victim"
    assert (moved / path.name).read_bytes() == original
    assert not list(moved.glob(".srs-config-*"))


@pytest.mark.parametrize("evidence", [
    {"error": "symlink"},
    {"exists": True, "content": "old", "sha256": "0" * 64},
    {"exists": True, "content": None, "sha256": "0" * 64},
])
def test_invalid_preflight_evidence_blocks_approval(monkeypatch, tmp_path, evidence):
    _, _, _, issues = config_plan(monkeypatch, tmp_path, evidence=evidence)
    assert issues
