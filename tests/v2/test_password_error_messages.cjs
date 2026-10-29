const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const window = {};
vm.runInNewContext(fs.readFileSync('bot/web/static/password_errors.js', 'utf8'), {window});
assert.match(window.v2PasswordError('password_too_long'), /72 byte/i);
assert.match(window.v2PasswordError('password_invalid'), /caratteri non supportati/i);
assert.match(window.v2PasswordError('invalid_or_expired_code'), /codice/i);
assert.match(window.v2PasswordError('unknown'), /riprova/i);
