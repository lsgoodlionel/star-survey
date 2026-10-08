import { expect, test } from '@playwright/test';
import { redactSensitiveInputs } from './redaction';

test('hides sensitive DOM controls before taking an in-memory failure screenshot', async ({ page }) => {
  const marker = 'PLAYWRIGHT-SENSITIVE-MARKER';
  await page.setContent(`
    <main><p>safe content</p></main>
    <input id="password" type="password" value="${marker}">
    <textarea id="marked" data-sensitive="token">${marker}</textarea>
  `);

  await redactSensitiveInputs(page);

  for (const selector of ['#password', '#marked']) {
    const control = page.locator(selector);
    await expect(control).toHaveCSS('visibility', 'hidden');
    await expect(control).toHaveValue('');
  }
  const screenshot = await page.screenshot();
  expect(screenshot.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
});
