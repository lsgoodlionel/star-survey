import { expect, test, type Page } from '@playwright/test';
import { execFile } from 'node:child_process';
import { readFile, stat } from 'node:fs/promises';
import { redactSensitiveInputs } from './redaction';

function env(name: string): string { const value = process.env[name]; if (!value) throw new Error(`Missing ${name}`); return value; }
async function downloadCsv(page: Page): Promise<string> {
  const pending = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载导出文件' }).click();
  const path = await (await pending).path();
  if (!path) throw new Error('Download path missing');
  return new Promise((resolve, reject) => execFile('unzip', ['-p', path, 'responses.csv'], { encoding: 'utf8', maxBuffer: 1048576 },
    (error, stdout) => error ? reject(error) : resolve(stdout)));
}

test('completed export naturally expires and a new one can be downloaded', async ({ page }) => {
  test.setTimeout(180_000);
  const tokenFile = env('ADMIN_WEB_JWT_FILE');
  expect((await stat(tokenFile)).mode & 0o777).toBe(0o600);
  const token = (await readFile(tokenFile, 'utf8')).trim();
  const result = JSON.parse(await readFile(env('ADMIN_WEB_RESULT_FILE'), 'utf8')) as { surveyId: string };
  await page.goto('/dev/token');
  try {
    await page.getByLabel('测试令牌').fill(token);
    const me = page.waitForResponse((r) => new URL(r.url()).pathname === '/v1/me' && r.request().method() === 'GET');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    expect((await me).status()).toBe(200);
    await expect(page).toHaveURL(/\/dashboard$/);
  } finally { await redactSensitiveInputs(page); }
  await page.evaluate((path) => { history.pushState({}, '', path); dispatchEvent(new PopStateEvent('popstate')); }, `/surveys/${result.surveyId}/responses`);
  await expect(page.getByRole('heading', { name: '答卷与导出' })).toBeVisible();
  await expect(page.getByLabel('已完成 1')).toBeVisible();
  const create = async () => {
    const pending = page.waitForResponse((r) => new URL(r.url()).pathname === `/v1/surveys/${result.surveyId}/exports` && r.request().method() === 'POST');
    await page.getByRole('button', { name: '创建导出任务' }).click();
    const response = await pending;
    expect(response.status()).toBe(202);
    const job = await response.json() as { jobId: string; expiresAt: string };
    expect(job.jobId).toMatch(/^[0-9a-f-]{36}$/i);
    expect(Number.isFinite(Date.parse(job.expiresAt))).toBe(true);
    await expect(page.locator('.export-job')).toContainText('导出完成', { timeout: 60_000 });
    return job;
  };
  const first = await create();
  const before = await downloadCsv(page);
  expect(before).toMatch(/(?:^|,)A1(?:,|\r?\n)/m);
  await expect.poll(async () => {
    const response = await page.request.get(`/v1/exports/${first.jobId}`, { headers: { Authorization: `Bearer ${token}` } });
    if (response.status() !== 200) return `http-${response.status()}`;
    const job = await response.json() as { status?: string };
    return job.status;
  }, { timeout: 70_000, intervals: [2000] }).toBe('expired');
  const old = await page.request.get(`/v1/exports/${first.jobId}/download`, { headers: { Authorization: `Bearer ${token}` } });
  expect(old.status()).toBe(410);
  await page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', { name: '发布与版本' }).click();
  await page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', { name: '答卷与导出' }).click();
  await expect(page.locator('.export-job')).toContainText('文件已过期');
  await expect(page.getByRole('button', { name: '下载导出文件' })).toHaveCount(0);
  const second = await create();
  expect(second.jobId).not.toBe(first.jobId);
  const after = await downloadCsv(page);
  expect(after).toBe(before);
  console.log('[natural-expiry] completed-download-expired-410-recreated-download:ok');
});
