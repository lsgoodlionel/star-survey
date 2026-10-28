import { z } from 'zod';
import type { ApiClient } from './http';

const contactKindSchema = z.enum(['staff', 'org_member', 'respondent']);
const dedupeKeySchema = z.enum(['email', 'phone', 'employee_id', 'external_id']);

export const contactListViewSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  kind: contactKindSchema,
  dedupeKey: dedupeKeySchema,
  createdAt: z.string().datetime({ offset: true }),
});

export const contactViewSchema = z.object({
  id: z.string().uuid(),
  kind: contactKindSchema,
  listId: z.string().uuid(),
  orgUnitId: z.string().uuid().nullable(),
  displayName: z.string(),
  email: z.string().nullable(),
  phone: z.string().nullable(),
  employeeId: z.string().nullable(),
  identity: z.object({ provider: z.string(), appId: z.string(), externalId: z.string() }).nullable(),
  source: z.string(),
  status: z.string(),
  tags: z.array(z.string()),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
});

export const contactPageSchema = z.object({
  items: z.array(contactViewSchema),
  nextCursor: z.string().nullable(),
});

export type ContactListView = z.infer<typeof contactListViewSchema>;
export type ContactView = z.infer<typeof contactViewSchema>;
export type ContactKind = z.infer<typeof contactKindSchema>;
export type DedupeKey = z.infer<typeof dedupeKeySchema>;

export interface CreateContactListInput {
  name: string;
  kind: ContactKind;
  dedupeKey: DedupeKey;
}

export interface CreateContactInput {
  listId: string;
  displayName: string;
  email?: string | null;
  phone?: string | null;
}

export const contactListsQueryKey = (tenantId: string) => ['contact-lists', tenantId] as const;
export const contactsQueryKey = (tenantId: string, listId: string) => ['contacts', tenantId, listId] as const;

export function listContactLists(api: ApiClient, signal?: AbortSignal) {
  return api.request({ path: '/v1/contact-lists', schema: z.array(contactListViewSchema), signal });
}

export function createContactList(api: ApiClient, input: CreateContactListInput, signal?: AbortSignal) {
  return api.request({ path: '/v1/contact-lists', method: 'POST', body: input, schema: contactListViewSchema, signal });
}

export function listContacts(api: ApiClient, listId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/contacts?listId=${encodeURIComponent(listId)}`,
    schema: contactPageSchema,
    signal,
  });
}

export function createRespondentContact(api: ApiClient, input: CreateContactInput, signal?: AbortSignal) {
  return api.request({
    path: '/v1/contacts',
    method: 'POST',
    body: { ...input, kind: 'respondent' },
    schema: contactViewSchema,
    signal,
  });
}
