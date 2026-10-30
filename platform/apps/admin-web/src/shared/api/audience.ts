import { z } from 'zod';
import type { ApiClient } from './http';

export const surveyAudienceViewSchema = z.object({
  surveyId: z.string().uuid(),
  listId: z.string().uuid().nullable(),
  orgUnitId: z.string().uuid().nullable(),
  includeDescendants: z.boolean(),
  tag: z.string().nullable(),
  updatedBy: z.string(),
  updatedAt: z.string().datetime({ offset: true }),
});

export const audienceSummarySchema = z.object({
  audience: surveyAudienceViewSchema,
  recipients: z.number().int().nonnegative(),
});

export type AudienceSummary = z.infer<typeof audienceSummarySchema>;
export interface SetAudienceInput {
  listId?: string | null;
  orgUnitId?: string | null;
  includeDescendants?: boolean;
  tag?: string | null;
}

export const audienceQueryKey = (tenantId: string, surveyId: string) =>
  ['survey-audience', tenantId, surveyId] as const;

export function getSurveyAudience(api: ApiClient, surveyId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/audience`,
    schema: audienceSummarySchema,
    signal,
  });
}

export function setSurveyAudience(api: ApiClient, surveyId: string, input: SetAudienceInput, signal?: AbortSignal) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/audience`,
    method: 'PUT',
    body: input,
    schema: audienceSummarySchema,
    signal,
  });
}
