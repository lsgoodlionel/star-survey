import { expect, test } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { inflateRawSync } from 'node:zlib';
import { recordRunRootId } from './runRootEvidence';
import { redactSensitiveInputs } from './redaction';
import { attachmentPackageFailure, persistedAttachmentName } from './attachmentEvidence';

const required = (name: string) => { const value = process.env[name]; if (!value) throw new Error(`${name} is required`); return value; };
async function stage<T>(name: string, timeout: number, action: () => Promise<T>): Promise<T> {
  process.stdout.write(`[attachment-stage] ${name}:start\n`);
  try { const value = await test.step(name, action, { timeout }); process.stdout.write(`[attachment-stage] ${name}:ok\n`); return value; }
  catch (error) { process.stdout.write(`[attachment-stage] ${name}:failed\n`); throw error; }
}

test('isolated real attachment upload and package', async ({ page, request }) => {
  test.setTimeout(360_000);
  page.setDefaultTimeout(15_000);
  const token = (await readFile(required('ADMIN_WEB_JWT_FILE'), 'utf8')).trim();
  const auth = { Authorization: `Bearer ${token}` };
  const base = required('ADMIN_WEB_BASE_URL');
  await stage('verify-admin-frame-protection', 15_000, async () => {
    const home = await request.get(base);
    expect(home.headers()['x-frame-options']).toBe('DENY');
    expect(home.headers()['x-content-type-options']).toBe('nosniff');
  });
  let surveyId = ''; let fieldName = ''; let temporaryName = ''; let storedName = '';
  await stage('create-synthetic-survey', 30_000, async () => {
    const project = await request.post(`${base}/v1/projects`, { headers: auth, data: { name: `附件隔离-${randomUUID()}` } });
    expect(project.status()).toBe(201);
    const projectData = await project.json() as { id: string };
    await recordRunRootId(required('ADMIN_WEB_TEST_RESULTS_DIR'), projectData);
    const definition = { definitionVersion: 2, title: '虚构附件诊断', description: '', language: 'zh-Hans', theme: 'fruity_twentythree',
      settings: { anonymized: 'N', datestamp: 'Y', savetimings: 'N', ipaddr: 'N', refurl: 'N', allowsave: 'Y', allowprev: 'Y', alloweditaftercompletion: 'N', format: 'G', questionindex: '0', usecaptcha: 'N', shownoanswer: 'N' },
      groups: [{ uuid: randomUUID(), title: '附件题组', questions: [{ uuid: randomUUID(), code: 'QFILE', type: '|', text: '请上传虚构文件', mandatory: false,
        attributes: { allowed_filetypes: 'txt', min_num_of_files: '0', max_num_of_files: '1', max_filesize: '1024' } }] }] };
    const survey = await request.post(`${base}/v1/surveys`, { headers: auth, data: { parentId: projectData.id, definition } });
    expect(survey.status()).toBe(201); surveyId = (await survey.json() as { id: string }).id;
  });
  await stage('publish-and-link', 180_000, async () => {
    await page.goto('/dev/token');
    try { await page.getByLabel('测试令牌').fill(token); await page.getByRole('button', { name: '登录' }).click(); await expect(page).toHaveURL(/\/dashboard$/); }
    finally { await redactSensitiveInputs(page); }
    await page.evaluate((id) => { window.history.pushState({}, '', `/surveys/${id}/publish`); window.dispatchEvent(new PopStateEvent('popstate')); }, surveyId);
    await page.getByRole('button', { name: '提交审批' }).click();
    await page.getByRole('button', { name: '批准申请' }).click();
    await page.getByRole('button', { name: '发布问卷' }).click();
    await expect(page.getByText('发布成功')).toBeVisible({ timeout: 150_000 });
    await page.getByLabel('链接名称').fill('虚构附件链接');
    await page.getByRole('button', { name: '创建链接' }).click();
    await expect(page.getByRole('list', { name: '投放链接' }).getByRole('link').first()).toBeVisible();
  });
  const link = await page.getByRole('list', { name: '投放链接' }).getByRole('link').first().getAttribute('href');
  if (!link) throw new Error('Synthetic delivery link missing');
  const respondent = await page.context().newPage();
  const uploadStatuses: string[] = [];
  const uploadPolicies: { method: string; status: number; frame: string; ancestors: string; nosniff: boolean }[] = [];
  let frameDenied = false;
  respondent.on('console', (message) => {
    if (/refused to display.*(?:x-frame-options|frame-ancestors)/i.test(message.text())) frameDenied = true;
  });
  respondent.on('response', (response) => {
    try {
      const method = response.request().method();
      if (new URL(response.url()).pathname.includes('/uploader/') && (method === 'GET' || method === 'POST')) {
        const header = (response.headers()['x-frame-options'] ?? '').toUpperCase();
        const frame = header === 'DENY' || header === 'SAMEORIGIN' ? header : header ? 'other' : 'none';
        uploadStatuses.push(`${method}:${response.status()}:${frame}`);
        const ancestors = (response.headers()['content-security-policy'] ?? '').split(';')
          .map((part) => part.trim()).find((part) => part.startsWith('frame-ancestors '));
        uploadPolicies.push({ method, status: response.status(), frame,
          ancestors: ancestors === "frame-ancestors 'self'" ? 'self' : 'other',
          nosniff: response.headers()['x-content-type-options'] === 'nosniff' });
      }
    } catch { /* Do not print URLs. */ }
  });
  await stage('open-respondent', 30_000, async () => { try { await respondent.goto(link, { timeout: 25_000 }); } catch { throw new Error('Synthetic respondent navigation failed'); } });
  await stage('find-upload-question', 60_000, async () => {
    for (let step = 0; step < 6; step++) {
      const count = respondent.locator('input[name$="_Cfilecount"]').first();
      if (await count.count()) { const name = await count.getAttribute('name'); if (!name?.endsWith('_Cfilecount')) throw new Error('Upload field missing'); fieldName = name.slice(0, -'_Cfilecount'.length); return; }
      const next = respondent.locator('#ls-button-submit:visible, #ls-button-next:visible, button[type="submit"]:visible, input[type="submit"]:visible').last();
      if (!await next.count()) break;
      await next.click({ timeout: 10_000 }); await respondent.waitForLoadState('domcontentloaded', { timeout: 15_000 });
    }
    throw new Error('Upload question not encountered');
  });
  respondent.setDefaultTimeout(8_000);
  await stage('click-upload-trigger', 12_000, async () => {
    const trigger = respondent.locator('a.upload').first();
    await expect(trigger).toBeVisible({ timeout: 8_000 });
    await trigger.click({ timeout: 8_000 });
  });
  await stage('locate-modal-control', 12_000, async () => {
    const select = respondent.locator('.file-upload-modal.show button[id^="button_"]').first();
    await expect(select).toBeVisible({ timeout: 8_000 });
  });
  await stage('create-file-input', 12_000, async () => {
    const select = respondent.locator('.file-upload-modal.show button[id^="button_"]').first();
    const fileInput = respondent.locator('input[type="file"][name="uploadfile"]').last();
    // AjaxUpload places a transparent file input over the button on mouseover.
    // If it already exists, hovering the covered button causes an interception loop.
    if (await fileInput.count() === 0) await select.dispatchEvent('mouseover', { bubbles: true });
    await expect(fileInput).toBeAttached({ timeout: 8_000 });
  });
  await stage('upload-synthetic-file', 45_000, async () => {
    await respondent.locator('input[type="file"][name="uploadfile"]').last().setInputFiles({ name: 'synthetic-evidence.txt', mimeType: 'text/plain', buffer: Buffer.from('synthetic wave 3 attachment') });
    try {
      await expect(respondent.locator('.file-upload-modal.show .file-name')).toHaveText('synthetic-evidence.txt', { timeout: 30_000 });
    } catch {
      const notices = await respondent.locator('.file-upload-modal.show .alert-danger:visible').count();
      const spinner = await respondent.locator('.file-upload-modal.show #loading-icon-fielupload').count();
      process.stdout.write(`[attachment-diagnostic] uploader-statuses=${uploadStatuses.slice(-20).join(',') || 'none'} danger=${notices > 0} spinner=${spinner > 0} frame-denied=${frameDenied}\n`);
      throw new Error('Synthetic upload did not appear in modal');
    }
  });
  await stage('verify-engine-frame-protection', 15_000, async () => {
    expect(frameDenied).toBe(false);
    for (const method of ['GET', 'POST']) {
      const policy = uploadPolicies.filter((item) => item.method === method).at(-1);
      expect(policy).toEqual({ method, status: 200, frame: 'SAMEORIGIN', ancestors: 'self', nosniff: true });
    }
    process.stdout.write(`[attachment-diagnostic] verified-policies=${uploadStatuses.slice(-20).join(',')}\n`);
  });
  await stage('save-upload-modal', 30_000, async () => {
    await respondent.locator('.file-upload-modal.show .modal-footer button').click();
    await expect(respondent.locator('.file-upload-modal.show')).toHaveCount(0);
    const answer = await respondent.locator('input').evaluateAll((inputs, name) => (inputs.find((input) => input.getAttribute('name') === name) as HTMLInputElement | undefined)?.value ?? '', fieldName);
    const files = JSON.parse(answer) as { filename?: string }[];
    if (files.length !== 1 || !files[0].filename) throw new Error('Upload not saved in form');
    temporaryName = files[0].filename;
    expect(temporaryName).toMatch(/^futmp_[a-z0-9]{15}_txt$/);
  });
  await stage('submit-response', 60_000, async () => {
    for (let step = 0; step < 6; step++) {
      const next = respondent.locator('#ls-button-submit:visible, #ls-button-next:visible, button[type="submit"]:visible, input[type="submit"]:visible').last();
      if (!await next.count()) break;
      await next.click({ timeout: 10_000 }); await respondent.waitForLoadState('domcontentloaded', { timeout: 15_000 });
    }
    await expect.poll(async () => { const response = await request.get(`${base}/v1/surveys/${surveyId}/responses/summary`, { headers: auth }); if (!response.ok()) return -1; return (await response.json() as { total: { engineCompleted: number } }).total.engineCompleted; }, { timeout: 40_000 }).toBe(1);
  });
  await stage('verify-response-value', 30_000, async () => {
    const response = await request.get(`${base}/v1/surveys/${surveyId}/responses?version=1&limit=50`, { headers: auth }); expect(response.status()).toBe(200);
    const body = await response.json() as { items: { state: string; answersStatus: string; answers: Record<string, string | null> | null }[] };
    expect(body.items.length).toBe(1);
    expect(body.items[0].state).toBe('engine_completed');
    expect(body.items[0].answersStatus).toBe('available');
    storedName = persistedAttachmentName(body.items[0].answers?.[fieldName], temporaryName);
  });
  await stage('download-and-check-package', 120_000, async () => {
    await page.evaluate((id) => { window.history.pushState({}, '', `/surveys/${id}/responses`); window.dispatchEvent(new PopStateEvent('popstate')); }, surveyId);
    await page.getByLabel('导出格式').selectOption('attachments');
    await page.getByRole('button', { name: '创建导出任务' }).click();
    await expect(page.locator('.export-job')).toContainText('导出完成', { timeout: 60_000 });
    const pending = page.waitForEvent('download', { timeout: 20_000 }); await page.getByRole('button', { name: '下载导出文件' }).click(); const file = await pending;
    const entries = readZipEntries(await readFile(await file.path()));
    const files = [...entries.entries()].filter(([name]) => name.startsWith('files/'));
    const rows = parseAttachmentCsv(entries.get('files.csv')?.toString('utf8') ?? '');
    const manifest = parseAttachmentCsv(entries.get('attachments.csv')?.toString('utf8') ?? '');
    if (files.length !== 1) {
      const evidence = attachmentPackageFailure(rows, manifest, fieldName, storedName);
      process.stdout.write(`[attachment-export-diagnostic] ${JSON.stringify(evidence)}\n`);
      const status = rows[0]?.indexOf('status') ?? -1;
      const category = manifest.length <= 1 ? 'manifest-empty'
        : rows.slice(1).some((row) => row[status] === 'absent') ? 'file-absent' : 'unexpected-file-count';
      throw new Error(`Attachment export: ${category}`);
    }
    expect(files[0][1].equals(Buffer.from('synthetic wave 3 attachment'))).toBe(true);
    expect(rows).toHaveLength(2); expect(manifest).toHaveLength(2);
    const index = Object.fromEntries(rows[0].map((key, i) => [key, rows[1][i]]));
    const item = Object.fromEntries(manifest[0].map((key, i) => [key, manifest[1][i]]));
    expect(index.status).toBe('stored'); expect(index.path === files[0][0]).toBe(true); expect(index.name).toBe('synthetic-evidence.txt');
    expect(item.storedname === storedName && item.fieldname === fieldName).toBe(true);
    for (const key of ['version', 'sid', 'responseid', 'fieldname', 'index']) expect(index[key] === item[key] && !!index[key]).toBe(true);
  });
});

