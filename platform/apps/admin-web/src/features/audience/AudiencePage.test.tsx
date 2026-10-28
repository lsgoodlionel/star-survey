import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, test } from 'vitest';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { renderWithQuery } from '../../test/render';
import { AudiencePage } from './AudiencePage';

const surveyId = '11111111-1111-4111-8111-111111111111';
const listId = '22222222-2222-4222-8222-222222222222';
const taskId = '33333333-3333-4333-8333-333333333333';
const now = '2026-10-28T08:00:00Z';
const list = { id: listId, name: 'Wave 4 测试名单', kind: 'respondent', dedupeKey: 'email', createdAt: now };
const audience = { audience: { surveyId, listId, orgUnitId: null, includeDescendants: true, tag: null, updatedBy: 'actor-a', updatedAt: now }, recipients: 1 };
const queued = { id: taskId, surveyId, channel: 'email', kind: 'invite', status: 'queued', sourceTaskId: null, recipientCount: 1, sentCount: 0, failedCount: 0, skippedCount: 0, billedUnits: 0, attempts: 0, lastError: null, createdAt: now, finishedAt: null };
const storageKey = `survey-delivery:tenant-a:actor-a:${surveyId}`;

function apiFrom(handler: (request: ApiRequest<unknown>) => unknown): ApiClient {
  return { request: (request) => Promise.resolve(handler(request as ApiRequest<unknown>)) as never };
}

