import { describe, expect, test } from 'vitest';
import { assertNoTimestampResources, stableDemoHierarchy } from '../../e2e/demoDataEvidence';

describe('demo data browser evidence', () => {
  test('defines the fixed project, folder and survey traversal', () => {
    expect(stableDemoHierarchy).toEqual(['客户体验研究', '2026 Q4', '品牌跟踪调查']);
  });

  test('rejects timestamp-style E2E resources anywhere in a loaded page', () => {
    expect(() => assertNoTimestampResources([
      '客户体验研究',
      'E2E问卷 20261009T120102',
    ])).toThrow('timestamp-style E2E resource');
    expect(() => assertNoTimestampResources(stableDemoHierarchy)).not.toThrow();
  });
});
