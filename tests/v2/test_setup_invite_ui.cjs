const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('bot/web/templates/v2_setup.html', 'utf8');
const script = source.match(/<script>(const csrf=.*?)<\/script>/s)[1].replace('{{ csrf_token|tojson }}', '"csrf"');
const elements = {};
for (const name of ['telegram', 'token', 'telegram-result', 'invite', 'invite-result', 'candidate', 'candidate-name', 'confirm']) {
  elements[name] = { hidden: name === 'candidate', textContent: '', innerHTML: '', addEventListener() {} };
}
let count = 0;
let releaseOld;
const pendingOld = new Promise(resolve => { releaseOld = resolve; });
const response = (ok, data) => ({ok, json: async () => data});
const fetch = async (url, options = {}) => {
  if (url === '/api/access/invites' && options.method === 'POST') {
    count++;
    return response(true, {id: `invite-${count}`, link: 'https://t.me/test'});
  }
  if (url === '/api/access/invites/invite-1') return pendingOld;
  if (url === '/api/access/invites/invite-2') return response(false, {});
  throw new Error(`unexpected request ${url}`);
};
const context = {document: {querySelector: selector => elements[selector.slice(1)]}, fetch, setTimeout() {}, encodeURIComponent};
vm.runInNewContext(script, context);
(async () => {
  await elements.invite.onclick();
  await elements.invite.onclick();
  releaseOld(response(true, {candidate_name: 'Old candidate', candidate_id: 1}));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(elements.candidate.hidden, true, 'a stale invitation must not display a confirmation');
})().catch(error => { console.error(error); process.exitCode = 1; });
