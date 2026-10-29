"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const page = process.argv[2];
const helperPath = process.argv[3];
const catalogPath = process.argv[4];
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
assert.match(html, /<dialog id="model-picker"/);
assert.match(html, /\{raw_text\}<\/textarea>/);
const elements = new Map();
function element(id, defaults = {}) {
  const item = {
    id, value: "", disabled: false, hidden: false, checked: false,
    textContent: "", files: [], elements: [], listeners: {}, options: [], children: [], attributes: {}, className: "",
    addEventListener(name, callback, options = false) { (this.listeners[name] ||= []).push({ callback, capture: options === true || options?.capture === true }); },
    replaceChildren(...children) { this.options = children; this.children = children; },
    append(...children) { this.children.push(...children); },
    add(option) { this.options.push(option); },
    setAttribute(name, value) { this.attributes[name] = value; },
    focus() { this.focused = true; },
    showModal() { this.open = true; },
    close() { this.open = false; for (const listener of this.listeners.close || []) listener.callback(); },
    classList: { add(value) { this.owner.className += ` ${value}`; } },
    ...defaults,
  };
  item.classList.owner = item;
  elements.set(id, item);
  return item;
}
const ids = [
  "service", "draft-form", "credential", "credential-label", "credential-version", "rename-credential-label", "rename-credential", "audio-model-id",
  "text-model-id", "text-model-wrap", "system-prompt",
  "refine-template", "max-file-size", "max-user", "max-global", "cooldown",
  "transcribe-timeout", "refine-timeout", "queue-enabled", "draft-state", "save-status",
  "save-credential", "credential-state", "load-models", "load-text-models", "audio-file",
  "run-test", "test-status", "test-result", "test-diagnostics", "test-steps", "result-text", "result-meta", "activate",
  "restore", "active-summary", "max-label", "audio-model-current", "text-model-current",
  "audio-model-description", "text-model-description", "model-picker", "model-picker-title",
  "model-picker-role", "model-picker-search", "model-provider-filter", "model-provider-filter-wrap",
  "model-price-controls", "model-sort", "model-free-prompt", "model-free-completion",
  "model-context-filter", "model-picker-status", "model-picker-results", "model-picker-more",
  "profile-list", "profile-status", "profile-name", "profile-confirmation", "profile-delete-confirmation",
  "profile-confirmation-title", "profile-confirmation-copy", "profile-unverified-wrap", "profile-unverified",
  "profile-confirm-action", "profile-delete-copy", "profile-delete-action", "sample-description",
  "create-profile", "profile-editor", "test-result", "test-diagnostics", "test-steps",
];
ids.forEach((id) => element(id));
element("save-draft");
const radioModes = [
  { value: "multimodal", checked: true }, { value: "two_stage", checked: false },
].map((mode) => ({ ...element(`mode-${mode.value}`), ...mode }));
const buttons = ["save-draft", "save-credential", "rename-credential", "load-models", "load-text-models", "run-test", "activate", "restore"]
  .map((id) => elements.get(id));
const form = elements.get("draft-form");
elements.set('profile-editor', elements.get('profile-editor'));
form.elements = [elements.get("service"), elements.get("credential"), elements.get("credential-version"),
  elements.get("audio-model-id"), elements.get("text-model-id"), elements.get("system-prompt"),
  elements.get("refine-template"), elements.get("max-file-size"), elements.get("max-user"),
  elements.get("max-global"), elements.get("cooldown"), elements.get("transcribe-timeout"),
  elements.get("refine-timeout"), elements.get("queue-enabled")];
