"""Build a self-contained Windows x64 folder and zip from Windows/macOS/Linux.

Requires .NET 10 SDK and Python 3 with pip. Downloads only official runtime and
PyPI wheels; binary dependencies remain in ignored .dist-cache/dist directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


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
    parser.add_argument("--offline", action="store_true", help="use previously fetched media dependencies")
    parser.add_argument("--nuget-source", help="optional NuGet feed or local package directory")
    args = parser.parse_args()
    cache = ROOT / ".dist-cache"
    wheels = cache / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    embedded = cache / "python-3.13.15-embed-amd64.zip"
    if not args.offline:
        from fetch import download

        download("https://www.python.org/ftp/python/3.13.15/python-3.13.15-embed-amd64.zip", embedded)
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
    if not embedded.exists():
        raise RuntimeError("media runtime is incomplete")
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
    digest = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    Path(archive + ".sha256").write_text(f"{digest}  {Path(archive).name}\n", encoding="utf-8")
    print(archive)
    print("SHA256 " + digest)


if __name__ == "__main__":
    main()
