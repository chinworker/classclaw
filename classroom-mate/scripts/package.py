"""Build a self-contained Windows x64 folder, zip, and optional Setup EXE.

Requires .NET 10 SDK and Python 3 with pip. Downloads only official runtime and
PyPI wheels; binary dependencies remain in ignored .dist-cache/dist directories.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMBEDDED_PYTHON = "python-3.13.15-embed-amd64.zip"
# Windows x64 embeddable package from https://www.python.org/downloads/release/python-31315/
EMBEDDED_PYTHON_SHA256 = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def write_checksum(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    print(path)
    print("SHA256 " + digest)


def locked_wheels(folder):
    files = {hashlib.sha256(path.read_bytes()).hexdigest(): path for path in folder.glob("*.whl")}
    selected = []
    for line in (ROOT / "media/requirements-lock.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([\w.-]+)==([\w.]+) --hash=sha256:([0-9a-f]{64})", line)
        if not match or match[3] not in files:
            raise RuntimeError("missing or invalid locked wheel: " + line.split()[0])
        selected.append(files[match[3]])
    return selected


def copy_notices(dist):
    assets = json.loads((ROOT / "src/ClassroomMate.Windows/obj/project.assets.json").read_text(encoding="utf-8"))
    deps = json.loads((dist / "ClassroomMate.deps.json").read_text(encoding="utf-8"))
    packages = set(assets["libraries"]) | set(deps["libraries"])
    for package in packages:
        relative = package.removeprefix("runtimepack.").lower()
        for folder in assets["packageFolders"]:
            source = Path(folder) / relative
            if not source.is_dir():
                continue
            destination = dist / "licenses" / relative.replace("/", "-")
            for path in source.iterdir():
                if path.is_file() and (path.name.lower().startswith(("license", "third-party", "thirdparty")) or path.suffix == ".nuspec"):
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination / path.name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--iscc", help="path to Inno Setup 6.3+ ISCC.exe to also build the Windows installer")
    parser.add_argument("--offline", action="store_true", help="use previously fetched media dependencies")
    parser.add_argument("--nuget-source", help="optional NuGet feed or local package directory")
    args = parser.parse_args()
    cache = ROOT / ".dist-cache"
    wheels = cache / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    embedded = cache / EMBEDDED_PYTHON
    if not args.offline:
        from fetch import download

        download(f"https://www.python.org/ftp/python/3.13.15/{EMBEDDED_PYTHON}", embedded, EMBEDDED_PYTHON_SHA256)
        run(
            sys.executable,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--platform",
            "win_amd64",
            "--python-version",
            "3.13",
            "--implementation",
            "cp",
            "--dest",
            str(wheels),
            "--require-hashes",
            "-r",
            str(ROOT / "media/requirements-lock.txt"),
        )
    selected = locked_wheels(wheels)
    if not embedded.is_file() or hashlib.sha256(embedded.read_bytes()).hexdigest() != EMBEDDED_PYTHON_SHA256:
        raise RuntimeError("embedded Python runtime is missing or its SHA-256 does not match")
    dist = ROOT / "dist" / "ClassroomMate-win-x64"
    if dist.exists():
        shutil.rmtree(dist)
    options = ["--source", args.nuget_source, "-p:NuGetAudit=false"] if args.nuget_source else []
    run(
        args.dotnet,
        "publish",
        "src/ClassroomMate.Windows",
        "-c",
        "Release",
        "-r",
        "win-x64",
        "--self-contained",
        "true",
        "-p:EnableWindowsTargeting=true",
        "-p:DebugType=None",
        "-p:DebugSymbols=false",
        "-o",
        str(dist),
        *options,
    )
    copy_notices(dist)
    runtime = dist / "runtime"
    with zipfile.ZipFile(embedded) as archive:
        if archive.testzip():
            raise RuntimeError("invalid embedded Python archive")
        archive.extractall(runtime)
    (runtime / "Lib/site-packages").mkdir(parents=True, exist_ok=True)
    for wheel in selected:
        with zipfile.ZipFile(wheel) as archive:
            if archive.testzip():
                raise RuntimeError("invalid wheel")
            archive.extractall(runtime / "Lib/site-packages")
    (runtime / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\nimport site\n")
    (dist / "media").mkdir()
    for file in ("publisher.py", "requirements.txt", "requirements-lock.txt"):
        shutil.copy2(ROOT / "media" / file, dist / "media" / file)
    for file in ("README.md", "THIRD-PARTY.md"):
        shutil.copy2(ROOT / file, dist / file)
    for file in ("install.ps1", "uninstall.ps1"):
        # Windows PowerShell 5.1 needs a BOM to decode Chinese messages as UTF-8.
        (dist / file).write_text((ROOT / "scripts" / file).read_text(encoding="utf-8"), encoding="utf-8-sig")
    shutil.copytree(
        ROOT.parent / "docs",
        dist / "docs",
        ignore=lambda _, names: [
            name
            for name in names
            if name not in {"windows-client-agent-prompt.md", "classroom-monitoring.md", "classroom-media-deployment.md"}
        ],
    )
    # README's repository-relative documentation links must work in the extracted package.
    readme = dist / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("../docs/", "docs/"), encoding="utf-8")
    manifest = {
        path.relative_to(dist).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(dist.rglob("*"))
        if path.is_file()
    }
    (dist / "SHA256SUMS.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = shutil.make_archive(str(ROOT / "dist/ClassroomMate-win-x64"), "zip", dist.parent, dist.name)
    write_checksum(Path(archive))
    if args.iscc:
        migration = (ROOT / "installer/migrate-user-install.ps1").read_text(encoding="utf-8")
        def encode_migration(finalize):
            script = f"$finalizeUpgrade = ${str(finalize).lower()}\n{migration}"
            return base64.b64encode(script.encode("utf-16-le")).decode("ascii")

        run(args.iscc, f"/DPrepareUser={encode_migration(False)}", f"/DFinishUser={encode_migration(True)}",
            str(ROOT / "installer/ClassroomMate.iss"))
        write_checksum(dist.parent / "ClassroomMate-Setup-win-x64.exe")


if __name__ == "__main__":
    main()
