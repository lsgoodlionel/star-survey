import { z } from 'zod';
import type { ApiClient } from './http';

export const surveyViewSchema = z.object({
  id: z.string().uuid(),
  title: z.string(),
  status: z.enum(['draft', 'publishing', 'published', 'publish_failed', 'pending_reconciliation']),
  draftVersion: z.number().int().positive(),
  publishedVersion: z.number().int().positive().nullable(),
  lastPublish: z.unknown().nullable(),
});

export type SurveyView = z.infer<typeof surveyViewSchema>;

export const draftViewSchema = z.object({
  surveyId: z.string().uuid(),
  version: z.number().int().positive(),
  definition: z.unknown(),
});

export type DraftView = z.infer<typeof draftViewSchema>;

export function createBlankDefinition(title: string): Record<string, unknown> {
  return {
    definitionVersion: 2,
    title,
    description: '',
    language: 'zh-Hans',
    theme: 'fruity_twentythree',
    settings: {
      anonymized: 'N',
      datestamp: 'Y',
      savetimings: 'N',
      ipaddr: 'N',
      refurl: 'N',
      allowsave: 'Y',
      allowprev: 'Y',
      alloweditaftercompletion: 'N',
      format: 'G',
      questionindex: '0',
      usecaptcha: 'N',
      shownoanswer: 'N',
    },
    groups: [
      {
        uuid: globalThis.crypto.randomUUID(),
        title: '第一题组',
        questions: [
          {
            uuid: globalThis.crypto.randomUUID(),
            code: 'QNOTE',
            type: 'X',
            text: '请在这里添加问卷说明',
          },
        ],
      },
    ],
  };
}

export function createSurvey(api: ApiClient, parentId: string, title: string) {
  return api.request({
    path: '/v1/surveys',
    method: 'POST',
    body: { parentId, definition: createBlankDefinition(title) },
    schema: surveyViewSchema,
  });
}

export function getSurvey(api: ApiClient, id: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(id)}`,
    schema: surveyViewSchema,
    signal,
  });
}

export function getSurveyDraft(api: ApiClient, id: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(id)}/draft`,
    schema: draftViewSchema,
    signal,
  });
}

export function saveDraft(
  api: ApiClient,
  id: string,
  expectedVersion: number,
  definition: unknown,
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(id)}/draft`,
    method: 'PUT',
    body: { expectedVersion, definition },
    schema: draftViewSchema,
    signal,
  });
}
