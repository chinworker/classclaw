"""Path validation shared by normal deletion and targeted maintenance."""
import shutil
from pathlib import Path

from app.core.errors import AppError


def safe_path(root: Path, target: Path) -> Path:
    root = root.expanduser().absolute()
    target = target.expanduser().absolute()
    if not target.is_relative_to(root) or target == root or ".." in target.parts:
        raise AppError("DELETION_UNSAFE_PATH", "清理路径不在允许的目录中", 409)
    # Reject symlinks in both roots and descendants, including dangling links.
    for part in (target, *target.parents):
        if part.is_symlink():
            raise AppError("DELETION_UNSAFE_PATH", "清理路径包含符号链接", 409)
    return target


def remove_tree(root: Path, target: Path) -> bool:
    path = safe_path(root, target)
    if not path.exists():
        return False
    if any(child.is_symlink() for child in path.rglob("*")):
        raise AppError("DELETION_UNSAFE_PATH", "清理目录包含符号链接", 409)
    shutil.rmtree(path)
    return True
