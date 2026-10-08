import type { Page, Response } from '@playwright/test';

export interface SanitizedNetworkEvent {
  method: string;
  status: number;
  url: string;
}

const MAX_NETWORK_EVENTS = 25;
const eventsByPage = new WeakMap<Page, SanitizedNetworkEvent[]>();

export function trackSanitizedNetworkEvents(page: Page) {
  if (eventsByPage.has(page)) return;
  const events: SanitizedNetworkEvent[] = [];
  eventsByPage.set(page, events);
  page.on('response', (response) => {
    const event = sanitizedNetworkEvent(response);
    if (!event) return;
    events.push(event);
    if (events.length > MAX_NETWORK_EVENTS) events.splice(0, events.length - MAX_NETWORK_EVENTS);
  });
}

export function recentSanitizedNetworkEvents(page: Page) {
  return [...(eventsByPage.get(page) ?? [])];
}

function sanitizedNetworkEvent(response: Response): SanitizedNetworkEvent | undefined {
  try {
    const url = new URL(response.url());
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return undefined;
    return {
      method: response.request().method(),
      status: response.status(),
      url: `${url.origin}${url.pathname}`,
    };
  } catch {
    return undefined;
  }
}
