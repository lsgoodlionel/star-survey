import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, test } from 'vitest';
import { recordRunRootId } from '../../e2e/runRootEvidence';

describe('run root evidence', () => {
  test('atomically records the canonical create response id before later assertions', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'admin-web-root-'));
    const rootId = '22222222-2222-4222-8222-222222222222';

    await recordRunRootId(directory, { id: rootId });
    const laterAssertion = () => expect(false).toBe(true);

    expect(laterAssertion).toThrow();
    expect(await readFile(join(directory, 'run-root-resource-id.txt'), 'utf8')).toBe(`${rootId}\n`);
  });

  test('rejects a non-canonical create response without writing evidence', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'admin-web-root-'));
    await expect(recordRunRootId(directory, { id: 'not-a-uuid' })).rejects.toThrow(
      'create response did not contain a canonical resource id',
    );
  });
});
