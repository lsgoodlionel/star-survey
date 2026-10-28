import { expect, test, vi } from 'vitest';
import { createApiClient } from './http';
import {
  contactListViewSchema,
  contactViewSchema,
  createContactList,
  createRespondentContact,
  listContactLists,
  listContacts,
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

test('validates contact list and contact contracts', () => {
  expect(contactListViewSchema.parse(list)).toEqual(list);
  expect(contactViewSchema.parse(contact)).toEqual(contact);
  expect(() => contactListViewSchema.parse({ ...list, dedupeKey: 'name' })).toThrow();
  expect(() => contactViewSchema.parse({ ...contact, email: 1 })).toThrow();
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
