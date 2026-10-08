import { z } from 'zod';
import type { ApiClient } from './http';
import { draftViewSchema } from './surveys';

const importProblemSchema = z.object({
  line: z.number().int().positive(),
  code: z.string().min(1),
  message: z.string().min(1),
});

const previewOptionSchema = z.object({
  code: z.string().min(1),
  text: z.string(),
});

const previewQuestionSchema = z.object({
  index: z.number().int().nonnegative(),
  line: z.number().int().positive(),
  code: z.string().min(1),
  type: z.string().min(1),
  typeName: z.string().min(1),
  typeInferred: z.boolean(),
  text: z.string(),
  mandatory: z.boolean(),
  options: z.array(previewOptionSchema),
  importable: z.boolean(),
  problems: z.array(importProblemSchema),
});

export const importPreviewSchema = z.object({
  lineCount: z.number().int().nonnegative(),
  questions: z.array(previewQuestionSchema),
  problems: z.array(importProblemSchema),
});

export type ImportPreview = z.infer<typeof importPreviewSchema>;

export function previewSurveyImport(
  api: ApiClient,
  surveyId: string,
  text: string,
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/import/preview`,
    method: 'POST',
    body: { text },
    schema: importPreviewSchema,
    signal,
  });
}

export function confirmSurveyImport(
  api: ApiClient,
  surveyId: string,
  request: {
    expectedVersion: number;
    text: string;
    accept: number[];
    groupUuid: string | null;
  },
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/surveys/${encodeURIComponent(surveyId)}/import`,
    method: 'POST',
    body: request,
    schema: draftViewSchema,
    signal,
  });
}
