/* global URL, process */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { prepareHandoff, loginWithHandoff } from './handoff.mjs';

const fixture = {
  tenantId: '11111111-1111-4111-8111-111111111111',
  principalId: '22222222-2222-4222-8222-222222222222',
  handoff: 'a'.repeat(43), nonce: 'b'.repeat(43), expiresAt: '2026-01-01T00:00:30Z',
};
const now = Date.parse('2026-01-01T00:00:00Z');

test('builds only the fixed native origin and scoped secure handoff cookie', () => {
  const { cookie, callback } = prepareHandoff(fixture, now);
  assert.equal(cookie.name, 'mjy_org_handoff');
  assert.equal(cookie.domain, 'survey-native.test');
  assert.equal(cookie.path, `/v1/auth/org/${fixture.tenantId}/handoff`);
  assert.equal(cookie.secure, true);
  assert.equal(cookie.httpOnly, true);
  assert.equal(cookie.sameSite, 'Strict');
  assert.equal(new URL(callback).origin, 'https://survey-native.test');
});

test('rejects malformed, expired, overlong and unknown fixture values', () => {
  for (const changed of [
    { tenantId: '../escape' }, { principalId: 'bad' }, { handoff: 'a'.repeat(42) },
    { nonce: 'b'.repeat(44) }, { expiresAt: '2025-12-31T23:59:59Z' },
    { expiresAt: '2026-01-01T00:01:01Z' }, { extra: 'unexpected' },
  ]) assert.throws(() => prepareHandoff({ ...fixture, ...changed }, now));
});

test('uses handoff, validates returned identity and never injects a token', async () => {
  const calls = [];
  const context = { addCookies: async (cookies) => calls.push(['cookie', cookies]) };
  const page = {
    waitForResponse: () => Promise.resolve({ status: () => 200, json: async () => ({ tenantId: fixture.tenantId, actorId: fixture.principalId }) }),
    goto: async (url) => calls.push(['goto', url]),
  };
  await loginWithHandoff(context, page, fixture, now);
  assert.deepEqual(calls.map(([kind]) => kind), ['cookie', 'goto']);
  assert.equal(calls[1][1].startsWith('https://survey-native.test/auth/callback?'), true);
  const bad = { ...page, waitForResponse: () => Promise.resolve({ status: () => 200, json: async () => ({ tenantId: fixture.tenantId, actorId: 'wrong' }) }) };
  await assert.rejects(() => loginWithHandoff(context, bad, fixture, now));
});

test('handoff errors never expose callback or one-time values', async () => {
  const context = { addCookies: async () => {} };
  const page = {
    waitForResponse: () => Promise.reject(new Error(`leaked ${fixture.nonce}`)),
    goto: async () => { throw new Error(`leaked ${fixture.handoff}`); },
  };
  await assert.rejects(() => loginWithHandoff(context, page, fixture, now), (error) => {
    assert.equal(error.message, 'native browser handoff failed');
    assert.equal(String(error).includes(fixture.handoff), false);
    assert.equal(String(error).includes(fixture.nonce), false);
    return true;
  });
});

test('reads only a private regular fixture and rejects symlinks and unsafe modes', async () => {
  const { mkdtemp, writeFile, chmod, symlink, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { execFileSync } = await import('node:child_process');
  const { readHandoffFixture } = await import('./handoff.mjs');
  const dir = await mkdtemp(join(tmpdir(), 'native-handoff-test-'));
  const path = join(dir, 'handoff.json');
  try {
    await writeFile(path, JSON.stringify(fixture), { mode: 0o600 });
    assert.deepEqual(await readHandoffFixture(path, dir, now), fixture);
    await chmod(path, 0o644);
    await assert.rejects(() => readHandoffFixture(path, dir, now));
    await rm(path);
    await symlink(join(dir, 'missing'), path);
    await assert.rejects(() => readHandoffFixture(path, dir, now));
    await rm(path);
    execFileSync('mkfifo', [path]);
    const program = `import {readHandoffFixture} from ${JSON.stringify(new URL('./handoff.mjs', import.meta.url).href)};
      try { await readHandoffFixture(process.argv[1], process.argv[2], ${now}); process.exit(1); }
      catch { process.exit(0); }`;
    execFileSync(process.execPath, ['--input-type=module', '-e', program, path, dir], { timeout: 3_000 });
    await assert.rejects(() => readHandoffFixture(join(dir, '..', 'handoff.json'), dir, now));
  } finally { await rm(dir, { recursive: true, force: true }); }
});
