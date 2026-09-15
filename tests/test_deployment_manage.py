from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import load_settings
from scripts import manage


def git(directory: Path, *arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=directory, check=True, text=True, capture_output=True).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    remote, source, checkout = (tmp_path / name for name in ("remote.git", "source", "checkout"))
    remote.mkdir()
    source.mkdir()
    git(remote, "init", "--bare")
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Deployment test")
    git(source, "config", "user.email", "deployment@example.invalid")
    (source / "README.md").write_text("initial\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "-u", "origin", "main")
    git(tmp_path, "clone", "--branch", "main", str(remote), str(checkout))
    git(checkout, "config", "user.name", "Deployment test")
    git(checkout, "config", "user.email", "deployment@example.invalid")
    manager = manage.Manager(checkout, pwd.getpwuid(os.getuid()).pw_name)

    def project(arguments, **kwargs):
        return manage.run(arguments, cwd=checkout, **kwargs)

    manager.project = project
    return manager, source


def publish(source: Path, name: str = "README.md") -> str:
    path = source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("updated\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "update")
    git(source, "push")
    return git(source, "rev-parse", "HEAD")


def test_upgrade_preview_fetches_without_changing_checkout(repository):
    manager, source = repository
    original = git(manager.root, "rev-parse", "HEAD")
    target = publish(source, "integrations/openclaw/classclaw/src/index.ts")
    plan = manager.upgrade_plan()
    assert plan == {"old": original, "target": target, "plugins": True}
    assert git(manager.root, "rev-parse", "HEAD") == original
    assert not (manager.root / "integrations").exists()


@pytest.mark.parametrize("dirty", ["tracked", "untracked"])
def test_upgrade_refuses_local_work_before_fetch(repository, dirty):
    manager, source = repository
    previous_upstream = git(manager.root, "rev-parse", "@{upstream}")
    publish(source)
    (manager.root / ("README.md" if dirty == "tracked" else "local.txt")).write_text("keep this\n")
    with pytest.raises(manage.ManagementError, match="工作区"):
        manager.upgrade_plan()
    assert git(manager.root, "rev-parse", "@{upstream}") == previous_upstream


def test_upgrade_refuses_diverged_branch(repository):
    manager, source = repository
    git(manager.root, "commit", "--allow-empty", "-m", "local work")
    original = git(manager.root, "rev-parse", "HEAD")
    publish(source)
    with pytest.raises(manage.ManagementError, match="快进"):
        manager.upgrade_plan()
    assert git(manager.root, "rev-parse", "HEAD") == original


class MaintenanceManager(manage.Manager):
    def __init__(self, root: Path, fail: str | None = None):
        super().__init__(root, pwd.getpwuid(os.getuid()).pw_name)
        self.events = []
        self.fail = fail
        self.pending_upgrade.parent.mkdir()
        self.states = {manage.APP_UNIT: "active", manage.GATEWAY_UNIT: "active"}

    def project(self, arguments, **kwargs):
        self.events.append(arguments)
        if arguments[0] == "/usr/bin/python3":
            return subprocess.run(arguments, text=True, capture_output=True, check=True, **kwargs)
        if "alembic" in arguments and self.fail == "migration":
            raise manage.ManagementError("migration failed")
        if "pip" in arguments and self.fail == "dependencies":
            raise manage.ManagementError("dependency install failed")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    def git(self, *arguments, **kwargs):
        self.events.append(["git", *arguments])
        return subprocess.CompletedProcess(arguments, 0, "old\n", "")

    def upgrade_plan(self):
        return {"old": "old", "target": "new", "plugins": True}

    def check_config(self):
        self.events.append(["check"])

    def check_layout(self):
        pass

    def service_states(self):
        return self.states

    def stop(self, units=manage.UNITS):
        self.events.append(["stop", *units])

    def start(self, units=manage.UNITS):
        assert not self.pending_upgrade.exists(), "systemd must be allowed to start after migrations"
        self.events.append(["start", *units])
        if self.fail == "health":
            raise manage.ManagementError("health check failed")

    def snapshot(self, *args, **kwargs):
        self.events.append(["snapshot"])
        if self.fail == "backup":
            raise manage.ManagementError("backup failed")
        return self.root / "saved"

    def build_plugins(self):
        self.events.append(["build_plugins"])


def test_upgrade_stops_and_backs_up_before_mutating_and_restores_active_units(tmp_path):
    manager = MaintenanceManager(tmp_path)
    manager.states[manage.GATEWAY_UNIT] = "inactive"
    manager.maintain("upgrade", tmp_path / "backups")
    events = manager.events
    assert events.index(["stop", *manage.UNITS]) < events.index(["snapshot"]) < events.index(["git", "merge", "--ff-only", "--no-edit", "new"])
    assert events.index(["build_plugins"]) < events.index([manager.python, "-m", "alembic", "upgrade", "head"])
    assert events[-1] == ["start", manage.APP_UNIT]
    assert not manager.pending_upgrade.exists()


@pytest.mark.parametrize("failure", ["backup", "dependencies", "migration", "health"])
def test_maintenance_failure_stops_both_and_preserves_recovery_marker(tmp_path, monkeypatch, failure):
    manager = MaintenanceManager(tmp_path, fail=failure)
    recovery = []
    monkeypatch.setattr(manage, "run", lambda arguments, **_kwargs: recovery.append(arguments))
    with pytest.raises(manage.ManagementError):
        manager.maintain("upgrade", tmp_path / "backups")
    assert recovery == [["systemctl", "stop", manage.GATEWAY_UNIT], ["systemctl", "stop", manage.APP_UNIT]]
    if failure == "backup":
        assert not any(event[:2] == ["git", "merge"] for event in manager.events)
        assert not manager.pending_upgrade.exists()
    else:
        assert json.loads(manager.pending_upgrade.read_text())["backup"] == str(tmp_path / "saved")
        with pytest.raises(manage.ManagementError, match="未完成"):
            manager.maintain("upgrade", tmp_path / "backups")
    if failure != "health":
        assert not any(event[0] == "start" for event in manager.events)


def test_backup_of_stopped_services_does_not_start_them(tmp_path):
    manager = MaintenanceManager(tmp_path)
    manager.states = dict.fromkeys(manage.UNITS, "inactive")
    manager.maintain("backup", tmp_path / "backups")
    assert manager.events[-1] == ["start"]
    assert not any("pip" in event or "alembic" in event or event[0] == "build_plugins" for event in manager.events)


def test_management_lock_refuses_overlap_and_releases_on_error(tmp_path):
    path = tmp_path / "lock"
    with pytest.raises(ValueError), manage.management_lock(path):
        with pytest.raises(manage.ManagementError, match="正在运行"), manage.management_lock(path):
            pytest.fail("a second maintenance action acquired the lock")
        raise ValueError("interrupted")
    with manage.management_lock(path):
        pass


def test_full_snapshot_copies_runtime_without_printing_credentials(tmp_path, monkeypatch, capsys):
    manager = manage.Manager(tmp_path, pwd.getpwuid(os.getuid()).pw_name)
    (tmp_path / ".env").write_text("CLASSCLAW_API_TOKEN=private-test-value\n")
    config_file = tmp_path / "config.toml"
    config_file.write_text("[server]\nport = 8000\n")
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    for path in (workspace, state):
        path.mkdir()
        (path / "state.txt").write_text("saved state")
    destination = tmp_path / "backups"
    backup = destination / "classclaw-backup-test"
    config = {
        "config_file": str(config_file),
        "storage": {
            "class_workspace_root": str(workspace), "openclaw_state_dir": str(state),
            "attachment_dir": str(tmp_path / "attachments"), "database_url": "sqlite:///core.db", "usage_database_url": "sqlite:///usage.db",
        },
    }
    monkeypatch.setattr(manager, "config", lambda: config)

    def project(arguments, **kwargs):
        output = ""
        if arguments[0] == "/usr/bin/python3":
            return manage.run(arguments, capture=True, **kwargs)
        if "scripts/backup.py" in arguments:
            backup.mkdir(parents=True)
            (backup / "manifest.json").write_text('{"version":2}')
            output = str(backup) + "\n"
        elif arguments[0] == "cp":
            source, target = map(Path, arguments[-2:])
            (shutil.copytree if source.is_dir() else shutil.copy2)(source, target)
        elif "freeze" in arguments:
            output = "fastapi==0.115.0\n"
        elif arguments[:2] == ["git", "archive"]:
            (backup / "source.tar").write_bytes(b"source archive")
        return subprocess.CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(manager, "project", project)
    assert manager.snapshot(destination, revision="old", target_revision="new") == backup
    assert "private-test-value" in (backup / "deployment.env").read_text()
    assert "private-test-value" not in capsys.readouterr().out
    metadata = json.loads((backup / "deployment.json").read_text())
    assert metadata["revision"] == "old"
    assert set(metadata["artifacts"]) == {"deployment.env", "classclaw.toml", "openclaw-agents", "openclaw-state"}
    assert (backup / "deployment.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(manage.ManagementError, match="备份目标"):
        manager.snapshot(workspace / "backups", revision="old")


def test_server_profile_loads_without_touching_production_paths():
    profile = Path(__file__).resolve().parents[1] / "deploy/classclaw.2c4g.toml"
    settings = load_settings(profile, environ={})
    assert settings.server.host == "127.0.0.1"
    assert settings.database_url == "sqlite:////opt/classclaw/data/classclaw.db"
    assert settings.max_attachment_bytes == 10 * 1024 * 1024
    assert settings.openclaw_timeout_seconds == 120
    assert settings.openclaw_extractor_enabled


@pytest.mark.parametrize("gateway,state", [
    ("http://remote.example:18789", ".openclaw"),
    ("http://127.0.0.1:18790", ".openclaw"),
    ("http://127.0.0.1:18789", "other-state"),
])
def test_maintenance_refuses_gateway_layout_that_cannot_be_stopped_safely(tmp_path, monkeypatch, gateway, state):
    manager = manage.Manager(tmp_path, pwd.getpwuid(os.getuid()).pw_name)
    monkeypatch.setattr(manager, "config", lambda: {
        "openclaw": {"gateway_url": gateway}, "storage": {"openclaw_state_dir": str(Path(manager.user.pw_dir, state))},
    })
    with pytest.raises(manage.ManagementError):
        manager.check_layout()
