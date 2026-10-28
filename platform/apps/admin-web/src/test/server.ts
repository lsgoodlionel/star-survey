import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll } from 'vitest';

export const server = setupServer();

export function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function binaryResponse(
  body: BodyInit,
  options: { status?: number; contentType?: string; headers?: HeadersInit } = {},
) {
  const headers = new Headers(options.headers);
  if (options.contentType) headers.set('Content-Type', options.contentType);
  return new Response(body, { status: options.status ?? 200, headers });
}

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());
