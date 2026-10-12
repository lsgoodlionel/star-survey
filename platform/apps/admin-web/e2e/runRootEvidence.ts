import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const canonicalUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function recordRunRootId(resultsDir: string, payload: unknown) {
  if (!isRecord(payload) || typeof payload.id !== 'string' || !canonicalUuid.test(payload.id)) {
    throw new Error('create response did not contain a canonical resource id');
  }

  const rootId = payload.id.toLowerCase();
  await atomicWrite(resultsDir, 'run-root-resource-id.txt', `${rootId}\n`);
  return rootId;
}

export async function recordPlaywrightSuiteSuccess(resultsDir: string) {
  const rootId = (await readFile(join(resultsDir, 'run-root-resource-id.txt'), 'utf8')).trim();
  if (!canonicalUuid.test(rootId)) {
    throw new Error('run root evidence does not contain a canonical resource id');
  }
  await atomicWrite(resultsDir, 'playwright-suite-success.txt', 'passed\n');
}

async function atomicWrite(directory: string, name: string, content: string) {
  await mkdir(directory, { recursive: true });
  const destination = join(directory, name);
  const temporary = join(directory, `.${name}.${process.pid}.${Date.now()}.tmp`);
  try {
    await writeFile(temporary, content, { mode: 0o600, flag: 'wx' });
    await rename(temporary, destination);
  } catch (error) {
    await unlink(temporary).catch(() => undefined);
    throw error;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
