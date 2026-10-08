import { describe, expect, test } from 'vitest';
import { assertArtifactContainsNoSecret, redactSecret } from './artifacts';

describe('browser artifact redaction', () => {
  test('removes raw and URL-encoded token values from recorded URLs', () => {
    const token = 'header.payload+/signature=';
    const url = `https://example.test/v1/check?raw=${token}&encoded=${encodeURIComponent(token)}`;

    const redacted = redactSecret(url, token);

    expect(redacted).not.toContain(token);
    expect(redacted).not.toContain(encodeURIComponent(token));
    expect(redacted).toContain('[REDACTED]');
  });

  test('rejects nested result artifacts containing a raw token', () => {
    expect(() => assertArtifactContainsNoSecret({ network: [{ url: 'secret-token' }] }, 'secret-token'))
      .toThrow('Refusing to write an artifact containing the test token');
  });

  test('accepts sanitized result artifacts', () => {
    expect(() => assertArtifactContainsNoSecret({ network: [{ url: '[REDACTED]' }] }, 'secret-token'))
      .not.toThrow();
  });
});
