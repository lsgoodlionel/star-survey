import { expect, test, type Page } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { readFile, stat } from 'node:fs/promises';
import { redactSensitiveInputs } from './redaction';

type ImportFormat = 'csv' | 'json';
interface ImportRow { name: string; email: string }
interface Outcome { rowNo: number; outcome: string; reason: string | null; contactId: string | null }

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const jobKeys = ['created', 'duplicate', 'invalid', 'jobId', 'listId', 'processedRows', 'status', 'totalRows', 'updated'];
const outcomeKeys = ['contactId', 'outcome', 'reason', 'rowNo'];

for (const { format, bom } of [{ format: 'csv', bom: false }, { format: 'csv', bom: true }, { format: 'json', bom: false }] as const) {
  test(`delivery ${format}${bom ? ' BOM' : ''} import preserves valid rows, reports failures and deduplicates without leaking row content`, async ({ page }) => {
    await login(page);
    await page.getByRole('link', { name: '投放', exact: true }).click();
    await expect(page).toHaveURL(/\/delivery$/);
    const suffix = randomUUID().slice(0, 8);
    const marker = `wave4-${format}-${suffix}`;
    const listName = `导入验收-${marker}`;
    const existing = { name: `原有联系人-${marker}`, email: `existing-${marker}@example.invalid` };
    const rows: ImportRow[] = [
      { name: `更新联系人-${marker}`, email: existing.email },
      { name: `新增甲-${marker}`, email: `new-a-${marker}@example.invalid` },
      { name: `新增甲-${marker}`, email: `NEW-A-${marker.toUpperCase()}@EXAMPLE.INVALID` },
      { name: `坏邮箱-${marker}`, email: `invalid-email-${marker}` },
      { name: `缺邮箱-${marker}`, email: '' },
      { name: `新增乙-${marker}`, email: `new-b-${marker}@example.invalid` },
    ];
    const deliveryRequests: string[] = [];
    page.context().on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if (request.method() === 'POST' && /\/delivery-tasks(?:\/|$)|\/email-settings\/(?:test|verify)/.test(path)) deliveryRequests.push(path);
    });

    await page.getByLabel('投放工作区新名单名称').fill(listName);
    const [createdList] = await Promise.all([
      page.waitForResponse((response) => new URL(response.url()).pathname === '/v1/contact-lists' && response.request().method() === 'POST'),
      page.getByRole('button', { name: '创建名单', exact: true }).click(),
    ]);
    expect(createdList.status()).toBe(201);
    const list = await createdList.json() as { id: string };
    expect(list.id).toMatch(uuid);
    await expect(page.getByLabel('当前联系人名单')).toHaveValue(list.id);
    await page.getByLabel('工作区联系人姓名').fill(existing.name);
    await page.getByLabel('工作区联系人邮箱').fill(existing.email);
    const [createdContact] = await Promise.all([
      page.waitForResponse((response) => new URL(response.url()).pathname === '/v1/contacts' && response.request().method() === 'POST'),
      page.getByRole('button', { name: '新增联系人', exact: true }).click(),
    ]);
    expect(createdContact.status()).toBe(201);
    const existingContact = await createdContact.json() as { id: string };
    expect(existingContact.id).toMatch(uuid);
    await expect(page.getByRole('region', { name: '联系人列表' })).toContainText(existing.name);

    await page.getByLabel('投放工作区导入格式').selectOption(format);
    await page.getByLabel('投放工作区导入内容').fill((bom ? '\uFEFF' : '') + serialize(format, rows));
    const [submitted, completed, outcomeResponse] = await Promise.all([
      page.waitForResponse((response) => new URL(response.url()).pathname === `/v1/contact-lists/${list.id}/imports` && response.request().method() === 'POST'),
      page.waitForResponse(async (response) => {
        if (!/^\/v1\/contact-imports\/[0-9a-f-]+$/.test(new URL(response.url()).pathname) || response.request().method() !== 'GET' || response.status() !== 200) return false;
        const body = await response.json() as { status?: string };
        return body.status === 'completed';
      }, { timeout: 60_000 }),
      page.waitForResponse((response) => /^\/v1\/contact-imports\/[0-9a-f-]+\/outcomes$/.test(new URL(response.url()).pathname) && response.request().method() === 'GET', { timeout: 60_000 }),
      page.getByRole('button', { name: '提交导入任务', exact: true }).click(),
    ]);
    expect(submitted.status()).toBe(202);
    const sent = submitted.request().postDataJSON() as { format: string; content: string };
    expect(sent.format).toBe(format);
    expect(sent.content).toBe((bom ? '\uFEFF' : '') + serialize(format, rows));
    const submittedJob = await submitted.json() as Record<string, unknown>;
    const completedJob = await completed.json() as Record<string, unknown>;
    for (const job of [submittedJob, completedJob]) {
      expect(Object.keys(job).sort()).toEqual(jobKeys);
      expect(job.listId).toBe(list.id);
      expect(job.jobId).toMatch(uuid);
      expect(JSON.stringify(job)).not.toContain(marker);
    }
    expect(completedJob).toEqual({
      jobId: submittedJob.jobId, listId: list.id, status: 'completed',
      totalRows: 6, processedRows: 6, created: 2, updated: 1, duplicate: 1, invalid: 2,
    });
    expect(new URL(outcomeResponse.url()).pathname).toBe(`/v1/contact-imports/${submittedJob.jobId}/outcomes`);
    expect(outcomeResponse.status()).toBe(200);
    const outcomes = await outcomeResponse.json() as Outcome[];
    expect(outcomes).toHaveLength(6);
    for (const outcome of outcomes) {
      expect(Object.keys(outcome).sort()).toEqual(outcomeKeys);
      if (outcome.contactId !== null) expect(outcome.contactId).toMatch(uuid);
    }
    expect(outcomes.map(({ rowNo, outcome, reason }) => ({ rowNo, outcome, reason }))).toEqual([
      { rowNo: 1, outcome: 'updated', reason: null },
      { rowNo: 2, outcome: 'created', reason: null },
      { rowNo: 3, outcome: 'duplicate', reason: 'duplicate_in_file' },
      { rowNo: 4, outcome: 'invalid', reason: 'invalid_email' },
      { rowNo: 5, outcome: 'invalid', reason: 'missing_dedupe_key' },
      { rowNo: 6, outcome: 'created', reason: null },
    ]);
    for (const index of [0, 1, 5]) expect(outcomes[index].contactId).toMatch(uuid);
    expect(outcomes[0].contactId).toBe(existingContact.id);
    expect(new Set([outcomes[0].contactId, outcomes[1].contactId, outcomes[5].contactId]).size).toBe(3);
    for (const index of [2, 3, 4]) expect(outcomes[index].contactId).toBeNull();
    const result = page.getByRole('status');
    await expect(result).toContainText('已完成；已处理 6/6 行，新增 2、更新 1、重复 1、无效 2');
    await expect(result.getByRole('listitem')).toHaveText([
      '第 1 行：updated', '第 2 行：created', '第 3 行：duplicate（duplicate_in_file）',
      '第 4 行：invalid（invalid_email）', '第 5 行：invalid（missing_dedupe_key）', '第 6 行：created',
    ]);
    for (const row of rows) {
      expect(JSON.stringify(outcomes)).not.toContain(row.name);
      await expect(result).not.toContainText(row.name);
      if (row.email) {
        expect(JSON.stringify(outcomes).toLowerCase()).not.toContain(row.email.toLowerCase());
        await expect(result).not.toContainText(row.email);
      }
    }

    // Completion must update the current list without a navigation or browser refresh.
    const contacts = page.getByRole('region', { name: '联系人列表' });
    await expect(contacts.getByRole('row')).toHaveCount(4);
    for (const row of [rows[0], rows[1], rows[5]]) {
      const contact = contacts.getByRole('row').filter({ hasText: row.name });
      await expect(contact).toHaveCount(1);
      await expect(contact).toContainText(row.email);
    }
    await expect(contacts).not.toContainText(existing.name);
    for (const row of [rows[3], rows[4]]) await expect(contacts).not.toContainText(row.name);

    // A new page has no React Query cache; require the real GET and check its persisted identities.
    const readbackPage = await page.context().newPage();
    try {
      await login(readbackPage);
      const [readbackResponse] = await Promise.all([
        readbackPage.waitForResponse((response) => {
          const url = new URL(response.url());
          return url.pathname === '/v1/contacts' && url.searchParams.get('listId') === list.id && response.request().method() === 'GET';
        }),
        (async () => {
          await readbackPage.getByRole('link', { name: '投放', exact: true }).click();
          await readbackPage.getByLabel('当前联系人名单').selectOption(list.id);
        })(),
      ]);
      expect(readbackResponse.status()).toBe(200);
      const persistedBody = await readbackResponse.json() as { items: { id: string; displayName: string; email: string }[]; nextCursor: string | null };
      expect(persistedBody.nextCursor).toBeNull();
      expect(persistedBody.items.map(({ id, displayName, email }) => ({ id, displayName, email }))).toEqual(expect.arrayContaining(
        [0, 1, 5].map((index) => ({ id: outcomes[index].contactId, displayName: rows[index].name, email: rows[index].email })),
      ));
      expect(persistedBody.items).toHaveLength(3);
      const persisted = readbackPage.getByRole('region', { name: '联系人列表' });
      await expect(persisted.getByRole('row')).toHaveCount(4);
      for (const row of [rows[0], rows[1], rows[5]]) await expect(persisted).toContainText(row.email);
    } finally {
      await readbackPage.close();
    }
    expect(deliveryRequests).toEqual([]);
  });
}

