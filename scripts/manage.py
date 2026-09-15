"""Linux/systemd operations for the layout in docs/deployment.md.

This orchestrator uses only the standard library. Project commands always run
as the service user with the project's virtualenv, including during upgrades.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

APP_UNIT = "classclaw.service"
GATEWAY_UNIT = "openclaw-gateway.service"
UNITS = (APP_UNIT, GATEWAY_UNIT)


class ManagementError(RuntimeError):
    pass


@contextmanager
def management_lock(path: Path = Path("/run/lock/classclaw-admin.lock")):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ManagementError("另一个 classclaw 运维命令正在运行，请等待其结束") from exc
        yield
    finally:
        os.close(descriptor)


def run(arguments: list[str], *, capture: bool = False, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(arguments, text=True, capture_output=capture, check=False, **kwargs)
    if check and result.returncode:
        if capture and result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        raise ManagementError(f"命令 {arguments[0]} 失败，退出码 {result.returncode}")
    return result


def wait_http(url: str, *, app: bool, timeout: float = 60) -> None:
    # A proxy configured for the operator's shell must not intercept localhost.
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while True:
        try:
            with opener.open(url, timeout=3) as response:
                payload = response.read(65536)
                if response.status == 200 and (not app or json.loads(payload).get("status") == "ok"):
                    return
        except (URLError, OSError, ValueError, AttributeError):
            pass
        if time.monotonic() >= deadline:
            raise ManagementError(f"健康检查超时：{url}；请检查 classclaw logs 和 classclaw logs --app")
        time.sleep(1)


class Manager:
    def __init__(self, root: Path, user: str = "classclaw"):
        self.root = root.resolve()
        self.user = pwd.getpwnam(user)
        self.python = str(self.root / ".venv/bin/python")
        self.pending_upgrade = self.root / "backups/.upgrade-pending.json"

    def project(self, arguments: list[str], *, directory: Path | None = None, **kwargs) -> subprocess.CompletedProcess:
        environment = dict(os.environ)
        # Deployment settings come from the same .env/TOML as systemd, not the
        # administrator's interactive shell. Never source .env as shell code.
        for name in list(environment):
            if name.startswith(("CLASSCLAW_", "OPENCLAW_")) or name in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "NODE_OPTIONS"}:
                environment.pop(name)
        environment["PATH"] = f"{self.user.pw_dir}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"
        environment["NODE_OPTIONS"] = "--max-old-space-size=1024"
        return run(
            ["/usr/sbin/runuser", "-u", self.user.pw_name, "--", *arguments],
            cwd=directory or self.root, env=environment, **kwargs,
        )

    def git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.project(["git", *arguments], capture=True, check=check)

    def config(self) -> dict:
        result = self.project([
            self.python, "-c", ("import json; from app.config import settings, safe_config_summary; "
                                "print(json.dumps(safe_config_summary(settings)))"),
        ], capture=True)
        return json.loads(result.stdout)

    def check_config(self) -> None:
        self.project([self.python, "scripts/check_config.py"])

    def check_layout(self) -> None:
        config = self.config()
        gateway = urlsplit(config["openclaw"]["gateway_url"])
        if gateway.hostname not in {"localhost", "127.0.0.1", "::1"} or gateway.port != 18789:
            raise ManagementError("配套 Gateway unit 使用本机 18789 端口，请对齐部署配置后维护")
        if Path(config["storage"]["openclaw_state_dir"]).resolve() != Path(self.user.pw_dir, ".openclaw").resolve():
            raise ManagementError("Gateway unit 与 ClassClaw 的 OpenClaw 状态目录必须一致：服务用户的 .openclaw")

    def service_states(self) -> dict[str, str]:
        states = {}
        for unit in UNITS:
            result = run(["systemctl", "show", unit, "--property=LoadState,ActiveState,User,WorkingDirectory"], capture=True)
            properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            if properties.get("LoadState") != "loaded" or properties.get("User") != self.user.pw_name:
                raise ManagementError(f"{unit} 未安装或运行用户不匹配，请先按 docs/deployment.md 安装配套 unit")
            if unit == APP_UNIT and properties.get("WorkingDirectory") != str(self.root):
                raise ManagementError("classclaw.service 的 WorkingDirectory 与代码目录不一致")
            state = properties.get("ActiveState", "unknown")
            if state not in {"active", "inactive", "failed"}:
                raise ManagementError(f"{unit} 当前为 {state}，请等待服务状态稳定后重试")
            states[unit] = state
        return states

    def stop(self, units=UNITS) -> None:
        # Gateway can still call the backend while its current runs drain.
        for unit in reversed(units):
            run(["systemctl", "stop", unit])
            state = run(["systemctl", "show", unit, "--property=ActiveState", "--value"], capture=True).stdout.strip()
            if state not in {"inactive", "failed"}:
                raise ManagementError(f"{unit} 尚未停止，不能执行数据维护")

    def start(self, units=UNITS) -> None:
        if not units:
            return
        for unit in units:
            run(["systemctl", "start", unit])
        self.health(units)

    def health(self, units=UNITS) -> None:
        config = self.config()
        server = config["server"]
        host = server["host"]
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1" if host == "0.0.0.0" else "::1"
        if ":" in host:
            host = f"[{host}]"
        if APP_UNIT in units:
            wait_http(f"http://{host}:{server['port']}/health", app=True)
            print("ClassClaw /health：通过（进程存活）")
        if GATEWAY_UNIT in units:
            gateway = config["openclaw"]["gateway_url"].rstrip("/")
            if urlsplit(gateway).hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ManagementError("该管理脚本仅适用于 Gateway 同机部署")
            wait_http(f"{gateway}/health", app=False)
            print("OpenClaw /health：通过（进程存活；插件、模型和微信请在网页验收）")

    def write_private(self, path: Path, content: str) -> None:
        # Keep every project file write unprivileged, including recovery markers.
        # Use the OS Python so a broken virtualenv cannot prevent recording failure.
        self.project([
            "/usr/bin/python3", "-c",
            ("from pathlib import Path; import sys; path = Path(sys.argv[1]); "
             "path.write_text(sys.stdin.read(), encoding='utf-8'); path.chmod(0o600)"),
            str(path),
        ], input=content)

    def snapshot(self, destination: Path, *, revision: str, target_revision: str | None = None) -> Path:
        config = self.config()
        storage = config["storage"]
        destination = destination.resolve()
        sources = {
            "deployment.env": self.root / ".env",
            "classclaw.toml": Path(config["config_file"]) if config["config_file"] else None,
            "openclaw-agents": Path(storage["class_workspace_root"]),
            "openclaw-state": Path(storage["openclaw_state_dir"]),
        }
        for source in [Path(storage["attachment_dir"]), *[p for p in sources.values() if p and p.is_dir()]]:
            if destination.is_relative_to(source.resolve()):
                raise ManagementError("备份目标不能位于附件、工作区或 OpenClaw 状态目录内")
        result = self.project([self.python, "scripts/backup.py", str(destination)], capture=True)
        backup = Path(result.stdout.strip()).resolve()
        if backup.parent != destination or not (backup / "manifest.json").is_file():
            raise ManagementError("备份脚本没有返回有效的完整数据库备份")
        print(f"数据库备份：{backup}", flush=True)
        artifacts = {}
        for name, source in sources.items():
            if source and source.exists():
                self.project(["cp", "-a", "--", str(source.resolve()), str(backup / name)])
                artifacts[name] = str(source)
        frozen = self.project([self.python, "-m", "pip", "freeze"], capture=True).stdout
        self.write_private(backup / "requirements.freeze.txt", frozen)
        self.project(["git", "archive", "--format=tar", f"--output={backup / 'source.tar'}", "HEAD"])
        # Written last: absence means the deployment snapshot is incomplete,
        # even if the separate database manifest has already been written.
        self.write_private(backup / "deployment.json", json.dumps({
            "version": 1, "revision": revision, "target_revision": target_revision,
            "project_root": str(self.root), "artifacts": artifacts,
            "database_url": storage["database_url"], "usage_database_url": storage["usage_database_url"],
        }, ensure_ascii=False, indent=2) + "\n")
        print(f"完整停机备份：{backup}", flush=True)
        return backup

    def upgrade_plan(self) -> dict:
        if self.git("status", "--porcelain", "--untracked-files=all").stdout.strip():
            raise ManagementError("Git 工作区有修改或未跟踪文件，请先提交或移走；不会自动 stash/reset")
        branch = self.git("symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
        remote = self.git("config", "--get", f"branch.{branch}.remote").stdout.strip()
        if not remote or remote == "." or remote.startswith("-"):
            raise ManagementError("当前分支必须跟踪一个远程分支；不支持 detached HEAD 或本地 upstream")
        old = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("fetch", "--prune", "--", remote)
        target = self.git("rev-parse", "--verify", "@{upstream}^{commit}").stdout.strip()
        if self.git("merge-base", "--is-ancestor", old, target, check=False).returncode:
            raise ManagementError("本地与远程不能快进合并，请在开发环境处理分支差异")
        changed = self.git("diff", "--name-only", "-z", old, target).stdout.split("\0")
        plan = {"old": old, "target": target, "plugins": any(p.startswith("integrations/openclaw/") for p in changed)}
        print(f"升级计划：{old[:12]} → {target[:12]}；重建插件：{'是' if plan['plugins'] else '否'}")
        return plan

    def build_plugins(self) -> None:
        plugin = self.root / "integrations/openclaw/classclaw"
        self.project(["npm", "ci", "--include=dev", "--ignore-scripts", "--no-audit", "--no-fund"], directory=plugin)
        self.project(["npm", "run", "plugin:validate"], directory=plugin)
        compat = self.root / "integrations/openclaw/openclaw-weixin-compat"
        self.project(["npm", "ci", "--omit=peer", "--ignore-scripts", "--no-audit", "--no-fund"], directory=compat)

    def maintain(self, action: str, destination: Path, *, check_only: bool = False) -> None:
        if action == "upgrade":
            self.ensure_no_pending_upgrade()
        plan = self.upgrade_plan() if action == "upgrade" else None
        if plan and (check_only or plan["old"] == plan["target"]):
            print("尚未更改运行服务。" if check_only else "当前已是 upstream 版本，无需升级。")
            return
        self.check_config()
        self.check_layout()
        states = self.service_states()
        revision = plan["old"] if plan else self.git("rev-parse", "HEAD").stdout.strip()
        backup = None
        update_started = False
        try:
            self.stop()
            backup = self.snapshot(destination, revision=revision, target_revision=plan["target"] if plan else None)
            if plan:
                self.project(["mkdir", "-p", "--", str(self.pending_upgrade.parent)])
                self.write_private(self.pending_upgrade, json.dumps({"backup": str(backup), **plan}) + "\n")
                update_started = True
                self.git("merge", "--ff-only", "--no-edit", plan["target"])
                self.project([self.python, "-m", "pip", "install", "--no-cache-dir", "-r", "requirements-runtime.txt"])
                self.project([self.python, "-m", "pip", "check"])
                if plan["plugins"]:
                    self.build_plugins()
                self.check_config()
                self.project([self.python, "-m", "alembic", "upgrade", "head"])
                # Both systemd units refuse to boot with an unfinished update.
                # The schema and code now match; allow startup validation.
                self.pending_upgrade.unlink()
            # Preserve explicitly stopped services; do not start them as a side
            # effect of backups or maintenance performed before first launch.
            self.start(tuple(unit for unit in UNITS if states[unit] == "active"))
        except BaseException:
            # A partially migrated schema must never run under old code. Do not
            # auto-checkout, auto-downgrade, or restart after any maintenance failure.
            if update_started:
                try:
                    self.write_private(self.pending_upgrade, json.dumps({"backup": str(backup), **plan}) + "\n")
                except (OSError, ManagementError):
                    print("无法保存升级失败标记，请保持停服并检查磁盘/权限。", file=sys.stderr)
            for unit in reversed(UNITS):
                try:
                    run(["systemctl", "stop", unit], check=False)
                except OSError:
                    print(f"无法调用 systemctl 停止 {unit}，请人工检查。", file=sys.stderr)
            print(f"维护失败，已请求停止两项服务。原版本：{revision}；完整备份：{backup or '未完成，请检查上方备份路径'}。"
                  "按 docs/deployment.md 的恢复步骤处理；不会自动回滚数据库。", file=sys.stderr)
            raise
        print("维护完成；已恢复维护前处于运行状态的服务。")
        if plan:
            print("请检查 deploy/ 模板变更并完成网页功能验收；已安装的 systemd/Nginx 配置和 OpenClaw 主程序未自动升级。")

    def ensure_no_pending_upgrade(self) -> None:
        if self.pending_upgrade.exists():
            raise ManagementError(f"存在未完成的升级：{self.pending_upgrade}；请先按部署文档恢复，不能直接启动或重复升级")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="ClassClaw 同机部署管理；详见 docs/deployment.md")
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="项目目录")
    commands = result.add_subparsers(dest="command", required=True)
    for action in ("start", "stop", "restart"):
        command = commands.add_parser(action, help="默认操作 ClassClaw 和 OpenClaw Gateway")
        command.add_argument("--backend-only", action="store_true", help="仅操作 ClassClaw 后端")
    for action in ("status", "health", "doctor", "version"):
        commands.add_parser(action)
    logs = commands.add_parser("logs", help="默认查看后端 systemd 日志")
    source = logs.add_mutually_exclusive_group()
    source.add_argument("--gateway", action="store_true")
    source.add_argument("--app", action="store_true", help="查看业务 JSON 日志")
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("-n", "--lines", type=int, default=100)
    for action in ("backup", "upgrade"):
        command = commands.add_parser(action, help="停服备份，成功后恢复之前的运行状态")
        command.add_argument("--directory", type=Path, help="备份根目录，默认项目内 backups/")
        if action == "upgrade":
            command.add_argument("--check", action="store_true", help="仅 fetch 并检查升级计划，不停服或修改工作区")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ManagementError("请在 Ubuntu 服务器通过 sudo classclaw <命令> 运行；--help 可在本地查看")
    os.umask(0o077)
    manager = Manager(args.root)
    action = args.command
    if action in {"start", "stop", "restart", "backup", "upgrade"}:
        with management_lock():
            if action in {"backup", "upgrade"}:
                manager.maintain(action, args.directory or manager.root / "backups", check_only=getattr(args, "check", False))
            else:
                manager.service_states()
                units = (APP_UNIT,) if args.backend_only else UNITS
                if action != "stop":
                    manager.ensure_no_pending_upgrade()
                    manager.check_config()
                if action != "start":
                    manager.stop(units)
                if action != "stop":
                    manager.start(units)
    elif action == "status":
        return run(["systemctl", "status", *UNITS, "--no-pager", "--full"], check=False).returncode
    elif action == "health":
        manager.health()
    elif action == "version":
        print(manager.git("rev-parse", "HEAD").stdout.strip())
        manager.project([manager.python, "--version"])
        manager.project(["node", "--version"])
        manager.project(["openclaw", "--version"])
    elif action == "doctor":
        manager.check_config()
        manager.project([manager.python, "-m", "pip", "check"])
        print(f"服务状态：{manager.service_states()}")
        print(f"项目所在磁盘可用：{shutil.disk_usage(manager.root).free / (1024 ** 3):.1f} GiB")
        run(["systemctl", "show", *UNITS, "--property=Id,MainPID,MemoryCurrent,MemoryPeak,MemoryHigh,MemoryMax"])
        manager.health()
    elif action == "logs":
        if args.lines < 1:
            raise ManagementError("日志行数必须大于 0")
        if args.app:
            arguments = ["tail", "-n", str(args.lines)]
            if args.follow:
                arguments.append("-F")
            manager.project([*arguments, "--", manager.config()["storage"]["log_file"]])
        else:
            arguments = ["journalctl", "-u", GATEWAY_UNIT if args.gateway else APP_UNIT, "-n", str(args.lines), "--no-pager"]
            run([*arguments, "-f"] if args.follow else arguments)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ManagementError, OSError, KeyError, ValueError) as exc:
        print(f"classclaw：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("classclaw：命令被中断，请检查服务状态。", file=sys.stderr)
        raise SystemExit(130) from None
