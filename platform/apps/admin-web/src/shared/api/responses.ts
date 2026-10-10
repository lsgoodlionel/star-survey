import { z } from 'zod';
import type { ApiClient } from './http';

export const responseStateSchema = z.enum(['in_progress', 'engine_completed', 'deleted']);
export const answersStatusSchema = z.enum(['available', 'deleted', 'archived', 'missing']);

const responseCountsSchema = z.object({
  inProgress: z.number().int().nonnegative(),
  engineCompleted: z.number().int().nonnegative(),
  deleted: z.number().int().nonnegative(),
});

export const responseSummarySchema = z.object({
  surveyId: z.string().uuid(),
  versions: z.array(
    z.object({
      version: z.number().int().positive(),
      counts: responseCountsSchema,
    }),
  ),
  total: responseCountsSchema,
});

export const responseRowSchema = z.object({
  version: z.number().int().positive(),
  engineInstanceId: z.string().min(1),
  engineSid: z.number().int().positive(),
  generation: z.string().min(1),
  responseId: z.number().int().positive(),
  state: responseStateSchema,
  startedAt: z.string().datetime({ offset: true }),
  completedAt: z.string().datetime({ offset: true }).nullable(),
  deletedAt: z.string().datetime({ offset: true }).nullable(),
  answersStatus: answersStatusSchema,
  answers: z.record(z.string(), z.string().nullable()).nullable(),
});

export const responsePageSchema = z.object({
  items: z.array(responseRowSchema),
  nextCursor: z.string().nullable(),
  sensitiveRevealed: z.boolean(),
});

export type ResponseState = z.infer<typeof responseStateSchema>;
export type ResponseSummary = z.infer<typeof responseSummarySchema>;
export type ResponseRow = z.infer<typeof responseRowSchema>;
export type ResponsePage = z.infer<typeof responsePageSchema>;

export interface ResponseFilters {
  state?: ResponseState | null;
  version?: number | null;
  startedFrom?: string | null;
  startedTo?: string | null;
  completedFrom?: string | null;
  completedTo?: string | null;
  limit?: number;
  cursor?: string | null;
}

export interface NormalizedResponseFilters {
  state: ResponseState | null;
  version: number | null;
  startedFrom: string | null;
  startedTo: string | null;
  completedFrom: string | null;
  completedTo: string | null;
  limit: number;
  cursor: string | null;
}

export function normalizeResponseFilters(filters: ResponseFilters = {}): NormalizedResponseFilters {
  return {
    state: filters.state ?? null,
    version: filters.version ?? null,
    startedFrom: nonBlank(filters.startedFrom),
    startedTo: nonBlank(filters.startedTo),
    completedFrom: nonBlank(filters.completedFrom),
    completedTo: nonBlank(filters.completedTo),
    limit: filters.limit ?? 50,
    cursor: filters.cursor?.trim() ? filters.cursor : null,
  };
}

const responseQueryRoot = (tenantId: string, surveyId: string) =>
  ['responses', tenantId, surveyId] as const;

export const responseSummaryQueryKey = (tenantId: string, surveyId: string) =>
  [...responseQueryRoot(tenantId, surveyId), 'summary'] as const;

export const responsePageQueryKey = (
  tenantId: string,
  surveyId: string,
  filters: ResponseFilters = {},
) => [...responseQueryRoot(tenantId, surveyId), 'page', normalizeResponseFilters(filters)] as const;

export function getResponseSummary(api: ApiClient, surveyId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/responses/summary`,
    schema: responseSummarySchema,
    signal,
  });
}

export function listResponses(
  api: ApiClient,
  surveyId: string,
  filters: ResponseFilters = {},
  signal?: AbortSignal,
) {
  const normalized = normalizeResponseFilters(filters);
  const search = new URLSearchParams();
  if (normalized.state) search.set('state', normalized.state);
  if (normalized.version !== null) search.set('version', String(normalized.version));
  if (normalized.startedFrom) search.set('startedFrom', normalized.startedFrom);
  if (normalized.startedTo) search.set('startedTo', normalized.startedTo);
  if (normalized.completedFrom) search.set('completedFrom', normalized.completedFrom);
  if (normalized.completedTo) search.set('completedTo', normalized.completedTo);
  search.set('limit', String(normalized.limit));
  if (normalized.cursor) search.set('cursor', normalized.cursor);

  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/responses?${search.toString()}`,
    schema: responsePageSchema,
    signal,
  });
}

function nonBlank(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}
