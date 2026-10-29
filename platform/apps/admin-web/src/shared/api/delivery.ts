import { z } from 'zod';
import { ApiError, apiErrorForStatus, unavailableApiError, unexpectedApiError } from './errors';
import { withRequestTimeout, type ApiClient } from './http';

export const linkViewSchema = z.object({
  id: z.string().uuid(),
  surveyId: z.string().uuid(),
  label: z.string(),
  signedParams: z.record(z.string(), z.string()),
  url: z.string().url(),
  shortUrl: z.string().url().nullable(),
  expiresAt: z.string().datetime({ offset: true }).nullable(),
  revokedAt: z.string().datetime({ offset: true }).nullable(),
  createdAt: z.string().datetime({ offset: true }),
});

export type LinkView = z.infer<typeof linkViewSchema>;

export const deliveryTaskSchema = z.object({
  id: z.string().uuid(),
  surveyId: z.string().uuid(),
  channel: z.enum(['email', 'sms']),
  kind: z.string(),
  status: z.string(),
  sourceTaskId: z.string().uuid().nullable(),
  recipientCount: z.number().int().nonnegative(),
  sentCount: z.number().int().nonnegative(),
  failedCount: z.number().int().nonnegative(),
  skippedCount: z.number().int().nonnegative(),
  billedUnits: z.number().int().nonnegative(),
  attempts: z.number().int().nonnegative(),
  lastError: z.string().nullable(),
  createdAt: z.string().datetime({ offset: true }),
  finishedAt: z.string().datetime({ offset: true }).nullable(),
});

export const deliveryRecipientSchema = z.object({
  id: z.string().uuid(),
  address: z.string(),
  displayName: z.string().nullable(),
  state: z.string(),
  attempts: z.number().int().nonnegative(),
  remindersSent: z.number().int().nonnegative(),
  lastError: z.string().nullable(),
  sentAt: z.string().datetime({ offset: true }).nullable(),
  lastReminderAt: z.string().datetime({ offset: true }).nullable(),
});

export const deliveryReceiptSchema = z.object({
  recipientId: z.string().uuid(),
  provider: z.string(),
  kind: z.enum(['delivered', 'bounced', 'failed', 'complaint', 'unsubscribed']),
  billable: z.boolean(),
  occurredAt: z.string().datetime({ offset: true }).nullable(),
  receivedAt: z.string().datetime({ offset: true }),
});

export const reminderRuleSchema = z.object({
  id: z.string().uuid(),
  surveyId: z.string().uuid(),
  sourceTaskId: z.string().uuid(),
  firstDelaySeconds: z.number().int().nonnegative(),
  intervalSeconds: z.number().int().positive(),
  maxReminders: z.number().int().min(1).max(10),
  subject: z.string().nullable(),
  enabled: z.boolean(),
  createdAt: z.string().datetime({ offset: true }),
});

export type DeliveryTask = z.infer<typeof deliveryTaskSchema>;
export type DeliveryRecipient = z.infer<typeof deliveryRecipientSchema>;
export type DeliveryReceipt = z.infer<typeof deliveryReceiptSchema>;
export type ReminderRule = z.infer<typeof reminderRuleSchema>;

export interface CreateAudienceDeliveryTaskInput {
  channel: 'email';
  linkId?: string | null;
  subject?: string | null;
  bodyTemplate: string;
  idempotencyKey?: string | null;
}

export interface CreateDeliveryLinkInput {
  label: string;
  params?: Record<string, string>;
  ttlSeconds?: number | null;
  shortLink: boolean;
}

export interface CreateReminderRuleInput {
  sourceTaskId: string;
  firstDelaySeconds: number;
  intervalSeconds: number;
  maxReminders: number;
  subject?: string | null;
  bodyTemplate: string;
}

export interface DeliveryQrOptions {
  format?: 'png' | 'svg';
  moduleSize?: number;
  signal?: AbortSignal;
}

export interface DeliveryBinaryClientOptions {
  fetchImpl?: typeof fetch;
  getToken?: () => string | null;
  onUnauthorized?: (requestToken: string | null) => void | Promise<void>;
  timeoutMs?: number;
}

export const deliveryLinksQueryKey = (tenantId: string, surveyId: string) =>
  ['delivery-links', tenantId, surveyId] as const;

export const deliveryTasksQueryKey = (tenantId: string, surveyId: string) =>
  ['delivery-tasks', tenantId, surveyId] as const;
export const deliveryTaskQueryKey = (tenantId: string, surveyId: string, taskId: string) =>
  [...deliveryTasksQueryKey(tenantId, surveyId), taskId] as const;
export const deliveryReceiptsQueryKey = (tenantId: string, taskId: string) =>
  ['delivery-receipts', tenantId, taskId] as const;
export const reminderRulesQueryKey = (tenantId: string, surveyId: string) =>
  ['delivery-reminder-rules', tenantId, surveyId] as const;

