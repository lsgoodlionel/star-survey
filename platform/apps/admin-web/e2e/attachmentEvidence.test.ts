import { describe, expect, it } from 'vitest';
import { attachmentPackageFailure, persistedAttachmentName } from './attachmentEvidence';

const temporary = 'futmp_23456789abcdefg_txt';
const persisted = 'fu_abcdefghijkmnpq';
const file = { name: 'synthetic-evidence.txt', ext: 'txt', filename: persisted };

describe('attachment persistence evidence', () => {
  it('uses the persisted filename after the engine renames a temporary upload', () => {
    expect(persistedAttachmentName(JSON.stringify([file]), temporary)).toBe(persisted);
  });

  it.each([
    undefined, null, '', 'invalid-json', '{}', '[]',
    JSON.stringify([file, file]),
    JSON.stringify([{ ...file, filename: temporary }]),
    JSON.stringify([{ ...file, filename: '' }]),
    JSON.stringify([{ ...file, name: 'other.txt' }]),
    JSON.stringify([{ ...file, ext: 'exe' }]),
  ])('rejects missing, temporary, ambiguous, or mismatched evidence %#', (value) => {
    expect(() => persistedAttachmentName(value, temporary)).toThrow();
  });
});


describe('redacted attachment package failure', () => {
  const identity = ['1', '123', '7', '123X1X1', '1'];
  const indexHeader = ['version', 'sid', 'responseid', 'fieldname', 'index', 'name', 'status', 'reason'];
  const manifestHeader = ['version', 'sid', 'responseid', 'fieldname', 'index', 'storedname'];
  const manifest = [manifestHeader, [...identity, persisted]];
  const index = (reason: string) => [indexHeader, [...identity, 'synthetic-evidence.txt', 'absent', reason]];

  it.each(['not_found', 'too_large', 'unknown'])('reports only the allowed reason %s', (reason) => {
    expect(attachmentPackageFailure(index(reason), manifest, '123X1X1', persisted)).toEqual({
      reason, singleManifest: true, singleIndex: true,
      manifestMatches: true, indexMatches: true, identityMatches: true,
    });
  });

  it('redacts arbitrary reasons and mismatched identifiers instead of logging their values', () => {
    const result = attachmentPackageFailure(index('private/path?sig=secret'), manifest, 'other-field', 'other-file');
    expect(result.reason).toBe('other');
    expect(result.manifestMatches).toBe(false);
    expect(result.indexMatches).toBe(false);
    expect(JSON.stringify(result)).not.toMatch(/private|secret|other-field|other-file|123X1X1/);
  });

  it('reports missing evidence without inventing an absence reason', () => {
    expect(attachmentPackageFailure([], [], 'field', 'file')).toEqual({
      reason: 'none', singleManifest: false, singleIndex: false,
      manifestMatches: false, indexMatches: false, identityMatches: false,
    });
  });
});
