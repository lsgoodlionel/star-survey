import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
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
  const metadata = JSON.parse(await readFile(metadataPath, 'utf8')) as { targetId: string; principal: string };
  const db = process.env.ADMIN_WEB_PLATFORM_DB_CONTAINER;
  if (!db || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(metadata.targetId)
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(metadata.principal)) throw new Error('isolated fixture metadata is invalid');
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
  await page.getByRole('link', { name: '管理引擎实例' }).click();
  const engineId = `engine-${randomUUID().replaceAll('-', '').slice(0, 12)}`;
  await page.getByLabel('实例标识').fill(engineId);
  await page.getByLabel('引擎基础地址').fill('https://engine.invalid');
  await page.getByRole('button', { name: '检查登记内容' }).click();
  await page.getByRole('button', { name: '确认登记实例' }).click();
  await expect(page.getByRole('heading', { name: engineId })).toBeVisible();
  await page.getByRole('button', { name: '停止接收新流量' }).click();
  await page.getByRole('button', { name: '确认停止接收新流量' }).click();
  await page.getByRole('button', { name: '退役实例' }).click();
  await page.getByRole('button', { name: '确认退役实例' }).click();
  await expect(page.getByText('退役为终态，无法恢复')).toBeVisible();
  const query = `SELECT count(*) FROM audit_log WHERE tenant_id='${metadata.targetId}' AND actor_id='${metadata.principal}' AND resource='engine_instance/${engineId}' AND action IN ('engine_instance.register','engine_instance.status:active->draining','engine_instance.status:draining->retired')`;
  const audit = execFileSync('docker', ['exec', '-i', db, 'psql', '-U', 'postgres', '-d', 'platform', '-At', '-v', 'ON_ERROR_STOP=1'], { input: query, encoding: 'utf8' }).trim();
  expect(audit).toBe('3');
  await page.getByRole('link', { name: '返回租户详情' }).click();
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
  const engineDenied = await request.get(`/v1/platform/tenants/${metadata.targetId}/engine-instances`, { headers: { Authorization: `Bearer ${token}` } });
  expect(engineDenied.status()).toBe(403);
  expect(denied.status()).toBe(403);
  await page.getByRole('link', { name: '返回租户列表' }).click();
  await expect(page.getByRole('heading', { name: '需要平台运营权限' })).toBeVisible();
  await page.evaluate((id) => {
    window.history.pushState({}, '', `/operations/tenants/${id}/engines`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, metadata.targetId);
  await expect(page.getByRole('heading', { name: '需要平台运营权限' })).toBeVisible();
  await expect(page.getByText(engineId)).toHaveCount(0);
  await expect(page.getByText('人员状态验收租户')).toHaveCount(0);
});
