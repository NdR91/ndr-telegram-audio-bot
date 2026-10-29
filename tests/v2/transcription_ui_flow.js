"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const page = process.argv[2];
const helperPath = process.argv[3];
const html = fs.readFileSync(page, "utf8");
const helper = require(helperPath);
const initialDraft = {
  service: "openai", mode: "multimodal", revision: 1, audio_model: "audio-v1",
  text_model: null, credential_version: 1, system_prompt: "faithful",
  refine_template: "{raw_text}", limits: {
    max_file_size_mb: 20, max_per_user: 2, max_concurrent_global: 6,
    cooldown_seconds: 30, transcribe_timeout: 120, refine_timeout: 90,
    queue_enabled: true,
  },
};

assert.match(html, /id="save-draft"/);
const elements = new Map();
function element(id, defaults = {}) {
  const item = {
    id, value: "", disabled: false, hidden: false, checked: false,
    textContent: "", files: [], elements: [], listeners: {}, options: [],
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); },
    replaceChildren(...children) { this.options = children; },
    add(option) { this.options.push(option); },
    ...defaults,
  };
  elements.set(id, item);
  return item;
}
const ids = [
  "service", "draft-form", "credential", "credential-label", "credential-version", "rename-credential-label", "rename-credential", "audio-model-id",
  "text-model-id", "text-model", "audio-model", "audio-model-search", "text-model-search", "text-model-wrap", "system-prompt",
  "refine-template", "max-file-size", "max-user", "max-global", "cooldown",
  "transcribe-timeout", "refine-timeout", "queue-enabled", "draft-state", "save-status",
  "save-credential", "credential-state", "load-models", "load-text-models", "audio-file",
  "run-test", "test-status", "test-result", "result-text", "result-meta", "activate",
  "restore", "active-summary", "max-label",
];
ids.forEach((id) => element(id));
element("save-draft");
const radioModes = [
  { value: "multimodal", checked: true }, { value: "two_stage", checked: false },
].map((mode) => ({ ...element(`mode-${mode.value}`), ...mode }));
const buttons = ["save-draft", "save-credential", "rename-credential", "load-models", "load-text-models", "run-test", "activate", "restore"]
  .map((id) => elements.get(id));
const form = elements.get("draft-form");
form.elements = [elements.get("service"), elements.get("credential"), elements.get("credential-version"),
  elements.get("audio-model-id"), elements.get("text-model-id"), elements.get("system-prompt"),
  elements.get("refine-template"), elements.get("max-file-size"), elements.get("max-user"),
  elements.get("max-global"), elements.get("cooldown"), elements.get("transcribe-timeout"),
  elements.get("refine-timeout"), elements.get("queue-enabled")];
