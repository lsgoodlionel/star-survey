import { z } from 'zod';
import type { ApiClient } from './http';

const contactKindSchema = z.enum(['staff', 'org_member', 'respondent']);
const dedupeKeySchema = z.enum(['email', 'phone', 'employee_id', 'external_id']);
export const importFormatSchema = z.enum(['csv', 'json']);
const importStatusSchema = z.enum(['queued', 'running', 'completed', 'failed']);
const importOutcomeSchema = z.enum(['created', 'updated', 'duplicate', 'invalid']);

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

export const orgUnitViewSchema = z.object({
  id: z.string().uuid(),
  parentId: z.string().uuid().nullable(),
  name: z.string(),
  externalRef: z.string().nullable(),
});

export const contactTagViewSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
});

/** Import results intentionally exclude submitted row content and contact addresses. */
export const contactImportJobSchema = z.object({
  jobId: z.string().uuid(),
  listId: z.string().uuid(),
  status: importStatusSchema,
  totalRows: z.number().int().nonnegative(),
  processedRows: z.number().int().nonnegative(),
  created: z.number().int().nonnegative(),
  updated: z.number().int().nonnegative(),
  duplicate: z.number().int().nonnegative(),
  invalid: z.number().int().nonnegative(),
});

export const contactImportOutcomeSchema = z.object({
  rowNo: z.number().int().positive(),
  outcome: importOutcomeSchema,
  reason: z.string().nullable(),
  contactId: z.string().uuid().nullable(),
});

export type ContactListView = z.infer<typeof contactListViewSchema>;
export type ContactView = z.infer<typeof contactViewSchema>;
export type ContactKind = z.infer<typeof contactKindSchema>;
export type DedupeKey = z.infer<typeof dedupeKeySchema>;
export type ContactImportJob = z.infer<typeof contactImportJobSchema>;
export type ContactImportOutcome = z.infer<typeof contactImportOutcomeSchema>;
export type OrgUnitView = z.infer<typeof orgUnitViewSchema>;
export type ContactTagView = z.infer<typeof contactTagViewSchema>;

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
  orgUnitId?: string | null;
}

export interface SubmitContactImportInput {
  format: z.infer<typeof importFormatSchema>;
  content: string;
  idempotencyKey?: string;
}

export const contactListsQueryKey = (tenantId: string) => ['contact-lists', tenantId] as const;
export const contactsQueryKey = (tenantId: string, listId: string) => ['contacts', tenantId, listId] as const;
export const contactImportQueryKey = (tenantId: string, jobId: string) =>
  ['contact-imports', tenantId, jobId] as const;
export const contactImportOutcomesQueryKey = (tenantId: string, jobId: string, fromRow: number, limit: number) =>
  [...contactImportQueryKey(tenantId, jobId), 'outcomes', fromRow, limit] as const;
export const orgUnitsQueryKey = (tenantId: string) => ['org-units', tenantId] as const;
export const contactTagsQueryKey = (tenantId: string) => ['contact-tags', tenantId] as const;

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

export function listOrgUnits(api: ApiClient, signal?: AbortSignal) {
  return api.request({ path: '/v1/org-units', schema: z.array(orgUnitViewSchema), signal });
}

export function createOrgUnit(api: ApiClient, input: { name: string; parentId?: string | null }, signal?: AbortSignal) {
  return api.request({
    path: '/v1/org-units', method: 'POST',
    body: { name: input.name, parentId: input.parentId ?? null, externalRef: null },
    schema: orgUnitViewSchema, signal,
  });
}

export function listContactTags(api: ApiClient, signal?: AbortSignal) {
  return api.request({ path: '/v1/contact-tags', schema: z.array(contactTagViewSchema), signal });
}

export function createContactTag(api: ApiClient, name: string, signal?: AbortSignal) {
  return api.request({ path: '/v1/contact-tags', method: 'POST', body: { name }, schema: contactTagViewSchema, signal });
}

export function assignContactTag(api: ApiClient, contactId: string, tagId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/contacts/${encodeURIComponent(contactId)}/tags`, method: 'POST', body: { tagId },
    schema: z.undefined(), acceptStatuses: [204], signal,
  });
}

export function disableContact(api: ApiClient, contactId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/contacts/${encodeURIComponent(contactId)}/disable`, method: 'POST', schema: contactViewSchema, signal,
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

export function submitContactImport(
  api: ApiClient,
  listId: string,
  input: SubmitContactImportInput,
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/contact-lists/${encodeURIComponent(listId)}/imports`,
    method: 'POST',
    body: { format: input.format, content: input.content },
    headers: input.idempotencyKey ? { 'Idempotency-Key': input.idempotencyKey } : undefined,
    acceptStatuses: [202],
    schema: contactImportJobSchema,
    signal,
  });
}

export function getContactImport(api: ApiClient, jobId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/contact-imports/${encodeURIComponent(jobId)}`,
    schema: contactImportJobSchema,
    signal,
  });
}

export function listContactImportOutcomes(
  api: ApiClient,
  jobId: string,
  options: { fromRow?: number; limit?: number; signal?: AbortSignal } = {},
) {
  const fromRow = options.fromRow ?? 1;
  const limit = options.limit ?? 100;
  return api.request({
    path: `/v1/contact-imports/${encodeURIComponent(jobId)}/outcomes?fromRow=${encodeURIComponent(fromRow)}&limit=${encodeURIComponent(limit)}`,
    schema: z.array(contactImportOutcomeSchema),
    signal: options.signal,
  });
}
