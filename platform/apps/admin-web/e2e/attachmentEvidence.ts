/** Verify the persisted answer, not the temporary filename returned by the upload modal. */
export function persistedAttachmentName(value: string | null | undefined, temporaryName: string): string {
  if (!/^futmp_[a-z0-9]{15}_txt$/.test(temporaryName) || !value) {
    throw new Error('Synthetic temporary upload evidence is missing');
  }
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error('Attachment answer is not JSON'); }
  if (!Array.isArray(parsed) || parsed.length !== 1 || !parsed[0] || typeof parsed[0] !== 'object') {
    throw new Error('Expected exactly one persisted attachment');
  }
  const file = parsed[0] as Record<string, unknown>;
  // LimeExpressionManager moves futmp_* to a new fu_* name on response submission.
  if (file.name !== 'synthetic-evidence.txt' || file.ext !== 'txt'
      || typeof file.filename !== 'string' || !/^fu_[a-z0-9]{15}$/.test(file.filename)
      || file.filename === temporaryName) {
    throw new Error('Persisted synthetic attachment metadata did not match');
  }
  return file.filename;
}