const document = {
  querySelector(selector) {
    if (selector === '.v2-columns') return elements.get('profile-editor');
    if (selector === 'input[name="mode"]:checked') return radioModes.find((radio) => radio.checked);
    if (selector === 'input[name="test-source"]:checked') return testSources.find(radio => radio.checked);
    const radioMatch = selector.match(/^input\[name="mode"\]\[value="(.+)"\]$/);
    if (radioMatch) return radioModes.find((radio) => radio.value === radioMatch[1]);
    return elements.get(selector.replace(/^#/, ""));
  },
  querySelectorAll(selector) {
    if (selector === ".v2-main button") return buttons;
    if (selector === 'input[name="test-source"]') return testSources;
    return [];
  },
  createElement(tag) { return element(`created-${tag}-${Math.random()}`); },
};
const testSources = [
  { ...element('test-source-sample'), value: 'sample', checked: true },
  { ...element('test-source-upload'), value: 'upload', checked: false },
];
class FakeOption {
  constructor(label, value) { this.textContent = label; this.value = value; }
}
let draft = initialDraft;
let activationAttempts = 0;
const profile = { id: 1, name: 'Runtime profile', revision: 1, generation: 1,
  role: null, verification: 'verified', service: 'openai', mode: 'multimodal',
  audio_model: 'audio-v1', text_model: null };
const requests = [];
let nextRevision = initialDraft.revision;
let deferredCatalogResponse = null;
const fetch = async (url, options = {}) => {
  requests.push(url);
  if (url === '/api/profiles') return { ok: true, json: async () => ({ profiles: [profile], roles_generation: 0, sample: { duration_seconds: 1.8, sha256: 'synthetic-hash' } }) };
  if (url === '/api/profiles/1') return { ok: true, json: async () => ({ profile: { ...profile, values: {
    service: 'openai', mode: 'multimodal', audio_model: 'audio-v1', text_model: null,
    credential_version: 1, system_prompt: 'faithful', refine_template: '{raw_text}',
    limits: initialDraft.limits,
  } } }) };
  if (url === '/api/profiles/1/test') return { ok: true, json: async () => ({
    status: 'succeeded', revision: 1, result: null,
  }) };
  if (url === '/api/transcription/credentials') return { ok: true, json: async () => ({
    credential_version: 3,
    keys: [{ version: 1, label: 'Personale' }, { version: 2, label: 'Lavoro' }, { version: 3, label: 'Nuova' }],
  }) };
  if (url === '/api/transcription/credentials/openai/1/label') {
    return { ok: true, json: async () => ({ keys: [{version: 1, label: 'Rinominata'}, {version: 2, label: 'Lavoro'}] }) };
  }
  if (url.startsWith("/api/transcription/models?")) {
    if (deferredCatalogResponse) {
      const response = deferredCatalogResponse;
      deferredCatalogResponse = null;
      return response;
    }
    return { ok: true, json: async () => ({ models: [
      { id: "openai/audio-v2", label: "Audio model", pricing: { prompt: "0.000002", completion: "0.000003", input_audio: "0.00004" }, context_length: 32000 },
      { id: "google/same-id", label: "same-id", pricing: { prompt: "0", completion: "0" }, context_length: 128000 },
      { id: "anthropic/other", label: "Another model", pricing: { prompt: "0.000001", completion: "0.000009" }, context_length: 8192 },
      { id: "meta/unknown-price", label: "Unknown", context_length: 64000 },
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
class FakeXMLHttpRequest {
  constructor() { this.listeners = {}; this.upload = { addEventListener() {} }; }
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader() {}
  addEventListener(name, callback) { this.listeners[name] = callback; }
  send() {
    Promise.resolve().then(async () => {
      const response = await fetch(this.url, { method: this.method });
      this.status = response.ok ? 200 : 400;
      this.responseText = JSON.stringify(await response.json());
      this.listeners.load?.();
    }).catch(() => this.listeners.error?.());
  }
}
const window = { v2Page: {
  csrf: "synthetic-csrf", draft: initialDraft, active: null,
  credentials: { openai: { configured: true, credential_version: 2, keys: [
    { version: 1, label: 'Personale' }, { version: 2, label: 'Lavoro' },
  ] } },
}, V2TranscriptionState: helper, prompt: () => null };
const context = {
  window, document, fetch, FormData, XMLHttpRequest: FakeXMLHttpRequest, Option: FakeOption, URLSearchParams, console,
  setTimeout, clearTimeout,
};
vm.runInNewContext(fs.readFileSync(`${__dirname}/../../bot/web/static/transcription.js`, "utf8"), context);
if (catalogPath) vm.runInNewContext(fs.readFileSync(catalogPath, "utf8"), context);

async function click(id) {
  const target = elements.get(id);
  const listeners = [...(target.listeners.click || [])].sort((a, b) => Number(b.capture) - Number(a.capture));
  const event = { preventDefault() {}, stopImmediatePropagation() { this.stopped = true; }, currentTarget: target };
  for (const listener of listeners) { await listener.callback(event); if (event.stopped) break; }
}
async function fire(target, name, event = {}) {
  const listeners = [...(target.listeners[name] || [])].sort((a, b) => Number(b.capture) - Number(a.capture));
  const payload = { preventDefault() {}, stopImmediatePropagation() { this.stopped = true; }, ...event };
  for (const listener of listeners) {
    await listener.callback(payload);
    if (payload.stopped) break;
  }
}
const runTest = elements.get('run-test');
const activate = elements.get('activate');
const file = elements.get('audio-file');

(async () => {
  if (catalogPath) {
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(elements.get('profile-list').children.length, 1, 'catalog script loads alongside the existing workbench');
    file.files = [new Blob(['synthetic audio'], { type: 'audio/wav' })];
    await fire(file, 'change');
    assert.equal(runTest.disabled, true, 'legacy draft state cannot enable the profile test before profile selection');
    await click('run-test');
    await click('activate');
    await fire(form, 'submit');
    assert.equal(requests.some(url => ['/api/transcription/test', '/api/transcription/activate', '/api/transcription/draft'].includes(url)), false,
      'profile mode suppresses all legacy submit, test and activation requests');
    const profileCard = elements.get('profile-list').children[0];
    const edit = profileCard.children[1].children.find(button => button.textContent === 'Modifica');
    await edit.listeners.click[0].callback();
    await new Promise(resolve => setImmediate(resolve));
    window.V2ProfileCatalog.updateActions();
    assert.equal(runTest.disabled, false, 'opening a saved profile enables its default sample test');
    await click('run-test');
    assert.ok(requests.includes('/api/profiles/1/test'), 'sample test uses the revision-bound profile route');
    elements.get('audio-model-id').value = 'unsaved-model';
    await fire(form, 'input', { target: elements.get('audio-model-id') });
    assert.equal(activate.disabled, true, 'dirty profile edits keep role activation visibly disabled');
    await click('activate');
    assert.equal(requests.some(url => url.endsWith('/role')), false, 'dirty profile cannot be assigned');
    elements.get('credential').value = 'synthetic-provider-key';
    await click('save-credential');
    assert.equal(window.v2Page.credentials.openai.credential_version, 3, 'credential updates remain visible to the profile editor without reload');
    const refreshedCard = elements.get('profile-list').children[0];
    await refreshedCard.children[1].children.find(button => button.textContent === 'Modifica').listeners.click[0].callback();
    assert.ok(elements.get('credential-version').options.some(option => option.textContent === 'Nuova'), 'profile editor reads the updated shared key catalog');
    await click('load-models');
    assert.equal(elements.get('model-picker').open, true, 'model catalog remains available in profile mode');
    return;
  }
  assert.ok(form.listeners.submit, "draft submit handler is wired");
  assert.equal(elements.get('credential-version').value, '1', 'an existing draft keeps its selected key');
  assert.equal(elements.get('audio-model-current').textContent, 'audio-v1', 'existing model ID is shown as the current selection');
  elements.get('service').value = 'openrouter';
  await fire(elements.get('service'), 'change');
  assert.equal(elements.get('model-price-controls').hidden, false, 'price controls appear for OpenRouter');
  elements.get('service').value = 'openai';
  await fire(elements.get('service'), 'change');
  elements.get('credential-version').value = '1';
  assert.deepEqual(elements.get('credential-version').options.map(option => option.textContent),
    ['Scegli una chiave', 'Personale', 'Lavoro']);
  file.files = [new Blob(["synthetic audio"], { type: "audio/wav" })];
  await fire(file, "change");
  assert.equal(runTest.disabled, false, "saved draft and selected file enable the test");

  elements.get("audio-model-id").value = "openai/audio-v2";
  await fire(form, "input");
  assert.equal(runTest.disabled, true, "editing the form makes its saved revision stale");
  elements.get('service').value = 'openrouter';
  await click("load-models");
  const results = elements.get('model-picker-results');
  assert.equal(elements.get('model-picker').open, true, 'native dialog opens for catalog search');
  assert.equal(results.children.length, 4, 'catalog models render as selectable result cards');
  const textOf = node => [node.textContent, ...(node.children || []).map(textOf)].join(' ');
  assert.match(textOf(results.children[1]), /same-id.*google\/same-id.*Prompt testuale.*\$0.*Output token.*\$0/s, 'model name, ID and separate prices are readable');
  assert.match(textOf(results.children[3]), /Non disponibile/, 'missing prices are never rendered as free');
  assert.equal(elements.get('model-provider-filter').options.length, 5, 'provider filter derives namespaces from OpenRouter model IDs');
  elements.get("model-picker-search").value = "same-id";
  await fire(elements.get("model-picker-search"), "input");
  assert.equal(results.children.length, 2, "search finds a result while retaining the current selection");
  assert.match(textOf(results.children[0]), /Selezionato · fuori dai filtri/, "the retained selection is marked when it misses the search");
  elements.get('model-picker-search').value = '';
  elements.get('model-sort').value = 'prompt';
  await fire(elements.get('model-sort'), 'change');
  assert.deepEqual(results.children.map(card => card.children[0].children[1].textContent),
    ['google/same-id', 'anthropic/other', 'openai/audio-v2', 'meta/unknown-price'], 'price sorting leaves unknown prices last');
  elements.get('model-free-prompt').checked = true;
  await fire(elements.get('model-free-prompt'), 'change');
  assert.deepEqual(results.children.map(card => card.children[0].children[1].textContent),
    ['google/same-id', 'openai/audio-v2'], 'free filter excludes paid and unknown prices while retaining the selected ID');
  assert.equal(elements.get('activate').disabled, true, 'catalog browsing does not restore a stale proof');
  const selection = results.children.find(card => card.children[0].children[1].textContent === 'google/same-id');
  await fire(selection.children[2], 'click');
  assert.equal(elements.get('audio-model-id').value, 'google/same-id', 'explicit card choice updates the draft model ID');
  assert.equal(elements.get('audio-model-current').textContent, 'google/same-id', 'explicit choice updates the visible selection summary');
  assert.equal(elements.get('model-picker').open, false, 'choosing a model closes the dialog');
  assert.equal(runTest.disabled, true, "catalogue completion cannot test dirty visible fields");
  assert.equal(activate.disabled, true, "catalogue completion cannot activate a stale revision");
  elements.get('service').value = 'openai';

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

  let resolveStaleCatalog;
  deferredCatalogResponse = new Promise(resolve => { resolveStaleCatalog = resolve; });
  elements.get('service').value = 'openrouter';
  await fire(elements.get('service'), 'change');
  const openButton = elements.get('load-models');
  const staleRequest = openButton.listeners.click[0].callback({ currentTarget: openButton });
  assert.equal(elements.get('model-picker').open, true, 'first catalog request opens its dialog');
  elements.get('model-picker').close();
  elements.get('service').value = 'gemini';
  await fire(elements.get('service'), 'change');
  await click('load-models');
  resolveStaleCatalog({ ok: true, json: async () => ({ models: [{ id: 'stale/old-service', label: 'Stale response' }] }) });
  await staleRequest;
  assert.doesNotMatch(textOf(elements.get('model-picker-results')), /stale\/old-service/, 'late catalog responses cannot replace a newer service result');
})().catch((error) => { console.error(error); process.exitCode = 1; });
