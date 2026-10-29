const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() {
    this.listeners = {};
    this.value = '';
    this.disabled = false;
    this.textContent = '';
    this.attributes = {};
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this.attributes[name] = value; }
}

async function pageFor(response) {
  const names = ['password-change', 'password-current', 'password-new', 'password-confirm', 'password-change-submit', 'password-change-status'];
  const elements = Object.fromEntries(names.map(name => [name, new Element()]));
  const requests = [];
  const context = {
    window: {v2AccessPage: {csrf: 'synthetic-csrf'}},
    document: {getElementById: name => elements[name]},
    fetch: async (url, options) => {
      requests.push({url, options});
      return {ok: response.ok, status: response.status, json: async () => response.body};
    },
  };
  vm.runInNewContext(fs.readFileSync('bot/web/static/password_change.js', 'utf8'), context);
  elements['password-current'].value = 'synthetic-current';
  elements['password-new'].value = 'synthetic-replacement';
  elements['password-confirm'].value = 'synthetic-replacement';
  await elements['password-change'].listeners.submit({preventDefault() {}});
  return {elements, requests, window: context.window};
}

(async () => {
  const success = await pageFor({ok: true, status: 200, body: {ok: true, csrf_token: 'renewed-csrf'}});
  assert.equal(success.requests[0].url, '/api/password/change');
  assert.deepEqual(JSON.parse(success.requests[0].options.body), {
    current_password: 'synthetic-current',
    new_password: 'synthetic-replacement',
    confirm_password: 'synthetic-replacement',
  });
  assert.equal(success.requests[0].options.headers['X-CSRF-Token'], 'synthetic-csrf');
  assert.match(success.elements['password-change-status'].textContent, /password aggiornata/i);
  assert.equal(success.elements['password-change-status'].textContent.includes('sessioni'), true);
  assert.equal(success.window.v2AccessPage.csrf, 'renewed-csrf');
  assert.equal(success.elements['password-current'].value, '');
  assert.equal(success.elements['password-new'].value, '');
  assert.equal(success.elements['password-confirm'].value, '');

  const rejected = await pageFor({ok: false, status: 401, body: {error: 'invalid_current_password'}});
  assert.match(rejected.elements['password-change-status'].textContent, /password attuale non è corretta/i);
  assert.equal(rejected.elements['password-change-submit'].disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
