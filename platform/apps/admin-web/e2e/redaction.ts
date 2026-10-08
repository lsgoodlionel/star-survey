import type { Page } from '@playwright/test';

export async function redactSensitiveInputs(page: Page) {
  await page.evaluate(() => {
    const elements = document.querySelectorAll('[data-sensitive="token"], input[type="password"]');
    for (const element of elements) {
      if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
        element.value = '';
      }
      (element as HTMLElement).style.visibility = 'hidden';
    }
  });
}
