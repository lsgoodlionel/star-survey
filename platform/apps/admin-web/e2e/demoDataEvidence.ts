export const stableDemoHierarchy = ['客户体验研究', '2026 Q4', '品牌跟踪调查'] as const;

const timestampResource = /^E2E(?:项目|文件夹|问卷).*\d{8}T?\d{6}/i;

export function assertNoTimestampResources(names: readonly string[]) {
  if (names.some((name) => timestampResource.test(name))) {
    throw new Error('demo contains a timestamp-style E2E resource');
  }
}