const document = {
  querySelector(selector) {
    if (selector === 'input[name="mode"]:checked') return radioModes.find((radio) => radio.checked);
    const radioMatch = selector.match(/^input\[name="mode"\]\[value="(.+)"\]$/);
    if (radioMatch) return radioModes.find((radio) => radio.value === radioMatch[1]);
    return elements.get(selector.replace(/^#/, ""));
  },
  querySelectorAll(selector) {
    if (selector === ".v2-main button") return buttons;
    return [];
  },
};
class FakeOption {
  constructor(label, value) { this.textContent = label; this.value = value; }
}
let draft = initialDraft;
let activationAttempts = 0;
const requests = [];
let nextRevision = initialDraft.revision;
const fetch = async (url, options = {}) => {
  requests.push(url);
  if (url === '/api/transcription/credentials/openai/1/label') {
    return { ok: true, json: async () => ({ keys: [{version: 1, label: 'Rinominata'}, {version: 2, label: 'Lavoro'}] }) };
  }
  if (url.startsWith("/api/transcription/models?")) {
    return { ok: true, json: async () => ({ models: [
      { id: "audio-v2", label: "Audio model" }, { id: "same-id", label: "same-id" },
      { id: "other", label: "Another model" },
    ] }) };
  }
  if (url === "/api/transcription/draft") {
    const body = JSON.parse(options.body);
    draft = { ...(draft || initialDraft), ...body, revision: ++nextRevision };
    return { ok: true, json: async () => ({ draft }) };
  }
  if (url === "/api/transcription/test") {
    return { ok: true, json: async () => ({ status: "succeeded", revision: draft.revision, id: "test-1", result: null }) };
  }
  if (url === "/api/transcription/activate") {
    activationAttempts += 1;
    if (activationAttempts === 1) return { ok: false, json: async () => ({ error: "synthetic temporary failure" }) };
    const active = { service: "openai", audio_model: draft.audio_model, revision: draft.revision };
    draft = null;
    return { ok: true, json: async () => ({ active }) };
  }
  throw new Error(`Unexpected request: ${url}`);
};
const window = { v2Page: {
  csrf: "synthetic-csrf", draft: initialDraft, active: null,
  credentials: { openai: { configured: true, credential_version: 2, keys: [
    { version: 1, label: 'Personale' }, { version: 2, label: 'Lavoro' },
  ] } },
}, V2TranscriptionState: helper };
const context = {
  window, document, fetch, FormData, Option: FakeOption, URLSearchParams, console,
  setTimeout, clearTimeout,
};
vm.runInNewContext(fs.readFileSync(`${__dirname}/../../bot/web/static/transcription.js`, "utf8"), context);

async function click(id) {
  for (const callback of elements.get(id).listeners.click || []) await callback({ preventDefault() {} });
}
async function fire(target, name, event = {}) {
  for (const callback of target.listeners[name] || []) await callback({ preventDefault() {}, ...event });
}

(async () => {
  assert.ok(form.listeners.submit, "draft submit handler is wired");
  assert.equal(elements.get('credential-version').value, '1', 'an existing draft keeps its selected key');
  assert.deepEqual(elements.get('credential-version').options.map(option => option.textContent),
    ['Scegli una chiave', 'Personale', 'Lavoro']);
  const runTest = elements.get("run-test");
  const activate = elements.get("activate");
  const file = elements.get("audio-file");
  file.files = [new Blob(["synthetic audio"], { type: "audio/wav" })];
  await fire(file, "change");
  assert.equal(runTest.disabled, false, "saved draft and selected file enable the test");

  elements.get("audio-model-id").value = "audio-v2";
  await fire(form, "input");
  assert.equal(runTest.disabled, true, "editing the form makes its saved revision stale");
  await click("load-models");
  assert.equal(elements.get("audio-model").options.find(option => option.value === "same-id").textContent, "same-id", "equal names must not be repeated");
  elements.get("audio-model-search").value = "same-id";
  await fire(elements.get("audio-model-search"), "input");
  assert.ok(elements.get("audio-model").options.some(option => option.value === "same-id"), "search finds matching models");
  assert.ok(!elements.get("audio-model").options.some(option => option.value === "other"), "search filters unrelated models");
  assert.ok(elements.get("audio-model").options.some(option => option.value === "audio-v2"), "search preserves the current selection");
  assert.equal(runTest.disabled, true, "catalogue completion cannot test dirty visible fields");
  assert.equal(activate.disabled, true, "catalogue completion cannot activate a stale revision");

  await fire(form, "submit");
  assert.equal(draft.revision, 2, `${elements.get("save-status").textContent} requests=${requests.join(",")}`);
  assert.equal(runTest.disabled, false, "saving the visible edits makes them eligible for a test");
  await click("run-test");
  assert.equal(activate.disabled, false, "successful test enables activation");

  const renameInput = elements.get('rename-credential-label');
  renameInput.value = 'Rinominata';
  await fire(form, 'input', { target: renameInput });
  await fire(form, 'change', { target: renameInput });
  await click('rename-credential');
  assert.equal(elements.get('credential-version').value, '1', 'renaming preserves the selected key');
  assert.ok(elements.get('credential-version').options.some(option => option.textContent === 'Rinominata'));
  assert.equal(activate.disabled, false, 'renaming does not invalidate a successful proof');

  await click("activate");
  assert.equal(activate.disabled, false, "failed activation preserves a valid proof for retry");
  await click("activate");
  assert.equal(activationAttempts, 2);
  assert.equal(activate.disabled, true, "successful activation consumes the proof");
  assert.equal(runTest.disabled, true, "activation removes the draft and blocks further tests");
  elements.get("audio-model-id").value = "audio-v3";
  await fire(form, "input");
  await fire(form, "submit");
  assert.equal(draft.revision, 3, "a fresh draft can be created after activation");
  assert.equal(runTest.disabled, false, "the new saved draft re-enables testing");
  elements.get('credential-version').value = '2';
  await fire(form, 'change', { target: elements.get('credential-version') });
  assert.equal(runTest.disabled, true, 'changing keys invalidates the proof until a new draft is saved');
  await fire(form, 'submit');
  assert.equal(draft.credential_version, 2, 'the selected saved key is bound to the new draft');
})().catch((error) => { console.error(error); process.exitCode = 1; });
