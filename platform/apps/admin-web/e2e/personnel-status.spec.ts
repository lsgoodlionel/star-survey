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

  // The platform operator has a signed live session and independent grant;
  // these writes never use the legacy no-session operator credential.
  const fixture = await readFile(new URL('../../../tests/fixtures/surveys/publish-gateway.json', import.meta.url));
  await page.getByRole('link', { name: '模板中心' }).click();
  await page.getByRole('link', { name: '平台模板管理' }).click();
  await page.getByRole('textbox', { name: '平台模板名称' }).fill('人员权限验收模板');
  await page.getByLabel('定义 JSON 文件').setInputFiles({ name: 'template.json', mimeType: 'application/json', buffer: fixture });
  await page.getByRole('button', { name: '导入平台模板' }).click();
  const templateLink = page.getByRole('link', { name: '查看平台模板' });
  await expect(templateLink).toBeVisible();
  const templateId = new URL((await templateLink.getAttribute('href'))!, 'http://local').pathname.split('/').at(-1)!;
  expect(templateId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  await templateLink.click();
  await expect(page.getByRole('heading', { name: '版本记录' })).toBeVisible();
  await page.getByLabel('定义 JSON 文件').setInputFiles({ name: 'template-v2.json', mimeType: 'application/json', buffer: fixture });
  const versionResponse = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith(`/v1/platform/survey-templates/${templateId}/versions`));
  await page.getByRole('button', { name: '导入新版本' }).click();
  expect((await versionResponse).status()).toBe(200);
  await expect(page.getByRole('heading', { name: '版本记录' }).locator('..').getByText('版本 2', { exact: false })).toBeVisible();
  await page.getByRole('combobox', { name: '上架版本' }).selectOption('2');
  await page.getByRole('button', { name: '上架所选版本' }).click();
  await page.getByRole('button', { name: '确认上架版本 2' }).click();
  await expect(page.getByText('平台模板已上架')).toBeVisible();
  await expect(page.locator('dl.template-facts').getByText('2', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '下架平台模板' }).click();
  await expect(page.getByText('平台模板已下架')).toBeVisible();
  const templateAuditQuery = `SELECT
    (SELECT count(*) FROM audit_log WHERE actor_id='${metadata.principal}' AND resource LIKE 'platform-survey-template/${templateId}%' AND action IN ('platform.template.create','platform.template.version','platform.template.publish','platform.template.unpublish')),
    (SELECT count(*) FROM audit_log WHERE actor_id='${metadata.principal}' AND resource='platform-survey-template/${templateId} version=2' AND action='platform.template.publish'),
    (SELECT count(*) FROM platform_survey_template WHERE id='${templateId}' AND status='unpublished' AND published_version IS NULL AND current_version=2)`;
  const templateAudit = execFileSync('docker', ['exec', '-i', db, 'psql', '-U', 'postgres', '-d', 'platform', '-At', '-v', 'ON_ERROR_STOP=1'], { input: templateAuditQuery, encoding: 'utf8' }).trim();
  expect(templateAudit).toBe('4|1|1');

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
  const templateDenied = await request.post(`/v1/platform/survey-templates/${templateId}/publish`, { headers: { Authorization: `Bearer ${token}` }, data: { versionNo: 2 } });
  expect(templateDenied.status()).toBe(403);
  expect(denied.status()).toBe(403);
  const catalogDenied = await request.get('/v1/platform/plan-catalog', { headers: { Authorization: `Bearer ${token}` } });
  expect(catalogDenied.status()).toBe(403);
  await page.getByRole('link', { name: '返回租户列表' }).click();
  await expect(page.getByRole('heading', { name: '需要平台运营权限' })).toBeVisible();
  await page.evaluate((id) => {
    window.history.pushState({}, '', `/operations/tenants/${id}/engines`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, metadata.targetId);
  await expect(page.getByRole('heading', { name: '需要平台运营权限' })).toBeVisible();
  await expect(page.getByText(engineId)).toHaveCount(0);
  await page.evaluate((id) => {
    window.history.pushState({}, '', `/templates/${id}?mode=platform`);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, templateId);
  await expect(page.getByRole('alert')).toHaveText('无权查看平台模板管理');
  await expect(page.getByRole('heading', { name: '人员权限验收模板' })).toHaveCount(0);
  await expect(page.getByText('人员状态验收租户')).toHaveCount(0);
  await page.evaluate(() => {
    window.history.pushState({}, '', '/operations/plans');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page.getByRole('heading', { name: '需要平台运营权限' })).toBeVisible();
  await expect(page.getByRole('button', { name: '发布新套餐' })).toHaveCount(0);
});
