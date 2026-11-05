/* global process, URL, window, PopStateEvent */
import { randomBytes } from 'node:crypto';
import { unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { test, expect } from '@playwright/test';
import { loginWithHandoff, readHandoffFixture } from './handoff.mjs';
import { pairActionAndResponse, runWithCleanup } from './cleanup.mjs';

// This is a test-only preview slice, not a full author-journey acceptance result.
// The supervisor must independently establish the isolated candidate and cleanup.
test('native candidate author can create and close an isolated preview', async ({ page, context }) => {
  const env = process.env;
  if (env.GITHUB_ACTIONS !== 'true' || env.RUNNER_ENVIRONMENT !== 'github-hosted' ||
      env.GITHUB_REPOSITORY !== 'lsgoodlionel/star-survey' || env.SURVEY_NATIVE_ACCEPTANCE !== '1' ||
      !env.NATIVE_AUTHOR_FIXTURE_DIR) throw new Error('native author fixture unavailable');
  const fixturePath = join(env.NATIVE_AUTHOR_FIXTURE_DIR, 'handoff.json');
  const fixture = await readHandoffFixture(fixturePath, env.NATIVE_AUTHOR_FIXTURE_DIR);
  await unlink(fixturePath);
  const projectName = `native-author-${randomBytes(8).toString('hex')}`;
  const state = { previewPossible: false, projectPossible: false, handoffAttempted: false };
  await runWithCleanup(state, async (current) => {
    current.handoffAttempted = true; // A session may exist even if /v1/me subsequently fails.
    await loginWithHandoff(context, page, fixture);
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 30_000 });
    await page.getByRole('link', { name: '项目与问卷' }).click();
    await expect(page).toHaveURL(/\/workspace$/);
    current.projectPossible = true; // A failed or timed-out POST can still have created the project.
    await create(page, '新建项目', '创建项目', projectName, '/v1/projects');
    await resource(page, projectName).click();
    await create(page, '新建问卷', '创建问卷', 'Native author test', '/v1/surveys');
    await expect(page).toHaveURL(/\/surveys\/[0-9a-f-]{36}\/edit$/i);
    await page.getByLabel('题目文本').fill('Native isolated preview question');
    await page.getByLabel('标题').fill('Native isolated author');
    await page.getByRole('button', { name: '保存草稿' }).click();
    await expect(page.getByText(/已保存版本 \d+/)).toBeVisible();
    await page.getByRole('link', { name: '快速预览' }).click();
    current.previewPossible = true;
    await page.getByRole('button', { name: '创建真实预览' }).click();
    await expect(page.getByText('真实预览已就绪')).toBeVisible({ timeout: 210_000 });
    await closePreview(page);
    current.previewPossible = false;
  }, {
    closePreview: () => closePreview(page),
    archiveProject: () => archiveProject(page, projectName),
    logout: () => logout(page),
    clearCookies: () => context.clearCookies(),
  });
});

function resource(page, name) {
  return page.getByRole('list', { name: '当前位置资源' }).getByRole('button', { name, exact: true });
}

async function closePreview(page) {
  const end = page.getByRole('button', { name: '结束真实预览' });
  await expect(end).toBeVisible({ timeout: 15_000 });
  await end.click();
  await expect(page.getByText('真实预览已结束')).toBeVisible({ timeout: 210_000 });
}

async function archiveProject(page, name) {
  await page.evaluate(() => { window.history.pushState({}, '', '/workspace'); window.dispatchEvent(new PopStateEvent('popstate')); });
  // If the create outcome was uncertain and the project is absent, fail rather than assert cleanup.
  await expect(resource(page, name)).toBeVisible({ timeout: 15_000 });
  await resource(page, name).click();
  await page.getByRole('button', { name: '归档', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '归档资源' });
  await dialog.getByRole('button', { name: '确认归档' }).click();
  await expect(dialog).toBeHidden();
  await expect(resource(page, name)).toHaveCount(0);
}

async function logout(page) {
  const response = await pairActionAndResponse(
    page.waitForResponse((r) => new URL(r.url()).pathname === '/v1/auth/logout' &&
      r.request().method() === 'POST', { timeout: 15_000 }),
    () => page.getByRole('button', { name: '退出登录' }).click(),
  );
  if (response.status() !== 204) throw new Error('native logout failed');
}

async function create(page, trigger, submit, name, endpoint) {
  await page.getByRole('button', { name: '新建', exact: true }).click();
  await page.getByRole('menuitem', { name: trigger }).click();
  const dialog = page.getByRole('dialog', { name: trigger });
  await dialog.getByRole('textbox').fill(name);
  const response = await pairActionAndResponse(
    page.waitForResponse((r) => new URL(r.url()).pathname === endpoint && r.request().method() === 'POST'),
    () => dialog.getByRole('button', { name: submit }).click(),
  );
  if (!response.ok()) throw new Error('native resource creation failed');
  await expect(dialog).toBeHidden();
}
