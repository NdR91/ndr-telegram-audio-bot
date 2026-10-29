import shutil
import subprocess
from pathlib import Path

import pytest


def test_stale_owner_invitation_cannot_show_candidate():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the setup invitation UI test')
    root = Path(__file__).resolve().parents[2]
    subprocess.run([node, 'tests/v2/test_setup_invite_ui.cjs'], cwd=root, check=True)
