const createEndpoints: Record<string, string> = {
  '新建项目': '/v1/projects',
  '新建文件夹': '/v1/folders',
  '新建问卷': '/v1/surveys',
};

export function createResourceEndpoint(trigger: string) {
  const endpoint = createEndpoints[trigger];
  if (!endpoint) throw new Error(`unsupported create resource trigger: ${trigger}`);
  return endpoint;
}