function serialize(format: ImportFormat, rows: ImportRow[]) {
  return format === 'json' ? JSON.stringify(rows) : ['name,email', ...rows.map((row) => `${row.name},${row.email}`)].join('\n');
}

async function login(page: Page) {
  const tokenFile = requiredEnv('ADMIN_WEB_JWT_FILE');
  if (((await stat(tokenFile)).mode & 0o777) !== 0o600) throw new Error('Test credential file must be private');
  const token = (await readFile(tokenFile, 'utf8')).trim();
  if (!token) throw new Error('Test credential file is empty');
  const metadata = JSON.parse(await readFile(requiredEnv('ADMIN_WEB_METADATA_FILE'), 'utf8')) as { actorId: string; tenantId: string };
  await page.goto('/dev/token');
  try {
    await page.getByLabel('测试令牌').fill(token);
    const [meResponse] = await Promise.all([
      page.waitForResponse((response) => new URL(response.url()).pathname === '/v1/me' && response.request().method() === 'GET'),
      page.getByRole('button', { name: '登录', exact: true }).click(),
    ]);
    expect(meResponse.status()).toBe(200);
    const me = await meResponse.json() as { actorId: string; tenantId: string };
    expect(me.actorId).toBe(metadata.actorId);
    expect(me.tenantId).toBe(metadata.tenantId);
    await expect(page).toHaveURL(/\/dashboard$/);
  } catch {
    throw new Error('Isolated delivery acceptance login did not complete');
  } finally {
    if (!page.isClosed()) await redactSensitiveInputs(page);
  }
}

function requiredEnv(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}
