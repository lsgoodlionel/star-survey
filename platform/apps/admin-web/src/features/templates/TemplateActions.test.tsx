import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import type { ApiClient } from '../../shared/api/http';
import type { TemplateCapabilities, TemplateView } from '../../shared/api/templates';
import { TemplateActions } from './TemplateActions';
import { PlatformTemplateImport } from './PlatformTemplateImport';

const id = '10000000-0000-4000-8000-000000000001';
const sourceSurveyId = '20000000-0000-4000-8000-000000000001';
const base: TemplateView = { id, scope: 'tenant', name: '模板', description: null,
  status: 'draft', currentVersion: 1, publishedVersion: null, createdBy: 'author',
  reviewedBy: null, reviewedAt: null, reviewReason: null, updatedAt: '2026-10-27T08:00:00Z' };
const none: TemplateCapabilities = { canAddVersion: false, canSubmit: false,
  canReview: false, canUnpublish: false };

function mount(status: TemplateView['status'], capabilities: TemplateCapabilities, request = vi.fn(),
  onRefresh = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const api = { request } as unknown as ApiClient;
  const template = { ...base, status };
  render(<QueryClientProvider client={client}>
    <TemplateActions api={api} tenantId="tenant-a" template={template}
      sourceSurveyId={sourceSurveyId} capabilities={capabilities} onRefresh={onRefresh} />
  </QueryClientProvider>);
  return { request, client, onRefresh };
}

test.each([
  ['draft', { ...none, canAddVersion: true, canSubmit: true }, ['添加版本', '提交审核']],
  ['pending', { ...none, canReview: true }, ['批准', '驳回']],
  ['rejected', { ...none, canSubmit: true }, ['提交审核']],
  ['published', { ...none, canUnpublish: true }, ['下架']],
  ['unpublished', { ...none, canSubmit: true }, ['提交审核']],
] as const)('shows authorized actions for %s', (status, capabilities, labels) => {
  mount(status, capabilities);
  for (const label of labels) expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
  for (const label of ['添加版本', '提交审核', '批准', '驳回', '下架']) {
    if (!labels.includes(label as never)) expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument();
  }
});

test('never shows mutation controls when server capabilities deny them', () => {
  mount('pending', none);
  expect(screen.queryByRole('button', { name: '批准' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '驳回' })).not.toBeInTheDocument();
});

test('requires a rejection reason and respects its 1000-character bound', async () => {
  const user = userEvent.setup();
  const { request } = mount('pending', { ...none, canReview: true });
  await user.click(screen.getByRole('button', { name: '驳回' }));
  await user.click(screen.getByRole('button', { name: '确认驳回' }));
  expect(screen.getByRole('alert')).toHaveTextContent('请填写驳回原因');
  fireEvent.change(screen.getByRole('textbox', { name: '驳回原因' }), { target: { value: 'x'.repeat(1001) } });
  await user.click(screen.getByRole('button', { name: '确认驳回' }));
  expect(screen.getByRole('alert')).toHaveTextContent('不能超过 1000 字');
  expect(request).not.toHaveBeenCalled();
});

test('blocks duplicate submit and invalidates template caches on success', async () => {
  let finish!: (value: unknown) => void;
  const request = vi.fn().mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const { client } = mount('draft', { ...none, canSubmit: true }, request);
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const button = screen.getByRole('button', { name: '提交审核' });
  fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
  expect(button).toBeDisabled();
  finish({ ...base, status: 'pending' });
  await screen.findByText('操作已完成');
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['templates', 'tenant-a'] });
});

test('reports a 409 conflict and asks the caller to refresh server state', async () => {
  const request = vi.fn().mockRejectedValue({ status: 409 });
  const onRefresh = vi.fn();
  mount('draft', { ...none, canSubmit: true }, request, onRefresh);
  await userEvent.setup().click(screen.getByRole('button', { name: '提交审核' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('状态已变化');
  expect(onRefresh).toHaveBeenCalled();
});

test('validates a local platform JSON file before importing a template', async () => {
  const request = vi.fn().mockResolvedValue({ ...base, scope: 'platform' });
  const api = { request } as unknown as ApiClient;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter>
    <PlatformTemplateImport api={api} tenantId="tenant-a" />
  </MemoryRouter></QueryClientProvider>);
  const user = userEvent.setup();
  fireEvent.change(screen.getByRole('textbox', { name: '平台模板名称' }), { target: { value: '行业模板' } });
  const fileInput = screen.getByLabelText('定义 JSON 文件');
  await user.upload(fileInput, new File(['{"title":"问卷"}'], 'bad.json', { type: 'application/json' }));
  await user.click(screen.getByRole('button', { name: '导入平台模板' }));
  expect(screen.getByRole('alert')).toHaveTextContent('定义文件格式无效');
  expect(request).not.toHaveBeenCalled();
  await user.upload(fileInput, new File([
    '{"definitionVersion":2,"title":"问卷","language":"zh-Hans","groups":[]}',
  ], 'valid.json', { type: 'application/json' }));
  await user.click(screen.getByRole('button', { name: '导入平台模板' }));
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({
    path: '/v1/platform/survey-templates', method: 'POST',
    body: { name: '行业模板', description: null,
      definition: { definitionVersion: 2, title: '问卷', language: 'zh-Hans', groups: [] } },
  })));
});
