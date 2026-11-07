import { describe, expect, it } from 'vitest';
import { persistedAttachmentName } from './attachmentEvidence';

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
