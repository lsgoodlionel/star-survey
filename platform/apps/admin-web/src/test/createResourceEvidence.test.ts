import { describe, expect, test } from 'vitest';
import { createResourceEndpoint } from '../../e2e/createResourceEvidence';

describe('create resource response evidence', () => {
  test.each([
    ['新建项目', '/v1/projects'],
    ['新建文件夹', '/v1/folders'],
    ['新建问卷', '/v1/surveys'],
  ])('waits for the real %s create endpoint', (trigger, endpoint) => {
    expect(createResourceEndpoint(trigger)).toBe(endpoint);
  });
});
