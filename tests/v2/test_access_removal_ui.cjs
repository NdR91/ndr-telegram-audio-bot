const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.value = '';
  }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  querySelector(selector) {
    if (selector === 'span') return this.children.find(child => child.tagName === 'span');
    return null;
  }
}

async function exerciseRemoval(kind, id, label, accepted) {
  const names = [
    'access-status', 'access-list-status', 'owners', 'users', 'groups',
    'refresh-access', 'manual-access', 'entry-kind', 'entry-id', 'invite-kind',
    'create-invite', 'invite-panel', 'invite-link', 'invite-status', 'candidate',
    'confirm-invite',
  ];
  const elements = Object.fromEntries(names.map(name => [name, new Element()]));
  const removed = [];
  const confirmations = [];
  const data = {
    owner: kind === 'owner' ? [id] : [],
    users: kind === 'user' ? [id] : [],
    groups: kind === 'group' ? [{id, last_received: null}] : [],
    labels: {[kind]: {[String(id)]: label}},
  };
  const fetch = async (url, options = {}) => {
    if (url === '/api/access' && !options.method) return {ok: true, json: async () => data};
    if (url.startsWith('/api/access/entries/') && options.method === 'DELETE') {
      removed.push(url);
      return {ok: true, json: async () => ({ok: true})};
    }
    throw new Error(`unexpected request ${url}`);
  };
  const context = {
    window: {
      v2AccessPage: {csrf: 'synthetic-csrf'},
      confirm: message => { confirmations.push(message); return accepted; },
    },
    document: {
      getElementById: name => elements[name],
      createElement: tag => new Element(tag),
    },
    fetch,
    Date,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(fs.readFileSync('bot/web/static/access.js', 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  const list = elements[kind === 'owner' ? 'owners' : kind === 'user' ? 'users' : 'groups'];
  const row = list.children.find(child => child.tagName === 'li');
  const remove = row.children.find(child => child.textContent === 'Rimuovi');
  await remove.listeners.click();
  return {confirmations, removed, status: elements['access-status'].textContent};
}

(async () => {
  const declined = await exerciseRemoval('group', -10042, 'Famiglia', false);
  assert.match(declined.confirmations[0], /Famiglia.*-10042/);
  assert.match(declined.confirmations[0], /tutti i membri/i);
  assert.match(declined.confirmations[0], /nuovi vocali/i);
  assert.deepEqual(declined.removed, [], 'declining confirmation leaves ACL unchanged');
  assert.match(declined.status, /annullat/i, 'cancellation is announced');

  const accepted = await exerciseRemoval('user', 42, 'Ada', true);
  assert.match(accepted.confirmations[0], /Ada.*42/);
  assert.match(accepted.confirmations[0], /audio in privato/i);
  assert.deepEqual(accepted.removed, ['/api/access/entries/user/42']);
  assert.match(accepted.status, /Ada.*ID 42/);
  assert.match(accepted.status, /accesso rimosso/i, 'success feedback identifies the removed access');
})().catch(error => { console.error(error); process.exitCode = 1; });
