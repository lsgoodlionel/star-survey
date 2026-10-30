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
