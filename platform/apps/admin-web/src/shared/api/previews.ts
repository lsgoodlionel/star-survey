import { z } from 'zod';
import { ApiError, apiErrorForStatus, unavailableApiError, unexpectedApiError } from './errors';
import { withRequestTimeout } from './http';

export const previewStatusSchema = z.enum([
  'creating',
  'ready',
  'closing',
  'closed',
  'failed',
  'cleanup_failed',
]);

export const previewSessionSchema = z.object({
  id: z.string().uuid(),
  requestId: z.string().uuid(),
  surveyId: z.string().uuid(),
  draftVersion: z.number().int().positive(),
  requestedBy: z.string().min(1),
  engineInstanceId: z.string().min(1),
  engineSid: z.number().int().positive().nullable(),
  generation: z.string().min(1).nullable(),
  previewUrl: z.string().url().nullable(),
  expiresAt: z.string().datetime({ offset: true }),
  status: previewStatusSchema,
  failure: z.string().nullable(),
  cleanupAttempts: z.number().int().nonnegative(),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
  closedAt: z.string().datetime({ offset: true }).nullable(),
});

export type PreviewSessionView = z.infer<typeof previewSessionSchema>;

export interface CreatePreviewInput {
  requestId: string;
  ttlSeconds: number;
}

export interface PreviewClientOptions {
  fetchImpl?: typeof fetch;
  getToken?: () => string | null;
  onUnauthorized?: (requestToken: string | null) => void | Promise<void>;
  timeoutMs?: number;
}

export interface PreviewClient {
  create(surveyId: string, input: CreatePreviewInput, signal?: AbortSignal): Promise<PreviewSessionView>;
  get(sessionId: string, signal?: AbortSignal): Promise<PreviewSessionView>;
  close(sessionId: string, signal?: AbortSignal): Promise<PreviewSessionView>;
}

export const previewSessionQueryKey = (tenantId: string, surveyId: string, sessionId: string) =>
  ['preview-session', tenantId, surveyId, sessionId] as const;

export function createPreviewClient(options: PreviewClientOptions = {}): PreviewClient {
  const fetchImpl = options.fetchImpl ?? fetch;

  async function request(path: string, init: RequestInit): Promise<PreviewSessionView> {
    const token = options.getToken?.() ?? null;
    const headers = new Headers({ Accept: 'application/json', ...init.headers });
    if (token) headers.set('Authorization', `Bearer ${token}`);
    try {
      return await withRequestTimeout(async (signal) => {
        const response = await fetchImpl(path, {
          ...init,
          headers,
          credentials: 'same-origin',
          signal,
        });
        if (response.status === 401) await options.onUnauthorized?.(token);
        const payload: unknown = await response.json().catch(() => undefined);
        const session = previewSessionSchema.safeParse(payload);
        if (session.success) return session.data;
        if (!response.ok) throw apiErrorForStatus(response.status, payload);
        throw unexpectedApiError();
      }, init.signal, options.timeoutMs);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error instanceof DOMException && error.name === 'AbortError') throw unavailableApiError();
      if (error instanceof TypeError) throw unavailableApiError();
      throw unexpectedApiError();
    }
  }

  return {
    create: (surveyId, input, signal) => request(
      `/v1/surveys/${encodeURIComponent(surveyId)}/preview-sessions`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
        signal,
      },
    ),
    get: (sessionId, signal) => request(
      `/v1/preview-sessions/${encodeURIComponent(sessionId)}`,
      { method: 'GET', signal },
    ),
    close: (sessionId, signal) => request(
      `/v1/preview-sessions/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE', signal },
    ),
  };
}
