"""The V2 workbench exposes separate active, draft and proof states."""

from pathlib import Path
import json
import shutil
import subprocess

import pytest

import httpx

from bot.deployment import DeploymentSettings
from bot.services import build_services
from bot.web.auth import hash_password
from bot.web.v2_app import create_app


async def test_ui_separates_active_draft_and_escapes_test_output(tmp_path):
    settings = DeploymentSettings(tmp_path, tmp_path / "app-v2.sqlite3", tmp_path / "key")
    services = build_services(settings)
    services.repo.set_setup_state("web_admin_hash", hash_password("synthetic-admin-password"))
    app = create_app(settings, services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/login", json={"password": "synthetic-admin-password"})
        response = await client.get("/admin/transcription")
        assert response.status_code == 200
        assert "Configurazione attiva" in response.text
        assert "Bozza" in response.text
        assert "Testo restituito" in response.text
        assert 'value="multimodal"' in response.text and 'value="two_stage"' in response.text
        script = Path(__file__).parents[2] / "bot/web/static/transcription.js"
        javascript = script.read_text()
        assert "textContent = data.result.text" in javascript
        assert "innerHTML" not in javascript
        assert "generation === editGeneration" in javascript
        assert "setFormBusy(true)" in javascript
        assert "...document.querySelectorAll('.v2-main button')" in javascript
        assert "if (control.id !== 'save-draft')" not in javascript
        assert "loadCatalog('refinement'" in javascript
        assert "state.credentials" in javascript
        assert "transcriptionState.credentialNeedsUpdate(state.draft, state.credentials)" in javascript
        assert "$('#credential-version').value = currentCredential || draft.credential_version" in javascript
    services.repo.close()


def test_transcription_ui_busy_unlock_recomputes_file_and_credential_state():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to exercise the browser state helper")
    helper = Path(__file__).parents[2] / "bot/web/static/transcription_state.js"
    script = f"""
const assert = require('node:assert/strict');
const {{ createBusyGate, credentialNeedsUpdate }} = require({json.dumps(str(helper))});
const runTest = {{ disabled: true }};
const saveDraft = {{ disabled: false }};
const activate = {{ disabled: true }};
const upload = {{ disabled: false }};
const controls = [runTest, saveDraft, activate, upload];
let draft = {{ service: 'openai', revision: 4, credential_version: 1 }};
const credentials = {{ openai: {{ configured: true, credential_version: 1 }} }};
let fileSelected = false;
let successfulTest = null;
let needsUpdate = false;
function recompute() {{
  needsUpdate = credentialNeedsUpdate(draft, credentials);
  runTest.disabled = draft.revision === null || needsUpdate || !fileSelected;
  activate.disabled = !successfulTest || needsUpdate;
}}
const gate = createBusyGate(() => controls, recompute);

fileSelected = true;
recompute();
assert.equal(runTest.disabled, false, 'selected file enables a test');
assert.equal(gate.begin(), true, 'save acquires the shared busy lock');
assert.equal(gate.begin(), false, 'a second operation cannot race with save');
assert.ok(controls.every(control => control.disabled), 'all actions are blocked while busy');
draft = {{ ...draft, revision: 5 }};
assert.equal(gate.end(), true);
assert.equal(runTest.disabled, false, 'unlock derives test availability from the current file and revision');

credentials.openai.credential_version = 2;
recompute();
assert.equal(needsUpdate, true, 'rotating a credential invalidates the current draft immediately');
assert.equal(runTest.disabled, true);
successfulTest = 'proof-for-old-credential';
recompute();
assert.equal(activate.disabled, true, 'old proof cannot activate after credential rotation');

draft = {{ ...draft, revision: 6, credential_version: 2 }};
successfulTest = null;
recompute();
assert.equal(needsUpdate, false, 'a draft saved at the current credential version is current');
assert.equal(runTest.disabled, false, 'the selected file can be tested after saving the updated draft');
"""
    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)


def test_transcription_ui_flow_keeps_dirty_revisions_blocked_and_allows_activation_retry():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the transcription page flow")
    root = Path(__file__).parents[2]
    subprocess.run([
        node,
        str(Path(__file__).with_name("transcription_ui_flow.js")),
        str(root / "bot/web/templates/v2_transcription.html"),
        str(root / "bot/web/static/transcription_state.js"),
    ], check=True, capture_output=True, text=True)
