import type { FullResult, Reporter } from '@playwright/test/reporter';
import { recordPlaywrightSuiteSuccess } from './runRootEvidence';

export default class RunRootReporter implements Reporter {
  async onEnd(result: FullResult) {
    if (result.status !== 'passed') return;

    const resultsDir = requiredEnv('ADMIN_WEB_TEST_RESULTS_DIR');

    try {
      await recordPlaywrightSuiteSuccess(resultsDir);
    } catch {
      process.stderr.write('  [FAIL] successful browser suite could not validate its run root evidence\n');
      return { status: 'failed' as const };
    }
  }
}

function requiredEnv(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}
