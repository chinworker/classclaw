from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_agent_conversation_frontend_regressions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Agent conversation frontend tests require Node.js")
    root = Path(__file__).resolve().parents[1]
    tests = sorted((root / "tests" / "web").glob("*.test.mjs"))
    result = subprocess.run(
        [node, "--test", *(str(path) for path in tests)],
        cwd=root, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
