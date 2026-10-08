import type { ZodType } from 'zod';
import { ApiError, apiErrorForStatus, unavailableApiError, unexpectedApiError } from './errors';

export interface ApiRequest<T> {
  path: string;
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  schema: ZodType<T>;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface ApiClient {
  request<T>(request: ApiRequest<T>): Promise<T>;
}

interface ApiClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  getToken?: () => string | null;
  onUnauthorized?: () => void | Promise<void>;
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const fetchImpl = options.fetchImpl ?? fetch;

  return {
    request: async <T>(request: ApiRequest<T>) => {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), request.timeoutMs ?? 15_000);
      const abort = () => controller.abort();
      request.signal?.addEventListener('abort', abort, { once: true });

      try {
        const token = options.getToken?.();
        const headers = new Headers({ Accept: 'application/json' });
        if (request.body !== undefined) headers.set('Content-Type', 'application/json');
        if (token) headers.set('Authorization', `Bearer ${token}`);

        const response = await fetchImpl(`${options.baseUrl ?? ''}${request.path}`, {
          method: request.method ?? 'GET',
          headers,
          body: request.body === undefined ? undefined : JSON.stringify(request.body),
          credentials: 'same-origin',
          signal: controller.signal,
        });
        const payload = await readPayload(response);

        if (response.status === 401) await options.onUnauthorized?.();
        if (response.status === 202 || !response.ok) throw apiErrorForStatus(response.status, payload);

        return request.schema.parse(payload);
      } catch (error) {
        if (error instanceof ApiError) throw error;
        if (error instanceof DOMException && error.name === 'AbortError') throw unavailableApiError();
        if (error instanceof TypeError) throw unavailableApiError();
        throw unexpectedApiError();
      } finally {
        window.clearTimeout(timeout);
        request.signal?.removeEventListener('abort', abort);
      }
    },
  };
}

async function readPayload(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const contentType = response.headers.get('Content-Type');
  if (!contentType?.includes('application/json')) return undefined;
  return response.json();
}
