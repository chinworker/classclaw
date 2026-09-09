from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="将现有唯一管理员密码直接重置为 .env 配置的 CLASSCLAW_DEFAULT_ADMIN_PASSWORD，并撤销旧会话。",
        epilog="建议先停止 ClassClaw 服务。无需旧密码或交互输入，不输出密码，不修改其他业务数据。",
    )
    parser.parse_args(argv)
    try:
        from app.config import settings
    except (OSError, RuntimeError, ValueError):
        print("配置加载失败，请检查 CLASSCLAW_CONFIG_FILE、TOML 和环境变量。", file=sys.stderr)
        return 1
    # dotenv is loaded by app.config (process env has precedence). Refuse the
    # historical implicit default when no password was explicitly configured.
    if not os.environ.get("CLASSCLAW_DEFAULT_ADMIN_PASSWORD", "").strip():
        print("未设置 CLASSCLAW_DEFAULT_ADMIN_PASSWORD，请先在 .env 中配置；密码未修改。", file=sys.stderr)
        return 1
    if not settings.database_url.startswith("sqlite:///") or settings.database_url.endswith(":memory:"):
        print("本地重置仅支持已经存在的文件型 SQLite 业务库。", file=sys.stderr)
        return 1
    database_path = Path(settings.database_url.removeprefix("sqlite:///"))
    if not database_path.is_file():
        print("业务数据库文件不存在，请核对配置；未创建数据库或账号。", file=sys.stderr)
        return 1

    from sqlalchemy.exc import SQLAlchemyError

    from app.core.errors import AppError
    from app.database import writer_session
    from app.services.accounts import admin_for_local_reset, reset_admin_password_locally

    try:
        with writer_session() as db:
            admin = admin_for_local_reset(db)
            admin_id, username = admin.id, admin.username
            print(f"业务数据库：{database_path}")
            print(f"待重置管理员：{username}")
            admin = reset_admin_password_locally(db, admin_id=admin_id, new_password=settings.default_admin_password)
            active = admin.is_active
        print(f"管理员 {username} 的密码已重置为 CLASSCLAW_DEFAULT_ADMIN_PASSWORD 的配置值（不显示），旧会话已撤销。")
        print("下次登录后请修改密码；用户名、其他账号和业务数据保持不变。")
        if not active:
            print("注意：该管理员仍处于停用状态，重置密码不会自动启用账号。")
        return 0
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError):
        # SQLAlchemy exception text can contain SQL parameters (password hashes).
        print("数据库操作失败，未提交的修改已回滚；请检查文件权限、表结构或锁占用后重试。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n操作被中断，请核对结果；未提交的修改会回滚。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
