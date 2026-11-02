import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { redactSensitiveInputs } from './redaction';

test.describe.configure({ retries: 0 });

test('personnel publishes an audited version and onboards an isolated tenant', async ({ page, request }) => {
  const tokenPath = process.env.ADMIN_WEB_PERSONNEL_JWT_FILE;
  const metadataPath = process.env.ADMIN_WEB_PERSONNEL_METADATA_FILE;
  const db = process.env.ADMIN_WEB_PLATFORM_DB_CONTAINER;
  if (!tokenPath || !metadataPath || !db) throw new Error('isolated personnel fixture is required');
  const token = (await readFile(tokenPath, 'utf8')).trim();
  const metadata = JSON.parse(await readFile(metadataPath, 'utf8')) as { principal: string };
  expect(metadata.principal).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  const suffix = randomUUID().replaceAll('-', '').slice(0, 12);
  const planCode = `a-plan-${suffix}`;
  const tenantCode = `a-tenant-${suffix}`;
  const created = await request.post('/v1/platform/tenants', { headers: { Authorization: `Bearer ${token}` },
    data: { code: tenantCode, name: '套餐开通验收租户' } });
  expect(created.status()).toBe(201);
  const target = await created.json() as { id: string; status: string };
  expect(target.status).toBe('provisioning');
  expect(target.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);

  await page.goto('/dev/token');
  try {
    await page.getByLabel('测试令牌').fill(token);
    await page.getByRole('button', { name: '登录' }).click();
    await expect(page).toHaveURL(/\/operations\/tenants$/);
  } finally { await redactSensitiveInputs(page); }
  await page.getByRole('link', { name: '套餐目录' }).click();
  await page.getByRole('button', { name: '发布新套餐' }).click();
  await page.getByLabel('套餐编码').fill(planCode);
  await page.getByLabel('能力编码（逗号分隔）').fill('survey.read');
  await page.getByLabel('额度 JSON').fill('{"member.seats":"5","response.valid_completed":"9223372036854775807"}');
  await page.getByRole('button', { name: '检查发布内容' }).click();
  await page.getByRole('button', { name: '确认发布版本' }).click();
  await expect(page.getByText('套餐版本已发布')).toBeVisible();
  await page.getByRole('button', { name: `查看 ${planCode} 版本` }).click();
  await expect(page.getByText('response.valid_completed: 9223372036854775807').first()).toBeVisible();
  await page.getByRole('button', { name: '选择版本 1 开通租户' }).click();
  await page.getByLabel('租户 ID').fill(target.id);
  await page.getByLabel('所有者标识').fill('personnel-plan-test-owner');
  await page.getByRole('button', { name: '检查开通内容' }).click();
  await page.getByRole('button', { name: '确认开通租户' }).click();
  await expect(page.getByText('租户订阅与所有者已开通')).toBeVisible();

  const query = `SELECT (SELECT count(*) FROM platform_plan_audit WHERE actor_subject='${metadata.principal}' AND plan_code='${planCode}' AND plan_version=1),
    (SELECT count(*) FROM subscription s JOIN plan_version p ON p.id=s.plan_version_id WHERE s.tenant_id='${target.id}' AND p.plan_code='${planCode}' AND p.version=1),
    (SELECT count(*) FROM audit_log WHERE tenant_id='${target.id}' AND actor_id='${metadata.principal}' AND action='tenant.onboard');`;
  const evidence = execFileSync('docker', ['exec', '-i', db, 'psql', '-U', 'postgres', '-d', 'platform', '-At', '-v', 'ON_ERROR_STOP=1'], { input: query, encoding: 'utf8' }).trim();
  expect(evidence).toBe('1|1|1');
});
