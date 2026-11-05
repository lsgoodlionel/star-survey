/* global process, URL, Buffer */
// Test-only helper for the real browser-bound handoff; never use a JWT or /dev/token.
import { constants } from 'node:fs';
import { lstat, open } from 'node:fs/promises';
import { isAbsolute, join, resolve } from 'node:path';
const ORIGIN = 'https://survey-native.test';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const OPAQUE = /^[A-Za-z0-9_-]{43}$/;

export async function readHandoffFixture(path, work, now = Date.now()) {
  if (!isAbsolute(work) || path !== join(work, 'handoff.json') || resolve(work) !== work)
    throw new Error('invalid native handoff path');
  let file;
  try {
    const dir = await lstat(work);
    if (!dir.isDirectory() || dir.isSymbolicLink() || (dir.mode & 0o777) !== 0o700 ||
        dir.uid !== process.geteuid()) throw new Error('unsafe directory');
    file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const info = await file.stat();
    if (!info.isFile() || (info.mode & 0o777) !== 0o600 || info.uid !== process.geteuid() || info.size > 4096)
      throw new Error('unsafe fixture');
    const buffer = Buffer.alloc(4097);
    const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
    if (bytesRead > 4096) throw new Error('oversized fixture');
    const fixture = JSON.parse(buffer.subarray(0, bytesRead).toString('utf8'));
    prepareHandoff(fixture, now);
    return fixture;
  } catch {
    throw new Error('native handoff fixture cannot be read');
  } finally {
    if (file) await file.close();
  }
}

export function prepareHandoff(fixture, now = Date.now()) {
  if (!fixture || typeof fixture !== 'object' || Array.isArray(fixture) ||
      Object.keys(fixture).sort().join(',') !== 'expiresAt,handoff,nonce,principalId,tenantId' ||
      typeof fixture.tenantId !== 'string' || !UUID.test(fixture.tenantId) ||
      typeof fixture.principalId !== 'string' || !UUID.test(fixture.principalId) ||
      typeof fixture.handoff !== 'string' || !OPAQUE.test(fixture.handoff) ||
      typeof fixture.nonce !== 'string' || !OPAQUE.test(fixture.nonce) ||
      typeof fixture.expiresAt !== 'string') throw new Error('invalid native handoff fixture');
  const expires = Date.parse(fixture.expiresAt);
  if (!Number.isFinite(expires) || !Number.isFinite(now) || expires <= now || expires - now > 60_000)
    throw new Error('expired or invalid native handoff fixture');
  const callback = new URL('/auth/callback', ORIGIN);
  callback.searchParams.set('tenant', fixture.tenantId);
  callback.searchParams.set('handoff', fixture.handoff);
  return {
    callback: callback.href,
    cookie: {
      name: 'mjy_org_handoff', value: fixture.nonce, domain: 'survey-native.test',
      path: `/v1/auth/org/${fixture.tenantId}/handoff`, httpOnly: true, secure: true,
      sameSite: 'Strict', expires: Math.floor(expires / 1000),
    },
  };
}

export async function loginWithHandoff(context, page, fixture, now = Date.now()) {
  const { cookie, callback } = prepareHandoff(fixture, now);
  try {
    await context.addCookies([cookie]);
    const [response] = await Promise.all([
      page.waitForResponse((r) => new URL(r.url()).pathname === '/v1/me' && r.request().method() === 'GET', { timeout: 30_000 }),
      page.goto(callback, { waitUntil: 'domcontentloaded', timeout: 30_000 }),
    ]);
    if (response.status() !== 200) throw new Error('handoff identity request failed');
    const me = await response.json();
    if (me?.tenantId !== fixture.tenantId || me?.actorId !== fixture.principalId)
      throw new Error('handoff identity mismatch');
  } catch {
    // Browser errors can contain the one-use handoff URL; never emit them.
    throw new Error('native browser handoff failed');
  }
}
