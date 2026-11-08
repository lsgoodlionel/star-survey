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

/** The returned object contains only fixed reason codes and booleans, never CSV values. */
export function attachmentPackageFailure(rows: string[][], manifest: string[][], fieldName: string, storedName: string) {
  const cell = (table: string[][], row: string[], name: string) => {
    const column = table[0]?.indexOf(name) ?? -1;
    return column >= 0 ? row[column] : undefined;
  };
  const absent = rows.slice(1).filter((row) => cell(rows, row, 'status') === 'absent');
  const rawReason = absent.length === 1 ? cell(rows, absent[0], 'reason') : undefined;
  const reason = absent.length === 0 ? 'none' : rawReason === 'not_found' ? 'not_found'
    : rawReason === 'too_large' ? 'too_large' : rawReason === 'unknown' ? 'unknown' : 'other';
  const singleManifest = manifest.length === 2;
  const singleIndex = rows.length === 2;
  const item = manifest[1] ?? [];
  const index = rows[1] ?? [];
  return {
    reason, singleManifest, singleIndex,
    manifestMatches: singleManifest && cell(manifest, item, 'storedname') === storedName
      && cell(manifest, item, 'fieldname') === fieldName,
    indexMatches: singleIndex && cell(rows, index, 'fieldname') === fieldName
      && cell(rows, index, 'name') === 'synthetic-evidence.txt',
    identityMatches: singleManifest && singleIndex && ['version', 'sid', 'responseid', 'fieldname', 'index'].every((key) => {
      const value = cell(rows, index, key);
      return typeof value === 'string' && value.length > 0 && value === cell(manifest, item, key);
    }),
  };
}
