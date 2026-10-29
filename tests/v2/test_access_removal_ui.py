import shutil
import subprocess
from pathlib import Path

import pytest


def test_access_removal_requires_contextual_confirmation_and_reports_result():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the access removal UI test")
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [node, "tests/v2/test_access_removal_ui.cjs"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
