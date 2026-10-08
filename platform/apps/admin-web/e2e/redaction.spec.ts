import { expect, test } from '@playwright/test';
import { recentSanitizedNetworkEvents, trackSanitizedNetworkEvents } from './networkEvidence';
import { redactSensitiveInputs } from './redaction';

test('collects sanitized network events and hides controls before an in-memory screenshot', async ({ page }) => {
  const marker = 'PLAYWRIGHT-SENSITIVE-MARKER';
  trackSanitizedNetworkEvents(page);
  await page.route('https://admin.example/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    await route.fulfill({
      body: path === '/workspace' ? '<main>workspace</main>' : '{}',
      contentType: path === '/workspace' ? 'text/html' : 'application/json',
      status: path === '/v1/failure' ? 503 : 200,
    });
  });
  await page.goto(`https://admin.example/workspace?token=${marker}`);
  await page.setContent(`
    <main><p>safe content</p></main>
    <input id="password" type="password" value="${marker}">
    <textarea id="marked" data-sensitive="token">${marker}</textarea>
  `);
  await page.evaluate(async () => {
    await fetch('/v1/success?token=must-not-persist');
    await fetch('/v1/failure?token=must-not-persist');
  });

  await redactSensitiveInputs(page);

  for (const selector of ['#password', '#marked']) {
    const control = page.locator(selector);
    await expect(control).toHaveCSS('visibility', 'hidden');
    await expect(control).toHaveValue('');
  }
  expect(recentSanitizedNetworkEvents(page).slice(-2)).toEqual([
    { method: 'GET', status: 200, url: 'https://admin.example/v1/success' },
    { method: 'GET', status: 503, url: 'https://admin.example/v1/failure' },
  ]);
  const screenshot = await page.screenshot();
  expect(screenshot.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
});