function readZipEntries(zip: Buffer) {
  let end = zip.length - 22;
  while (end >= 0 && zip.readUInt32LE(end) !== 0x06054b50) end -= 1;
  if (end < 0) throw new Error('ZIP end directory is missing');
  const count = zip.readUInt16LE(end + 10);
  let cursor = zip.readUInt32LE(end + 16);
  const entries = new Map<string, Buffer>();
  for (let index = 0; index < count; index += 1) {
    if (zip.readUInt32LE(cursor) !== 0x02014b50) throw new Error('ZIP central directory is invalid');
    const method = zip.readUInt16LE(cursor + 10);
    const compressedSize = zip.readUInt32LE(cursor + 20);
    const filenameLength = zip.readUInt16LE(cursor + 28);
    const extraLength = zip.readUInt16LE(cursor + 30);
    const commentLength = zip.readUInt16LE(cursor + 32);
    const localOffset = zip.readUInt32LE(cursor + 42);
    const name = zip.subarray(cursor + 46, cursor + 46 + filenameLength).toString('utf8');
    if (zip.readUInt32LE(localOffset) !== 0x04034b50) throw new Error('ZIP local entry is invalid');
    const localNameLength = zip.readUInt16LE(localOffset + 26);
    const localExtraLength = zip.readUInt16LE(localOffset + 28);
    const start = localOffset + 30 + localNameLength + localExtraLength;
    const compressed = zip.subarray(start, start + compressedSize);
    const value = method === 0 ? compressed : method === 8 ? inflateRawSync(compressed)
      : (() => { throw new Error(`unsupported ZIP method ${method}`); })();
    entries.set(name, value);
    cursor += 46 + filenameLength + extraLength + commentLength;
  }
  return entries;
}


function parseAttachmentCsv(value: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = []; let cell = ''; let quoted = false;
  const text = value.replace(/^\uFEFF/, '');
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i += 1; } else quoted = !quoted;
    } else if (c === ',' && !quoted) { row.push(cell); cell = ''; }
    else if ((c === '\r' || c === '\n') && !quoted) {
      row.push(cell); rows.push(row); row = []; cell = '';
      if (c === '\r' && text[i + 1] === '\n') i += 1;
    } else cell += c;
  }
  if (quoted) throw new Error('Invalid attachment CSV');
  if (cell || row.length) rows.push([...row, cell]);
  return rows;
}
