/* global process, URL, window, PopStateEvent */
import { randomBytes } from 'node:crypto';
import { unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { test, expect } from '@playwright/test';
import { loginWithHandoff, readHandoffFixture } from './handoff.mjs';

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
  let projectCreated = false;
  let previewStarted = false;
  let signedIn = false;
  let failed = false;
  try {
    await loginWithHandoff(context, page, fixture);
    signedIn = true;
    await expect(page).toHaveURL(/\/dashboard$/, { timeout: 30_000 });
    await page.getByRole('link', { name: '项目与问卷' }).click();
    await expect(page).toHaveURL(/\/workspace$/);
    await create(page, '新建项目', '创建项目', projectName, '/v1/projects', () => { projectCreated = true; });
    await resource(page, projectName).click();
    await create(page, '新建问卷', '创建问卷', 'Native author test', '/v1/surveys');
    await expect(page).toHaveURL(/\/surveys\/[0-9a-f-]{36}\/edit$/i);
    await page.getByLabel('题目文本').fill('Native isolated preview question');
    await page.getByLabel('标题').fill('Native isolated author');
    await page.getByRole('button', { name: '保存草稿' }).click();
    await expect(page.getByText(/已保存版本 \d+/)).toBeVisible();
    await page.getByRole('link', { name: '快速预览' }).click();
    previewStarted = true; // The request may have succeeded even if the UI times out.
    await page.getByRole('button', { name: '创建真实预览' }).click();
    await expect(page.getByText('真实预览已就绪')).toBeVisible({ timeout: 210_000 });
    await page.getByRole('button', { name: '结束真实预览' }).click();
    await expect(page.getByText('真实预览已结束')).toBeVisible({ timeout: 210_000 });
    previewStarted = false;
  } catch {
    failed = true;
  } finally {
    try {
      if (previewStarted) {
        const end = page.getByRole('button', { name: '结束真实预览' });
        await expect(end).toBeVisible({ timeout: 15_000 });
        await end.click();
        await expect(page.getByText('真实预览已结束')).toBeVisible({ timeout: 210_000 });
      }
      if (projectCreated) {
        await page.evaluate(() => { window.history.pushState({}, '', '/workspace'); window.dispatchEvent(new PopStateEvent('popstate')); });
        await resource(page, projectName).click();
        await page.getByRole('button', { name: '归档', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: '归档资源' });
        await dialog.getByRole('button', { name: '确认归档' }).click();
        await expect(dialog).toBeHidden();
        await expect(resource(page, projectName)).toHaveCount(0);
      }
    } catch {
      failed = true;
    }
    if (signedIn) {
      try {
        const logout = page.waitForResponse((r) => new URL(r.url()).pathname === '/v1/auth/logout' &&
          r.request().method() === 'POST', { timeout: 15_000 });
        await page.getByRole('button', { name: '退出登录' }).click();
        if ((await logout).status() !== 204) throw new Error('logout failed');
      } catch {
        failed = true;
      }
    }
    await context.clearCookies().catch(() => { failed = true; });
  }
  // Avoid emitting one-use handoff URLs, cookies or browser exception text.
  if (failed) throw new Error('native preview slice or cleanup failed');
});

function resource(page, name) {
  return page.getByRole('list', { name: '当前位置资源' }).getByRole('button', { name, exact: true });
}

async function create(page, trigger, submit, name, endpoint, onCreated = () => {}) {
  await page.getByRole('button', { name: '新建', exact: true }).click();
  await page.getByRole('menuitem', { name: trigger }).click();
  const dialog = page.getByRole('dialog', { name: trigger });
  await dialog.getByRole('textbox').fill(name);
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === endpoint && r.request().method() === 'POST');
  await dialog.getByRole('button', { name: submit }).click();
  if (!(await response).ok()) throw new Error('native resource creation failed');
  onCreated();
  await expect(dialog).toBeHidden();
}
