import { fireEvent, screen, waitFor } from '@testing-library/react';
import { vi, expect, test } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { renderWithQuery } from '../../test/render';
import { DeliveryWorkspacePage } from './DeliveryWorkspacePage';

const now = '2026-10-30T08:00:00Z';
const list = { id: '10000000-0000-4000-8000-000000000001', name: '客户名单', kind: 'respondent', dedupeKey: 'email', createdAt: now };
const unit = { id: '20000000-0000-4000-8000-000000000001', parentId: null, name: '华东区', externalRef: null };
const tag = { id: '30000000-0000-4000-8000-000000000001', name: '重点客户' };
const contact = { id: '40000000-0000-4000-8000-000000000001', kind: 'respondent', listId: list.id, orgUnitId: unit.id, displayName: '王同学', email: 'wang@example.test', phone: null, employeeId: null, identity: null, source: 'manual', status: 'active', tags: [], createdAt: now, updatedAt: now };
const requests: ApiRequest<unknown>[] = [];

const api: ApiClient = { request: (request) => {
  requests.push(request as ApiRequest<unknown>);
  if (request.path === '/v1/contact-lists') return Promise.resolve(request.method === 'POST' ? list : [list]) as never;
  if (request.path === '/v1/org-units') return Promise.resolve(request.method === 'POST' ? unit : [unit]) as never;
  if (request.path === '/v1/contact-tags') return Promise.resolve(request.method === 'POST' ? tag : [tag]) as never;
  if (request.path === `/v1/contacts?listId=${list.id}`) return Promise.resolve({ items: [contact], nextCursor: null }) as never;
  if (request.path === `/v1/contacts/${contact.id}/tags`) return Promise.resolve(undefined) as never;
  throw new Error(`Unhandled ${request.path}`);
} };

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ api, session: { me: { tenantId: 'tenant-a', actorId: 'owner', roles: ['tenant_owner'] } } }) }));

test('manages directory structure and links it to survey delivery', async () => {
  requests.length = 0;
  renderWithQuery(<MemoryRouter><DeliveryWorkspacePage /></MemoryRouter>);
  expect(await screen.findByRole('heading', { name: '通讯录与投放' })).toBeInTheDocument();
  expect(await screen.findByText('wang@example.test')).toBeInTheDocument();
  expect(screen.getAllByText('华东区').length).toBeGreaterThan(0);
  expect(screen.getByRole('link', { name: '选择投放问卷' })).toHaveAttribute('href', '/workspace');

  fireEvent.change(screen.getByLabelText('为 王同学 选择标签'), { target: { value: tag.id } });
  fireEvent.click(screen.getByRole('button', { name: '添加标签' }));
  await waitFor(() => expect(requests).toContainEqual(expect.objectContaining({
    path: `/v1/contacts/${contact.id}/tags`, method: 'POST', body: { tagId: tag.id },
  })));
});

test('refreshes the imported list when its asynchronous import completes', async () => {
  requests.length = 0;
  const originalRequest = api.request;
  const imported = { ...contact, id: '40000000-0000-4000-8000-000000000002', displayName: '导入联系人', email: 'imported@example.invalid' };
  const job = { jobId: '50000000-0000-4000-8000-000000000001', listId: list.id, status: 'queued', totalRows: 1, processedRows: 0, created: 0, updated: 0, duplicate: 0, invalid: 0 };
  let completed = false;
  vi.spyOn(api, 'request').mockImplementation((request) => {
    if (request.path === `/v1/contact-lists/${list.id}/imports`) return Promise.resolve(job) as never;
    if (request.path === `/v1/contact-imports/${job.jobId}`) {
      completed = true;
      return Promise.resolve({ ...job, status: 'completed', processedRows: 1, created: 1 }) as never;
    }
    if (request.path.startsWith(`/v1/contact-imports/${job.jobId}/outcomes`)) return Promise.resolve([{ rowNo: 1, outcome: 'created', reason: null, contactId: imported.id }]) as never;
    if (request.path === `/v1/contacts?listId=${list.id}` && completed) return Promise.resolve({ items: [contact, imported], nextCursor: null }) as never;
    return originalRequest(request);
  });
  renderWithQuery(<MemoryRouter><DeliveryWorkspacePage /></MemoryRouter>);
  expect(await screen.findByText(contact.email)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('投放工作区导入内容'), { target: { value: 'name,email\n导入联系人,imported@example.invalid\n' } });
  fireEvent.click(screen.getByRole('button', { name: '提交导入任务' }));
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已完成'));
  expect(await screen.findByText(imported.email)).toBeInTheDocument();
});

test('completion invalidates the job list instead of the currently selected list', async () => {
  requests.length = 0;
  const originalRequest = api.request;
  const otherList = { ...list, id: '10000000-0000-4000-8000-000000000002', name: '其他名单' };
  const otherContact = { ...contact, id: '40000000-0000-4000-8000-000000000003', listId: otherList.id, email: 'other@example.invalid' };
  const job = { jobId: '50000000-0000-4000-8000-000000000002', listId: list.id, status: 'queued', totalRows: 1, processedRows: 0, created: 0, updated: 0, duplicate: 0, invalid: 0 };
  let finish: ((value: typeof job) => void) | undefined;
  const pending = new Promise<typeof job>((resolve) => { finish = resolve; });
  vi.spyOn(api, 'request').mockImplementation((request) => {
    if (request.path === '/v1/contact-lists') return Promise.resolve([list, otherList]) as never;
    if (request.path === `/v1/contacts?listId=${otherList.id}`) return Promise.resolve({ items: [otherContact], nextCursor: null }) as never;
    if (request.path === `/v1/contact-lists/${list.id}/imports`) return Promise.resolve(job) as never;
    if (request.path === `/v1/contact-imports/${job.jobId}`) return pending as never;
    if (request.path.startsWith(`/v1/contact-imports/${job.jobId}/outcomes`)) return Promise.resolve([]) as never;
    return originalRequest(request);
  });
  const { queryClient } = renderWithQuery(<MemoryRouter><DeliveryWorkspacePage /></MemoryRouter>);
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  expect(await screen.findByText(contact.email)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('投放工作区导入内容'), { target: { value: 'name,email\n导入联系人,imported@example.invalid\n' } });
  fireEvent.click(screen.getByRole('button', { name: '提交导入任务' }));
  await screen.findByRole('status');
  fireEvent.change(screen.getByLabelText('当前联系人名单'), { target: { value: otherList.id } });
  expect(await screen.findByText(otherContact.email)).toBeInTheDocument();
  finish!({ ...job, status: 'completed', processedRows: 1, created: 1 });
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['contacts', 'tenant-a', list.id] }));
  expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ['contacts', 'tenant-a', otherList.id] });
  expect(screen.getByText(otherContact.email)).toBeInTheDocument();
});
