import type { ZodType } from 'zod';
import {
  ApiError,
  apiErrorForStatus,
  invalidApiUrlError,
  unavailableApiError,
  unexpectedApiError,
} from './errors';

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
  fetchImpl?: typeof fetch;
  getToken?: () => string | null;
  locationOrigin?: string;
  onUnauthorized?: (requestToken: string | null) => void | Promise<void>;
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const fetchImpl = options.fetchImpl ?? fetch;
  const locationOrigin = new URL(options.locationOrigin ?? window.location.origin).origin;

  return {
    request: async <T>(request: ApiRequest<T>) => {
      const requestPath = resolveApiPath(request.path, locationOrigin);
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), request.timeoutMs ?? 15_000);
      const abort = () => controller.abort();
      if (request.signal?.aborted) controller.abort();
      else request.signal?.addEventListener('abort', abort, { once: true });

      try {
        const token = options.getToken?.();
        const headers = new Headers({ Accept: 'application/json' });
        if (request.body !== undefined) headers.set('Content-Type', 'application/json');
        if (token) headers.set('Authorization', `Bearer ${token}`);

        const response = await fetchImpl(requestPath, {
          method: request.method ?? 'GET',
          headers,
          body: request.body === undefined ? undefined : JSON.stringify(request.body),
          credentials: 'same-origin',
          signal: controller.signal,
        });
        if (response.status === 401) await options.onUnauthorized?.(token ?? null);
        const payload = response.ok ? await readPayload(response) : await readErrorPayload(response);
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

function resolveApiPath(path: string, locationOrigin: string): string {
  if (path.startsWith('//')) throw invalidApiUrlError();

  let url: URL;
  try {
    url = new URL(path, locationOrigin);
  } catch {
    throw invalidApiUrlError();
  }

  const isApiPath = url.pathname === '/v1' || url.pathname.startsWith('/v1/');
  if (url.origin !== locationOrigin || !isApiPath || url.hash) throw invalidApiUrlError();
  return `${url.pathname}${url.search}`;
}

async function readPayload(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const contentType = response.headers.get('Content-Type');
  if (!contentType?.includes('application/json')) return undefined;
  return response.json();
}

async function readErrorPayload(response: Response): Promise<unknown> {
  try {
    return await readPayload(response);
  } catch {
    return undefined;
  }
}
