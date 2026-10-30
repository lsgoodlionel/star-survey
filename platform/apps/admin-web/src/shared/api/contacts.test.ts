import { expect, test, vi } from 'vitest';
import { createApiClient } from './http';
import {
  contactListViewSchema,
  contactImportJobSchema,
  contactImportOutcomeSchema,
  contactViewSchema,
  createContactList,
  createRespondentContact,
  listContactLists,
  getContactImport,
  listContactImportOutcomes,
  listContacts,
  submitContactImport,
} from './contacts';
import { jsonResponse } from '../../test/server';

const listId = '10000000-0000-4000-8000-000000000001';
const contactId = '20000000-0000-4000-8000-000000000001';
const now = '2026-10-28T08:00:00Z';
const list = { id: listId, name: '华东客户', kind: 'respondent', dedupeKey: 'email', createdAt: now };
const contact = {
  id: contactId, kind: 'respondent', listId, orgUnitId: null, displayName: '王小明',
  email: 'wang@example.test', phone: null, employeeId: null, identity: null,
  source: 'manual', status: 'active', tags: [], createdAt: now, updatedAt: now,
};
const jobId = '30000000-0000-4000-8000-000000000001';
const importJob = {
  jobId, listId, status: 'completed', totalRows: 2, processedRows: 2,
  created: 1, updated: 0, duplicate: 0, invalid: 1,
};
const importOutcome = { rowNo: 2, outcome: 'invalid', reason: 'invalid_email', contactId: null };

test('validates contact list and contact contracts', () => {
  expect(contactListViewSchema.parse(list)).toEqual(list);
  expect(contactViewSchema.parse(contact)).toEqual(contact);
  expect(() => contactListViewSchema.parse({ ...list, dedupeKey: 'name' })).toThrow();
  expect(() => contactViewSchema.parse({ ...contact, email: 1 })).toThrow();
  expect(contactImportJobSchema.parse(importJob)).toEqual(importJob);
  expect(contactImportOutcomeSchema.parse(importOutcome)).toEqual(importOutcome);
  expect(contactImportOutcomeSchema.parse({ ...importOutcome, content: 'leak@example.test' })).toEqual(importOutcome);
});

test('sends typed list and respondent contact requests', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    if (init?.method === 'POST' && input === '/v1/contact-lists') return jsonResponse(list, 201);
    if (init?.method === 'POST') return jsonResponse(contact, 201);
    if (String(input).startsWith('/v1/contacts')) return jsonResponse({ items: [contact], nextCursor: null });
    return jsonResponse([list]);
  });
  const api = createApiClient({ fetchImpl });

  await listContactLists(api);
  await createContactList(api, { name: '华东客户', kind: 'respondent', dedupeKey: 'email' });
  await listContacts(api, 'list/id');
  await createRespondentContact(api, { listId, displayName: '王小明', email: 'wang@example.test' });

  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method, init?.body])).toEqual([
    ['/v1/contact-lists', 'GET', undefined],
    ['/v1/contact-lists', 'POST', JSON.stringify({ name: '华东客户', kind: 'respondent', dedupeKey: 'email' })],
    ['/v1/contacts?listId=list%2Fid', 'GET', undefined],
    ['/v1/contacts', 'POST', JSON.stringify({ listId, displayName: '王小明', email: 'wang@example.test', kind: 'respondent' })],
  ]);
});

test('submits idempotent imports and reads PII-free progress and outcomes', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    if (init?.method === 'POST') return jsonResponse(importJob, 202);
    if (String(input).endsWith('/outcomes?fromRow=10&limit=20')) return jsonResponse([importOutcome]);
    return jsonResponse(importJob);
  });
  const api = createApiClient({ fetchImpl });

  await submitContactImport(api, 'list/id', {
    format: 'csv', content: 'name,email\\n王同学,wang@example.test\\n', idempotencyKey: 'import-1',
  });
  await getContactImport(api, jobId);
  await listContactImportOutcomes(api, jobId, { fromRow: 10, limit: 20 });

  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method, init?.body, new Headers(init?.headers).get('Idempotency-Key')])).toEqual([
    [`/v1/contact-lists/list%2Fid/imports`, 'POST', JSON.stringify({ format: 'csv', content: 'name,email\\n王同学,wang@example.test\\n' }), 'import-1'],
    [`/v1/contact-imports/${jobId}`, 'GET', undefined, null],
    [`/v1/contact-imports/${jobId}/outcomes?fromRow=10&limit=20`, 'GET', undefined, null],
  ]);
});
