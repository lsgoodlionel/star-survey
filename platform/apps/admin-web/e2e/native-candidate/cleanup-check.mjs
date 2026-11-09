/* global process, setTimeout */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { cleanupSteps } from './cleanup.mjs';

test('preview cleanup failure does not suppress project archive or logout', async () => {
  const called = [];
  const actions = {
    closePreview: async () => { called.push('preview'); throw new Error('secret-preview'); },
    archiveProject: async () => { called.push('project'); },
    logout: async () => { called.push('logout'); },
    clearCookies: async () => { called.push('cookies'); },
  };
  assert.equal(await cleanupSteps({ previewPossible: true, projectPossible: true, handoffAttempted: true }, actions), false);
  assert.deepEqual(called, ['preview', 'project', 'logout', 'cookies']);
});

test('uncertain handoff and project creation still require cleanup', async () => {
  const called = [];
  const actions = {
    closePreview: async () => { called.push('preview'); },
    archiveProject: async () => { called.push('project'); throw new Error('handoff-secret'); },
    logout: async () => { called.push('logout'); throw new Error('nonce-secret'); },
    clearCookies: async () => { called.push('cookies'); },
  };
  assert.equal(await cleanupSteps({ previewPossible: false, projectPossible: true, handoffAttempted: true }, actions), false);
  assert.deepEqual(called, ['project', 'logout', 'cookies']);
});

test('only verified successful cleanup returns success', async () => {
  const actions = { closePreview: async () => {}, archiveProject: async () => {}, logout: async () => {}, clearCookies: async () => {} };
  assert.equal(await cleanupSteps({ previewPossible: true, projectPossible: true, handoffAttempted: true }, actions), true);
  actions.clearCookies = async () => { throw new Error('sensitive'); };
  assert.equal(await cleanupSteps({ previewPossible: false, projectPossible: false, handoffAttempted: false }, actions), false);
});

test('click failure and later response rejection are both observed and sanitized', async () => {
  const { pairActionAndResponse } = await import('./cleanup.mjs');
  const unhandled = [];
  const observer = (error) => unhandled.push(error);
  process.on('unhandledRejection', observer);
  try {
    const wait = new Promise((_, reject) => setTimeout(() => reject(new Error('handoff-sensitive')), 30));
    await assert.rejects(() => pairActionAndResponse(wait, async () => { throw new Error('nonce-sensitive'); }),
      (error) => error.message === 'native browser action failed');
    await new Promise((resolve) => setTimeout(resolve, 60));
    assert.deepEqual(unhandled, []);
  } finally { process.off('unhandledRejection', observer); }
});

test('journey failure after uncertain handoff and project request still attempts independent cleanup', async () => {
  const { runWithCleanup } = await import('./cleanup.mjs');
  const called = [];
  const state = { previewPossible: false, projectPossible: false, handoffAttempted: false };
  const actions = {
    closePreview: async () => { called.push('preview'); },
    archiveProject: async () => { called.push('project'); },
    logout: async () => { called.push('logout'); },
    clearCookies: async () => { called.push('cookies'); },
  };
  await assert.rejects(() => runWithCleanup(state, async (s) => {
    s.handoffAttempted = true; s.projectPossible = true;
    throw new Error('sensitive-handoff');
  }, actions), (error) => error.message === 'native preview slice or cleanup failed');
  assert.deepEqual(called, ['project', 'logout', 'cookies']);
});
