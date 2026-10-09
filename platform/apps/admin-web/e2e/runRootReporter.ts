import type { FullResult, Reporter } from '@playwright/test/reporter';
import { execFile } from 'node:child_process';
import { access } from 'node:fs/promises';
import { resolve } from 'node:path';
import { promisify } from 'node:util';

const run = promisify(execFile);

export default class RunRootReporter implements Reporter {
  async onEnd(result: FullResult) {
    if (result.status !== 'passed') return;

    const baseURL = requiredEnv('ADMIN_WEB_BASE_URL');
    const jwtFile = requiredEnv('ADMIN_WEB_JWT_FILE');
    const resultsDir = requiredEnv('ADMIN_WEB_TEST_RESULTS_DIR');
    const rootIdFile = resolve(resultsDir, 'run-root-resource-id.txt');
    const gate = resolve(import.meta.dirname, '../../../tests/e2e/admin_web_gate.py');

    try {
      await access(rootIdFile);
      await run('python3', [
        gate,
        '--base-url', baseURL,
        'archive-run-root',
        '--jwt-file', jwtFile,
        '--root-id-file', rootIdFile,
      ]);
    } catch {
      process.stderr.write('  [FAIL] successful browser suite could not archive its run root\n');
      return { status: 'failed' as const };
    }
  }
}

function requiredEnv(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}
