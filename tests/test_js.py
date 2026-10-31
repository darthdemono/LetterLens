import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_renderer_unit_tests_pass():
    tests = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.js"))
    result = subprocess.run(["node", "--test", *tests], cwd=ROOT, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