describe('AudiencePage', () => {
  beforeEach(() => sessionStorage.clear());

  test('saves an audience, creates the server-side email task, and only renders masked recipients', async () => {
    const requests: ApiRequest<unknown>[] = [];
    const api = apiFrom((request) => {
      requests.push(request);
      if (request.path === '/v1/contact-lists') return request.method === 'POST' ? list : [list];
      if (request.path === `/v1/surveys/${surveyId}/audience`) return request.method === 'PUT' ? audience : audience;
      if (request.path === `/v1/surveys/${surveyId}/delivery-tasks`) return queued;
      if (request.path === `/v1/delivery/tasks/${taskId}`) return queued;
      if (request.path === `/v1/delivery/tasks/${taskId}/recipients`) return [{ id: '44444444-4444-4444-8444-444444444444', address: 'a***@example.test', displayName: '王同学', state: 'queued', attempts: 0, remindersSent: 0, lastError: null, sentAt: null, lastReminderAt: null }];
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(<AudiencePage api={api} surveyId={surveyId} tenantId="tenant-a" actorId="actor-a" />);
    expect(await screen.findByText(/服务端当前可邀请/)).toHaveTextContent('1 人');
    fireEvent.click(screen.getByRole('button', { name: '保存当前受众' }));
    expect(await screen.findByText('受众已保存，当前可邀请 1 人。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '从受众创建 Email 任务' }));
    expect(await screen.findByText(/状态：等待发送/)).toBeInTheDocument();
    expect(await screen.findByLabelText('脱敏收件人')).toHaveTextContent('a***@example.test');
    const create = requests.find((item) => item.path.endsWith('/delivery-tasks'))!;
    expect(create.body).not.toHaveProperty('recipients');
    expect(create.body).toMatchObject({ channel: 'email' });
    expect(sessionStorage.getItem(storageKey)).toBe(taskId);
  });

  test('shows the channel-unavailable message without a created task', async () => {
    const unavailable = new ApiError('unavailable', '服务暂时不可用，请稍后重试', 503);
    const api = apiFrom((request) => {
      if (request.path === '/v1/contact-lists') return [list];
      if (request.path === `/v1/surveys/${surveyId}/audience`) return audience;
      if (request.path.endsWith('/delivery-tasks')) return Promise.reject(unavailable);
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(<AudiencePage api={api} surveyId={surveyId} tenantId="tenant-a" actorId="actor-a" />);
    await screen.findByText(/服务端当前可邀请/);
    fireEvent.click(screen.getByRole('button', { name: '从受众创建 Email 任务' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('渠道未配置，未创建任务');
    expect(screen.queryByLabelText('投放任务状态')).not.toBeInTheDocument();
  });

  test('cancels queued tasks', async () => {
    let cancelled = false;
    const api = apiFrom((request) => {
      if (request.path === '/v1/contact-lists') return [list];
      if (request.path === `/v1/surveys/${surveyId}/audience`) return audience;
      if (request.path === `/v1/surveys/${surveyId}/delivery-tasks`) return queued;
      if (request.path === `/v1/delivery/tasks/${taskId}`) return cancelled ? { ...queued, status: 'cancelled' } : queued;
      if (request.path === `/v1/delivery/tasks/${taskId}/recipients`) return [];
      if (request.path === `/v1/delivery/tasks/${taskId}/cancel`) { cancelled = true; return { ...queued, status: 'cancelled' }; }
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(<AudiencePage api={api} surveyId={surveyId} tenantId="tenant-a" actorId="actor-a" />);
    await screen.findByText(/服务端当前可邀请/);
    fireEvent.click(screen.getByRole('button', { name: '从受众创建 Email 任务' }));
    expect(await screen.findByRole('button', { name: '取消任务' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消任务' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument());
    expect(screen.getByText(/状态：已取消/)).toBeInTheDocument();
  });

  test('restores the server task and masked recipients after remounting', async () => {
    sessionStorage.setItem(storageKey, taskId);
    const api = apiFrom((request) => {
      if (request.path === '/v1/contact-lists') return [list];
      if (request.path === `/v1/surveys/${surveyId}/audience`) return audience;
      if (request.path === `/v1/delivery/tasks/${taskId}`) return queued;
      if (request.path === `/v1/delivery/tasks/${taskId}/recipients`) return [{ id: '44444444-4444-4444-8444-444444444444', address: 'a***@example.test', displayName: '王同学', state: 'queued', attempts: 0, remindersSent: 0, lastError: null, sentAt: null, lastReminderAt: null }];
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(<AudiencePage api={api} surveyId={surveyId} tenantId="tenant-a" actorId="actor-a" />);
    expect(await screen.findByLabelText('投放任务状态')).toHaveTextContent('等待发送');
    expect(await screen.findByLabelText('脱敏收件人')).toHaveTextContent('a***@example.test');
  });

  test('forgets an inaccessible restored task instead of displaying stale task data', async () => {
    sessionStorage.setItem(storageKey, taskId);
    const api = apiFrom((request) => {
      if (request.path === '/v1/contact-lists') return [list];
      if (request.path === `/v1/surveys/${surveyId}/audience`) return audience;
      if (request.path === `/v1/delivery/tasks/${taskId}`) return Promise.reject(new ApiError('forbidden', '无权执行当前操作', 403));
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(<AudiencePage api={api} surveyId={surveyId} tenantId="tenant-a" actorId="actor-a" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('任务不可访问或已不存在');
    expect(sessionStorage.getItem(storageKey)).toBeNull();
    expect(screen.queryByLabelText('投放任务状态')).not.toBeInTheDocument();
  });

  test('keeps the queued task visible and explains a cancellation failure', async () => {
    const api = apiFrom((request) => {
      if (request.path === '/v1/contact-lists') return [list];
      if (request.path === `/v1/surveys/${surveyId}/audience`) return audience;
      if (request.path.endsWith('/delivery-tasks')) return queued;
      if (request.path === `/v1/delivery/tasks/${taskId}`) return queued;
      if (request.path === `/v1/delivery/tasks/${taskId}/recipients`) return [];
      if (request.path === `/v1/delivery/tasks/${taskId}/cancel`) return Promise.reject(new ApiError('unavailable', '取消失败', 500));
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(<AudiencePage api={api} surveyId={surveyId} tenantId="tenant-a" actorId="actor-a" />);
    await screen.findByText(/服务端当前可邀请/);
    fireEvent.click(screen.getByRole('button', { name: '从受众创建 Email 任务' }));
    expect(await screen.findByRole('button', { name: '取消任务' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消任务' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('取消任务失败，请稍后重试。');
    expect(screen.getByLabelText('投放任务状态')).toHaveTextContent('等待发送');
  });
});
