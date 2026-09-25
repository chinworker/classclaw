"""Resumable HTTPS downloads for release tooling; no application network requests."""

from __future__ import annotations

import concurrent.futures
import hashlib
import urllib.request
from pathlib import Path


def download(url: str, destination: Path, digest: str | None = None, algorithm="sha256", block=1024 * 1024):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and digest and hashlib.new(algorithm, destination.read_bytes()).hexdigest() == digest:
        return
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=0-0"}), timeout=30) as response:
        if response.status != 206:
            data = response.read()
            if digest and hashlib.new(algorithm, data).hexdigest() != digest:
                raise RuntimeError("download checksum mismatch")
            destination.write_bytes(data)
            return
        length = int(response.headers["Content-Range"].split("/")[-1])
    parts = destination.parent / (destination.name + ".parts")
    parts.mkdir(exist_ok=True)

    def part(index):
        start, end = index * block, min(length, (index + 1) * block) - 1
        path = parts / str(index)
        for _ in range(40):
            offset = path.stat().st_size if path.exists() else 0
            if offset == end - start + 1:
                return
            try:
                request = urllib.request.Request(url, headers={"Range": f"bytes={start + offset}-{end}"})
                with urllib.request.urlopen(request, timeout=15) as response:
                    if response.status != 206 or not response.headers["Content-Range"].startswith(f"bytes {start + offset}-"):
                        raise RuntimeError("server did not honor range")
                    with path.open("ab") as output:
                        while chunk := response.read(16384):
                            output.write(chunk)
            except (OSError, ValueError):
                continue
        raise RuntimeError(f"download incomplete: part {index}")

    count = (length + block - 1) // block
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(part, range(count)))
    with destination.open("wb") as output:
        for index in range(count):
            output.write((parts / str(index)).read_bytes())
    if digest and hashlib.new(algorithm, destination.read_bytes()).hexdigest() != digest:
        raise RuntimeError("download checksum mismatch")
