import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { redactSensitiveInputs } from './redaction';

test.describe.configure({ retries: 0 });

test('live personnel grant controls tenant status and revocation takes effect', async ({ page, request }) => {
  const tokenPath = process.env.ADMIN_WEB_PERSONNEL_JWT_FILE;
  const legacyPath = process.env.ADMIN_WEB_OPERATOR_JWT_FILE;
  const metadataPath = process.env.ADMIN_WEB_PERSONNEL_METADATA_FILE;
  const helper = process.env.ADMIN_WEB_PERSONNEL_HELPER;
  if (!tokenPath || !legacyPath || !metadataPath || !helper) throw new Error('isolated personnel fixture is required');
  const token = (await readFile(tokenPath, 'utf8')).trim();
  const legacy = (await readFile(legacyPath, 'utf8')).trim();
  const metadata = JSON.parse(await readFile(metadataPath, 'utf8')) as { targetId: string };
  const legacyDenied = await request.get('/v1/platform/tenants/directory', { headers: { Authorization: `Bearer ${legacy}` } });
  expect(legacyDenied.status()).toBe(403);
  await page.goto('/dev/token');
  try {
    await page.getByLabel('测试令牌').fill(token);
    await page.getByRole('button', { name: '登录' }).click();
    await expect(page).toHaveURL(/\/operations\/tenants$/);
  } finally { await redactSensitiveInputs(page); }

  await page.evaluate((id) => {
    window.history.pushState({}, '', `/operations/tenants/${id}`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, metadata.targetId);
  await expect(page.getByRole('heading', { name: '人员状态验收租户' })).toBeVisible();
  await page.getByRole('button', { name: '暂停租户' }).click();
  await page.getByRole('button', { name: '确认暂停租户' }).click();
  await expect(page.getByRole('button', { name: '启用租户' })).toBeVisible();
  await page.getByRole('button', { name: '关闭租户' }).click();
  await expect(page.getByText('关闭为终态，无法重新启用。')).toBeVisible();
  await page.getByRole('button', { name: '确认关闭租户' }).click();
  await expect(page.getByText('关闭为终态，无法重新启用')).toBeVisible();
  await expect(page.getByRole('button', { name: '启用租户' })).toHaveCount(0);

  execFileSync('python3', [helper, 'verify-and-revoke'], { stdio: 'pipe' });
  const denied = await request.post(`/v1/platform/tenants/${metadata.targetId}/status`, {
    headers: { Authorization: `Bearer ${token}` }, data: { status: 'active' },
  });
  expect(denied.status()).toBe(403);
  const catalogDenied = await request.get('/v1/platform/plan-catalog', { headers: { Authorization: `Bearer ${token}` } });
  expect(catalogDenied.status()).toBe(403);
  await page.getByRole('link', { name: '返回租户列表' }).click();
  await expect(page.getByRole('heading', { name: '需要平台运营权限' })).toBeVisible();
  await expect(page.getByText('人员状态验收租户')).toHaveCount(0);
  await page.evaluate(() => {
    window.history.pushState({}, '', '/operations/plans');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page.getByRole('heading', { name: '需要平台运营权限' })).toBeVisible();
  await expect(page.getByRole('button', { name: '发布新套餐' })).toHaveCount(0);
});
