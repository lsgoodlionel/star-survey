import { z } from 'zod';
import type { ApiClient } from './http';

export const approvalStatusSchema = z.enum([
  'pending',
  'approved',
  'rejected',
  'withdrawn',
  'voided',
  'published',
]);

export const approvalRequestSchema = z.object({
  id: z.string().uuid(),
  surveyId: z.string().uuid(),
  draftVersion: z.number().int().positive(),
  status: approvalStatusSchema,
  applicant: z.string(),
  submittedAt: z.string(),
  decidedBy: z.string().nullable(),
  decidedAt: z.string().nullable(),
  reason: z.string().nullable(),
});

export const approvalEventSchema = z.object({
  event: z.string(),
  actor: z.string(),
  draftVersion: z.number().int().positive(),
  reason: z.string().nullable(),
  publishRequestId: z.string().uuid().nullable(),
  at: z.string(),
});

export const approvalDetailSchema = z.object({
  request: approvalRequestSchema,
  history: z.array(approvalEventSchema),
});

const publishAttemptSchema = z.object({
  requestId: z.string().uuid(),
  engineInstanceId: z.string(),
  draftVersion: z.number().int().positive(),
  outcome: z.string(),
  gatewayStatus: z.number().int().nullable(),
  failedStage: z.string().nullable(),
  failures: z.array(z.string()),
  orphanEngineSid: z.number().int().nullable(),
  tries: z.number().int().nonnegative(),
  nextReconcileAt: z.string().nullable(),
  manualReviewAt: z.string().nullable(),
});

export const surveyOverviewSchema = z.object({
  id: z.string().uuid(),
  title: z.string(),
  status: z.enum(['draft', 'publishing', 'published', 'publish_failed', 'pending_reconciliation']),
  draftVersion: z.number().int().positive(),
  publishedVersion: z.number().int().positive().nullable(),
  lastPublish: publishAttemptSchema.nullable(),
});

const questionFieldSchema = z.object({
  questionUuid: z.string().uuid(),
  code: z.string(),
  type: z.string(),
  fieldname: z.string(),
  aid: z.string(),
  scale: z.number().int(),
});

export const publishedVersionSchema = z.object({
  surveyId: z.string().uuid(),
  version: z.number().int().positive(),
  requestId: z.string().uuid(),
  draftVersion: z.number().int().positive(),
  engineInstanceId: z.string(),
  engineSid: z.number().int().positive(),
  compilerVersion: z.string(),
  fingerprintVersion: z.string(),
  fingerprint: z.string(),
  language: z.string(),
  enginePublishedAt: z.string(),
  publishedBy: z.string(),
  publishedAt: z.string(),
  fields: z.array(questionFieldSchema),
  definition: z.unknown(),
  live: z.boolean(),
  supersededAt: z.string().nullable(),
  engineClosedAt: z.string().nullable(),
});

export type ApprovalStatus = z.infer<typeof approvalStatusSchema>;
export type ApprovalRequest = z.infer<typeof approvalRequestSchema>;
export type ApprovalDetail = z.infer<typeof approvalDetailSchema>;
export type SurveyOverview = z.infer<typeof surveyOverviewSchema>;
export type PublishedVersion = z.infer<typeof publishedVersionSchema>;

const publishQueryRoot = (tenantId: string, surveyId: string) =>
  ['survey', tenantId, surveyId] as const;

export const surveyOverviewQueryKey = (tenantId: string, surveyId: string) =>
  [...publishQueryRoot(tenantId, surveyId), 'overview'] as const;
export const approvalRequestsQueryKey = (tenantId: string, surveyId: string) =>
  [...publishQueryRoot(tenantId, surveyId), 'approval-requests'] as const;
export const versionsQueryKey = (tenantId: string, surveyId: string) =>
  [...publishQueryRoot(tenantId, surveyId), 'versions'] as const;
export const versionQueryKey = (tenantId: string, surveyId: string, version: number) =>
  [...versionsQueryKey(tenantId, surveyId), version] as const;

export function getSurveyOverview(api: ApiClient, surveyId: string, signal?: AbortSignal) {
  return api.request({ path: `/v1/surveys/${encodeURIComponent(surveyId)}`, schema: surveyOverviewSchema, signal });
}

export function listApprovalRequests(api: ApiClient, surveyId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/approval-requests`,
    schema: z.array(approvalRequestSchema),
    signal,
  });
}

export function getApprovalDetail(api: ApiClient, approvalId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/publish-approvals/${encodeURIComponent(approvalId)}`,
    schema: approvalDetailSchema,
    signal,
  });
}

export function submitApproval(api: ApiClient, surveyId: string, draftVersion: number) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/approval-requests`,
    method: 'POST',
    body: { draftVersion },
    schema: approvalRequestSchema,
  });
}

export function approveRequest(api: ApiClient, approvalId: string) {
  return approvalAction(api, approvalId, 'approve');
}

export function rejectRequest(api: ApiClient, approvalId: string, reason: string) {
  return approvalAction(api, approvalId, 'reject', { reason });
}

export function withdrawRequest(api: ApiClient, approvalId: string) {
  return approvalAction(api, approvalId, 'withdraw');
}

export function publishSurvey(api: ApiClient, surveyId: string) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/publish`,
    method: 'POST',
    schema: z.unknown(),
    timeoutMs: 180_000,
  });
}

export function listPublishedVersions(api: ApiClient, surveyId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/versions`,
    schema: z.array(publishedVersionSchema),
    signal,
  });
}

export function getPublishedVersion(
  api: ApiClient,
  surveyId: string,
  version: number,
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/versions/${version}`,
    schema: publishedVersionSchema,
    signal,
  });
}

function approvalAction(
  api: ApiClient,
  approvalId: string,
  action: 'approve' | 'reject' | 'withdraw',
  body?: unknown,
) {
  return api.request({
    path: `/v1/publish-approvals/${encodeURIComponent(approvalId)}/${action}`,
    method: 'POST',
    body,
    schema: approvalRequestSchema,
  });
}
