#!/usr/bin/env python3
"""Receive one verified Windows release via the deployment account's forced SSH command."""

import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

INSTALLER = "ClassroomMate-Setup-win-x64.exe"
ROOT = Path("/var/www/downloads/classroom-mate")
MAX_SIZE = 250 * 1024 * 1024


def version_parts(manifest):
    return tuple(map(int, manifest["version"].split(".")))


def receive(root, source):
    header = source.readline(1025)
    if not header.endswith(b"\n") or len(header) > 1024:
        raise ValueError("invalid release manifest")
    manifest = json.loads(header)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"version", "size", "sha256"}
        or not isinstance(manifest["version"], str)
        or not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){3}", manifest["version"])
        or type(manifest["size"]) is not int
        or not 1 <= manifest["size"] <= MAX_SIZE
        or not isinstance(manifest["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])
    ):
        raise ValueError("invalid release manifest")
    parts = version_parts(manifest)
    if max(parts) > 65534 or parts[-1] < 1:
        raise ValueError("invalid Windows version")

    build = root / f"build-{parts[-1]}"
    latest = root / "latest"
    with tempfile.TemporaryDirectory(dir=root, prefix=".upload-") as directory:
        stage = Path(directory)
        payload = stage / "payload"
        payload.mkdir(mode=0o755)
        digest = hashlib.sha256()
        remaining = manifest["size"]
        with (payload / INSTALLER).open("wb") as file:
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("incomplete installer")
                remaining -= len(chunk)
                digest.update(chunk)
                file.write(chunk)
        if source.read(1) or digest.hexdigest() != manifest["sha256"]:
            raise ValueError("installer SHA-256 or size mismatch")
        (payload / "release.json").write_text(json.dumps(manifest) + "\n", encoding="ascii")

        with (root / ".publish.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if build.exists():
                existing = json.loads((build / "release.json").read_text())
                with (build / INSTALLER).open("rb") as file:
                    existing_hash = hashlib.file_digest(file, "sha256").hexdigest()
                if existing != manifest or existing_hash != manifest["sha256"]:
                    raise ValueError("published build is immutable")
            else:
                payload.rename(build)

            current = latest / "release.json"
            if not current.exists() or version_parts(json.loads(current.read_text())) < parts:
                link = stage / "latest"
                link.symlink_to(build.name, target_is_directory=True)
                os.replace(link, latest)
    print(f"Verified Classroom Mate {manifest['version']}")


if __name__ == "__main__":
    receive(ROOT, sys.stdin.buffer)
