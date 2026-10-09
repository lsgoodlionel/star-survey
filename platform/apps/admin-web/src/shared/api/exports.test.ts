import { expect, test, vi } from 'vitest';
import {
  createExportClient,
  exportJobQueryKey,
  exportJobSchema,
} from './exports';
import { binaryResponse, jsonResponse } from '../../test/server';

const surveyId = '10000000-0000-4000-8000-000000000001';
const jobId = '20000000-0000-4000-8000-000000000001';
const job = {
  jobId,
  surveyId,
  format: 'xlsx',
  templateVersion: 'default',
  filter: { states: ['engine_completed'] },
  status: 'completed',
  sensitiveRevealed: false,
  totalRows: 12,
  processedRows: 12,
  attempts: 1,
  error: null,
  fileSize: 4096,
  sha256: 'a'.repeat(64),
  createdAt: '2026-10-09T08:00:00Z',
  startedAt: '2026-10-09T08:00:01Z',
  finishedAt: '2026-10-09T08:00:02Z',
  expiresAt: '2026-10-16T08:00:02Z',
};

test('validates the complete export job contract', () => {
  expect(exportJobSchema.parse(job)).toEqual(job);
  expect(() => exportJobSchema.parse({ ...job, status: 'done' })).toThrow();
  expect(() => exportJobSchema.parse({ ...job, processedRows: -1 })).toThrow();
  expect(() => exportJobSchema.parse({ ...job, sha256: 'short' })).toThrow();
});

test('creates an export from a 202 response with the exact body and idempotency key', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(job, 202));
  const client = createExportClient({ fetchImpl, getToken: () => 'jwt-token' });

  await expect(
    client.create(
      'survey/id',
      { format: 'xlsx', filter: { states: ['engine_completed'] }, templateVersion: 'default' },
      { idempotencyKey: 'export-request-1' },
    ),
  ).resolves.toEqual(job);

  const [path, init] = fetchImpl.mock.calls[0] ?? [];
  expect(path).toBe('/v1/surveys/survey%2Fid/exports');
  expect(init?.method).toBe('POST');
  expect(init?.body).toBe(
    JSON.stringify({
      format: 'xlsx',
      filter: { states: ['engine_completed'] },
      templateVersion: 'default',
    }),
  );
  const headers = new Headers(init?.headers);
  expect(headers.get('Authorization')).toBe('Bearer jwt-token');
  expect(headers.get('Idempotency-Key')).toBe('export-request-1');
});

test('loads and cancels an export job through typed responses', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse(job));
  const client = createExportClient({ fetchImpl });

  await client.get('job/id');
  await client.cancel('job/id');

  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method])).toEqual([
    ['/v1/exports/job%2Fid', 'GET'],
    ['/v1/exports/job%2Fid/cancel', 'POST'],
  ]);
});

test('export query keys are isolated by tenant survey and job', () => {
  const keys = [
    exportJobQueryKey('tenant-a', surveyId, jobId),
    exportJobQueryKey('tenant-b', surveyId, jobId),
    exportJobQueryKey('tenant-a', '30000000-0000-4000-8000-000000000001', jobId),
    exportJobQueryKey('tenant-a', surveyId, '40000000-0000-4000-8000-000000000001'),
  ];
  expect(new Set(keys.map((key) => JSON.stringify(key)))).toHaveLength(keys.length);
});

test('downloads a successful export blob and validates its filename', async () => {
  const bytes = new Uint8Array([80, 75, 3, 4]);
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
    binaryResponse(bytes, {
      contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      headers: {
        'Content-Disposition': 'attachment; filename="survey-export.xlsx"',
        'X-Content-SHA256': 'b'.repeat(64),
      },
    }),
  );

  const download = await createExportClient({ fetchImpl }).download('job/id');

  expect(download.filename).toBe('survey-export.xlsx');
  expect(download.contentType).toBe(
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  );
  expect(download.sha256).toBe('b'.repeat(64));
  expect(new Uint8Array(await download.blob.arrayBuffer())).toEqual(bytes);
});

test.each([
  ['missing filename', binaryResponse('file', { contentType: 'application/zip' }), '文件名'],
  [
    'unsafe filename',
    binaryResponse('file', {
      contentType: 'application/zip',
      headers: { 'Content-Disposition': 'attachment; filename="../secret.zip"' },
    }),
    '文件名',
  ],
])('rejects export downloads with %s', async (_label, response, message) => {
  await expect(
    createExportClient({ fetchImpl: vi.fn<typeof fetch>().mockResolvedValue(response) }).download(jobId),
  ).rejects.toThrow(message);
});

test('rejects a non-success export download status before reading the body', async () => {
  await expect(
    createExportClient({
      fetchImpl: vi.fn<typeof fetch>().mockResolvedValue(binaryResponse('not ready', { status: 409 })),
    }).download(jobId),
  ).rejects.toMatchObject({ status: 409 });
});
