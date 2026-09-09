from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _nearest_existing(path: Path) -> Path | None:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.exists() else None


def _filesystem_checks(summary: dict) -> list[dict]:
    storage = summary["storage"]
    database_url = storage["database_url"]
    database_path = None
    if database_url.startswith("sqlite:///") and not database_url.endswith(":memory:"):
        database_path = Path(database_url.removeprefix("sqlite:///"))
    usage_url = storage["usage_database_url"]
    usage_path = None if usage_url.endswith(":memory:") else Path(usage_url.removeprefix("sqlite:///"))
    targets = [
        ("database", database_path, "file"),
        ("usage_database", usage_path, "file"),
        ("attachments", Path(storage["attachment_dir"]), "directory"),
        ("class_workspace", Path(storage["class_workspace_root"]), "directory"),
        ("openclaw_state", Path(storage["openclaw_state_dir"]), "directory"),
        ("log", Path(storage["log_file"]), "file"),
    ]
    rows = []
    for name, path, expected in targets:
        if path is None:
            continue
        exists = path.exists()
        type_ok = not exists or (path.is_dir() if expected == "directory" else path.is_file())
        ancestor = _nearest_existing(path if expected == "directory" else path.parent)
        writable = bool(ancestor and os.access(ancestor, os.W_OK))
        rows.append({
            "name": name,
            "path": str(path),
            "exists": exists,
            "type_ok": type_ok,
            "nearest_existing_parent": str(ancestor) if ancestor else None,
            "parent_writable": writable,
        })
    return rows


def _security_checks(settings) -> list[dict]:
    checks = [
        ("classclaw_api_token", settings.api_token, 32, "CLASSCLAW_API_TOKEN"),
        ("openclaw_gateway_token", settings.openclaw_gateway_token, 16, "CLASSCLAW_OPENCLAW_GATEWAY_TOKEN"),
        ("default_admin_password", settings.default_admin_password, 12, "CLASSCLAW_DEFAULT_ADMIN_PASSWORD"),
    ]
    markers = ("replace", "change-me", "changeme", "example", "32767")
    rows = []
    for name, value, minimum, environment_name in checks:
        normalized = str(value or "").strip().lower()
        configured = bool(normalized)
        placeholder = configured and any(marker in normalized for marker in markers)
        long_enough = configured and len(str(value)) >= minimum
        if not configured:
            issue = f"{environment_name} 未配置"
        elif placeholder:
            issue = f"{environment_name} 仍是示例值"
        elif not long_enough:
            issue = f"{environment_name} 长度至少应为 {minimum} 个字符"
        else:
            issue = None
        rows.append({"name": name, "configured": configured, "safe": issue is None, "issue": issue})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 ClassClaw 静态配置，不启动应用或修改文件")
    parser.add_argument("--config", type=Path, help="要校验的 classclaw.toml；省略时使用默认发现规则")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    if args.config:
        os.environ["CLASSCLAW_CONFIG_FILE"] = str(args.config.expanduser().resolve())
    try:
        from app.config import safe_config_summary, settings

        summary = safe_config_summary(settings)
        filesystem = _filesystem_checks(summary)
        security = _security_checks(settings)
        result = {
            "ok": all(row["type_ok"] and row["parent_writable"] for row in filesystem) and all(row["safe"] for row in security),
            "configuration": summary,
            "filesystem": filesystem,
            "security": security,
        }
    except (OSError, RuntimeError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        summary = result["configuration"]
        print("配置有效")
        print(f"配置文件：{summary['config_file'] or '未提供，使用默认值和环境变量'}")
        print(f"配置哈希：{summary['config_hash']}")
        for row in result["filesystem"]:
            state = "可用" if row["type_ok"] and row["parent_writable"] else "不可用"
            print(f"- {row['name']}: {state} · {row['path']}")
        print("密钥检查：通过（内容未输出）")
    else:
        print(f"配置无效：{result.get('error') or '文件类型或目录写权限检查失败'}", file=sys.stderr)
        if result.get("filesystem"):
            for row in result["filesystem"]:
                if not row["type_ok"] or not row["parent_writable"]:
                    print(f"- {row['name']}: {row['path']}", file=sys.stderr)
        for row in result.get("security") or []:
            if not row["safe"]:
                print(f"- {row['issue']}", file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
