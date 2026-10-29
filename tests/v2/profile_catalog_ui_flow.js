"use strict";

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const scriptPath = process.argv[2];
const source = fs.readFileSync(scriptPath, 'utf8');
const elements = new Map();
function element(id) {
  const item = {
    id, value: '', checked: false, disabled: false, hidden: false, textContent: '',
    children: [], options: [], listeners: {}, files: [], className: '',
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); },
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; this.options = nodes; },
    add(node) { this.options.push(node); },
    focus() { this.focused = true; },
    showModal() { this.open = true; },
    close() { this.open = false; },
    reset() {},
  };
  elements.set(id, item);
  return item;
}
const ids = [
  'profile-list', 'profile-status', 'profile-editor', 'draft-form', 'profile-name',
  'profile-confirmation', 'profile-delete-confirmation', 'profile-confirmation-title',
  'profile-confirmation-copy', 'profile-unverified-wrap', 'profile-unverified',
  'profile-confirm-action', 'profile-delete-copy', 'profile-delete-action', 'sample-description',
  'run-test', 'activate', 'active-summary', 'create-profile', 'service', 'credential-version',
  'audio-model-id', 'text-model-id', 'system-prompt', 'refine-template', 'max-file-size',
  'max-user', 'max-global', 'cooldown', 'transcribe-timeout', 'refine-timeout', 'queue-enabled',
  'active-title', 'draft-title', 'save-draft', 'test-status', 'audio-file', 'result-text',
  'result-meta', 'test-result', 'test-diagnostics', 'credential-state', 'credential-label',
  'credential', 'rename-credential-label', 'rename-credential', 'save-credential',
  'load-models', 'load-text-models', 'text-model-wrap', 'draft-state', 'save-status',
  'max-label', 'audio-model-current', 'text-model-current', 'audio-model-description',
  'text-model-description', 'model-picker', 'restore',
];
ids.forEach(element);
const modeMultimodal = element('mode-multimodal');
modeMultimodal.value = 'multimodal'; modeMultimodal.checked = true;
const modeTwoStage = element('mode-two_stage');
modeTwoStage.value = 'two_stage';
const sourceSample = element('source-sample'); sourceSample.value = 'sample'; sourceSample.checked = true;
const sourceUpload = element('source-upload'); sourceUpload.value = 'upload';
const workbench = element('workbench');
const draftForm = elements.get('draft-form');
const document = {
  querySelector(selector) {
    if (selector === '.v2-columns') return workbench;
    if (selector === 'input[name="mode"]:checked') return modeMultimodal.checked ? modeMultimodal : modeTwoStage;
    if (selector === 'input[name="test-source"]:checked') return sourceSample.checked ? sourceSample : sourceUpload;
    const modeMatch = selector.match(/^input\[name="mode"\]\[value="(.+)"\]$/);
    if (modeMatch) return modeMatch[1] === 'multimodal' ? modeMultimodal : modeTwoStage;
    return elements.get(selector.replace(/^#/, ''));
  },
  querySelectorAll(selector) {
    if (selector === 'input[name="test-source"]') return [sourceSample, sourceUpload];
    return [];
  },
  createElement(tag) { return element('created-' + tag + '-' + Math.random()); },
};
class FakeOption { constructor(label, value) { this.textContent = label; this.value = value; } }
const initial = {
  id: 'profile-one', name: '<Profilo>', revision: 3, generation: 5, service: 'openai',
  mode: 'multimodal', audio_model: 'draft-model', text_model: null,
  role: 'fallback', role_revision: 2, role_values: { service: 'openai', mode: 'multimodal', audio_model: 'role-model', text_model: null },
  verification: 'retest_required', role_verification: 'retest_required', last_test: null,
};
let profile = { ...initial };
let rolePayload = null;
const roleCalls = [];
let rolesGeneration = 8;
const fetch = async (url, options = {}) => {
  if (url === '/api/profiles') return { ok: true, json: async () => ({ profiles: [profile], roles_generation: rolesGeneration, sample: { duration_seconds: 1.8, sha256: 'synthetic-hash' } }) };
  if (url === '/api/profiles/profile-one') return { ok: true, json: async () => ({ profile: { ...profile, values: {
    service: 'openai', mode: 'multimodal', audio_model: 'audio-model', text_model: null,
    credential_version: 1, system_prompt: 'Keep it faithful.', refine_template: '{raw_text}',
    limits: { max_file_size_mb: 2, max_per_user: 2, max_concurrent_global: 6,
      cooldown_seconds: 30, transcribe_timeout: 120, refine_timeout: 90, queue_enabled: true },
  } } }) };
  if (url === '/api/profiles/roles/fallback/clear') {
    roleCalls.push([url, JSON.parse(options.body)]);
    profile = { ...profile, role: null, verification: 'unverified' };
    rolesGeneration = 9;
    return { ok: true, json: async () => ({ roles_generation: rolesGeneration }) };
  }
  if (url === '/api/profiles/roles/primary/restore') {
    roleCalls.push([url, JSON.parse(options.body)]);
    rolesGeneration = 10;
    return { ok: true, json: async () => ({ roles_generation: 10, restored: false }) };
  }
  if (url.endsWith('/role')) {
    rolePayload = JSON.parse(options.body);
    rolesGeneration = rolePayload.expected_generation + 1;
    profile = { ...profile, role: rolePayload.role };
    return { ok: true, json: async () => ({ profile, roles_generation: rolesGeneration }) };
  }
  throw new Error('Unexpected request ' + url);
};
const window = { v2Page: { csrf: 'synthetic-csrf', credentials: {} }, prompt: () => null, confirm: () => true };
vm.runInNewContext(source, { window, document, fetch, Option: FakeOption, FormData, Date, URLSearchParams, console });

async function run() {
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements.get('profile-list').children.length, 1, 'catalog renders a profile row');
  const card = elements.get('profile-list').children[0];
  assert.ok(card.children[0].children[0].children[0].textContent === '<Profilo>', 'profile names are assigned as text');
  assert.match(card.children[0].children[0].children[1].textContent, /role-model/, 'assigned runtime summary uses role_values');
  assert.doesNotMatch(card.children[0].children[0].children[1].textContent, /draft-model/, 'draft values are not shown as active');
  assert.match(card.children[0].children[0].children[2].textContent, /revisione 2/);
  assert.match(card.children[0].children[0].children[3].textContent, /salvata 3.*usa la revisione 2/);
  assert.match(elements.get('sample-description').textContent, /1\.8 secondi/);
  const edit = card.children[1].children.find(button => button.textContent === 'Modifica');
  await edit.listeners.click[0]();
  assert.equal(elements.get('run-test').disabled, false, 'opening a clean saved profile enables its default sample test');
  assert.equal(elements.get('activate').disabled, false, 'opening a clean saved profile enables the role action');
  const fallbackAction = card.children[1].children.find(button => button.textContent === 'Imposta fallback');
  await fallbackAction.listeners.click[0]();
  assert.equal(elements.get('profile-confirm-action').disabled, true, 'an unverified profile cannot be assigned as fallback');
  assert.match(elements.get('profile-confirmation-copy').textContent, /prova valida/i, 'fallback confirmation explains its test prerequisite');
  const actions = card.children[1].children;
  assert.ok(card.children[0].children[0].children.some(node => /prova da ripetere/.test(node.textContent)), 'assigned roles display role_verification instead of the general test state');
  const clear = actions.find(button => button.textContent === 'Rimuovi fallback');
  await clear.listeners.click[0]();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(roleCalls[0], ['/api/profiles/roles/fallback/clear', { expected_generation: 8 }]);
  await elements.get('restore').listeners.click[0]({ preventDefault() {}, stopImmediatePropagation() {} });
  assert.deepEqual(roleCalls[1], ['/api/profiles/roles/primary/restore', { expected_generation: 9 }]);
  assert.match(elements.get('profile-status').textContent, /Non esiste/);
  await new Promise(resolve => setImmediate(resolve));
  const refreshedCard = elements.get('profile-list').children[0];
  const refreshedActions = refreshedCard.children[1].children;
  const mainAction = refreshedActions.find(button => button.textContent === 'Imposta principale');
  await mainAction.listeners.click[0]();
  assert.equal(elements.get('profile-confirmation').open, true, 'main activation requires a confirmation dialog');
  assert.equal(elements.get('profile-confirm-action').disabled, true, 'unverified main activation requires an explicit checkbox');
  elements.get('profile-unverified').checked = true;
  elements.get('profile-unverified').listeners.change[0]({ target: elements.get('profile-unverified') });
  assert.equal(elements.get('profile-confirm-action').disabled, false);
  await elements.get('profile-confirm-action').listeners.click[0]({ preventDefault() {} });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(rolePayload, { role: 'main', expected_generation: 10, confirm_unverified: true });
  assert.match(elements.get('active-summary').textContent, /Principale|Profilo/);
}
run().catch(error => { console.error(error); process.exitCode = 1; });