export function listDeliveryLinks(api: ApiClient, surveyId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/delivery/surveys/${encodeURIComponent(surveyId)}/links`,
    schema: z.array(linkViewSchema),
    signal,
  });
}

export function createDeliveryLink(
  api: ApiClient,
  surveyId: string,
  input: CreateDeliveryLinkInput,
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/delivery/surveys/${encodeURIComponent(surveyId)}/links`,
    method: 'POST',
    body: input,
    schema: linkViewSchema,
    signal,
  });
}

export function getDeliveryLink(api: ApiClient, linkId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/delivery/links/${encodeURIComponent(linkId)}`,
    schema: linkViewSchema,
    signal,
  });
}

export function revokeDeliveryLink(api: ApiClient, linkId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/delivery/links/${encodeURIComponent(linkId)}`,
    method: 'DELETE',
    schema: linkViewSchema,
    signal,
  });
}

export function createAudienceDeliveryTask(
  api: ApiClient,
  surveyId: string,
  input: CreateAudienceDeliveryTaskInput,
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/delivery-tasks`,
    method: 'POST',
    body: input,
    schema: deliveryTaskSchema,
    signal,
  });
}

export function getDeliveryTask(api: ApiClient, taskId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/delivery/tasks/${encodeURIComponent(taskId)}`,
    schema: deliveryTaskSchema,
    signal,
  });
}

export function listSurveyDeliveryTasks(api: ApiClient, surveyId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/delivery-tasks?limit=50`,
    schema: z.array(deliveryTaskSchema),
    signal,
  });
}

export function listReminderRules(api: ApiClient, surveyId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/delivery/surveys/${encodeURIComponent(surveyId)}/reminder-rules`,
    schema: z.array(reminderRuleSchema),
    signal,
  });
}

export function createReminderRule(
  api: ApiClient,
  surveyId: string,
  input: CreateReminderRuleInput,
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/delivery/surveys/${encodeURIComponent(surveyId)}/reminder-rules`,
    method: 'POST',
    body: input,
    schema: reminderRuleSchema,
    signal,
  });
}

export function setReminderRuleEnabled(
  api: ApiClient,
  surveyId: string,
  ruleId: string,
  enabled: boolean,
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/delivery/surveys/${encodeURIComponent(surveyId)}/reminder-rules/${encodeURIComponent(ruleId)}`,
    method: 'PATCH',
    body: { enabled },
    schema: reminderRuleSchema,
    signal,
  });
}

export function listDeliveryRecipients(api: ApiClient, taskId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/delivery/tasks/${encodeURIComponent(taskId)}/recipients`,
    schema: z.array(deliveryRecipientSchema),
    signal,
  });
}

export function listDeliveryReceipts(api: ApiClient, taskId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/delivery/tasks/${encodeURIComponent(taskId)}/receipts?limit=50`,
    schema: z.array(deliveryReceiptSchema),
    signal,
  });
}

export function cancelDeliveryTask(api: ApiClient, taskId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/delivery/tasks/${encodeURIComponent(taskId)}/cancel`,
    method: 'POST',
    schema: deliveryTaskSchema,
    signal,
  });
}

export async function fetchDeliveryQr(
  client: DeliveryBinaryClientOptions,
  linkId: string,
  options: DeliveryQrOptions = {},
): Promise<Blob> {
  const format = options.format ?? 'png';
  const search = new URLSearchParams({ format });
  if (options.moduleSize !== undefined) search.set('moduleSize', String(options.moduleSize));
  const token = client.getToken?.() ?? null;
  const headers = new Headers();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  try {
    return await withRequestTimeout(
      async (signal) => {
        const response = await (client.fetchImpl ?? fetch)(
          `/v1/delivery/links/${encodeURIComponent(linkId)}/qr?${search.toString()}`,
          { headers, credentials: 'same-origin', signal },
        );
        if (response.status === 401) await client.onUnauthorized?.(token);
        if (!response.ok) throw apiErrorForStatus(response.status, await readErrorPayload(response));
        const expectedType = format === 'svg' ? 'image/svg+xml' : 'image/png';
        const contentType = response.headers.get('Content-Type')?.split(';', 1)[0]?.trim().toLowerCase();
        if (contentType !== expectedType) throw new Error('二维码响应格式无效');
        return response.blob();
      },
      options.signal,
      client.timeoutMs,
    );
  } catch (error) {
    if (error instanceof ApiError || (error instanceof Error && error.message === '二维码响应格式无效')) {
      throw error;
    }
    if (error instanceof DOMException && error.name === 'AbortError') throw unavailableApiError();
    if (error instanceof TypeError) throw unavailableApiError();
    throw unexpectedApiError();
  }
}

async function readErrorPayload(response: Response): Promise<unknown> {
  if (!response.headers.get('Content-Type')?.includes('application/json')) return undefined;
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}
