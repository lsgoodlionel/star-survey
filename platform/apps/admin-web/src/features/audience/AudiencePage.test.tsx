import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, test } from 'vitest';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { renderWithQuery } from '../../test/render';
import { AudiencePage } from './AudiencePage';

const surveyId = '11111111-1111-4111-8111-111111111111';
const listId = '22222222-2222-4222-8222-222222222222';
const taskId = '33333333-3333-4333-8333-333333333333';
const importJobId = '55555555-5555-4555-8555-555555555555';
const now = '2026-10-28T08:00:00Z';
const list = { id: listId, name: 'Wave 4 测试名单', kind: 'respondent', dedupeKey: 'email', createdAt: now };
const audience = { audience: { surveyId, listId, orgUnitId: null, includeDescendants: true, tag: null, updatedBy: 'actor-a', updatedAt: now }, recipients: 1 };
const queued = { id: taskId, surveyId, channel: 'email', kind: 'invite', status: 'queued', sourceTaskId: null, recipientCount: 1, sentCount: 0, failedCount: 0, skippedCount: 0, billedUnits: 0, attempts: 0, lastError: null, createdAt: now, finishedAt: null };
const storageKey = `survey-delivery:tenant-a:actor-a:${surveyId}`;
const importStorageKey = `contact-import:tenant-a:actor-a:${surveyId}:${listId}`;
const importJob = { jobId: importJobId, listId, status: 'completed', totalRows: 2, processedRows: 2, created: 1, updated: 0, duplicate: 0, invalid: 1 };
const queuedImportJob = { ...importJob, status: 'queued', processedRows: 0, created: 0, invalid: 0 };

function apiFrom(handler: (request: ApiRequest<unknown>) => unknown): ApiClient {
  return {
    request: (request) => {
      try {
        return Promise.resolve(handler(request as ApiRequest<unknown>)) as never;
      } catch (error) {
        if (request.path === `/v1/surveys/${surveyId}/delivery-tasks?limit=50`
          || request.path === `/v1/delivery/tasks/${taskId}/receipts?limit=50`
          || request.path === `/v1/delivery/surveys/${surveyId}/reminder-rules`) {
          return Promise.resolve([]) as never;
        }
        return Promise.reject(error) as never;
      }
    },
  };
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
    expect(screen.getByText('暂无已认证回执。')).toBeInTheDocument();
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

  test('selects a redacted invite task and creates then pauses its reminder rule', async () => {
    const ruleId = '77777777-7777-4777-8777-777777777777';
    const reminder = {
      id: ruleId, surveyId, sourceTaskId: taskId, firstDelaySeconds: 0, intervalSeconds: 3600,
      maxReminders: 2, subject: '请继续填写问卷', enabled: true, createdAt: now,
    };
    const requests: ApiRequest<unknown>[] = [];
    const api = apiFrom((request) => {
      requests.push(request);
      if (request.path === '/v1/contact-lists') return [list];
      if (request.path === `/v1/surveys/${surveyId}/audience`) return audience;
      if (request.path === `/v1/surveys/${surveyId}/delivery-tasks?limit=50`) return [queued];
      if (request.path === `/v1/delivery/surveys/${surveyId}/reminder-rules`) return request.method === 'POST' ? reminder : [];
      if (request.path === `/v1/delivery/surveys/${surveyId}/reminder-rules/${ruleId}`) return { ...reminder, enabled: false };
      if (request.path === `/v1/delivery/tasks/${taskId}`) return queued;
      if (request.path === `/v1/delivery/tasks/${taskId}/recipients`) return [];
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(<AudiencePage api={api} surveyId={surveyId} tenantId="tenant-a" actorId="actor-a" />);
    expect(await screen.findByLabelText('最近投放任务列表')).toHaveTextContent('邀请任务：等待发送，收件人 1 人。');
    fireEvent.click(screen.getByRole('button', { name: '查看任务' }));
    expect(await screen.findByRole('heading', { name: '催答规则' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('首次延迟（分钟）'), { target: { value: '0' } });
    fireEvent.change(screen.getByLabelText('催答间隔（分钟）'), { target: { value: '60' } });
    fireEvent.click(screen.getByRole('button', { name: '创建催答规则' }));
    expect(await screen.findByLabelText('催答规则状态')).toHaveTextContent('已启用；首次延迟 0 分钟，间隔 60 分钟，最多 2 次。');
    const create = requests.find((request) => request.path.endsWith('/reminder-rules') && request.method === 'POST')!;
    expect(create.body).toMatchObject({ sourceTaskId: taskId, firstDelaySeconds: 0, intervalSeconds: 3600, maxReminders: 2 });
    fireEvent.click(screen.getByRole('button', { name: '暂停催答规则' }));
    await waitFor(() => expect(screen.getByLabelText('催答规则状态')).toHaveTextContent('已暂停'));
    expect(requests.find((request) => request.method === 'PATCH')?.body).toEqual({ enabled: false });
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

  test('submits a scoped CSV import and renders only non-PII row outcomes', async () => {
    const requests: ApiRequest<unknown>[] = [];
    const api = apiFrom((request) => {
      requests.push(request);
      if (request.path === '/v1/contact-lists') return [list];
      if (request.path === `/v1/surveys/${surveyId}/audience`) return audience;
      if (request.path === `/v1/contact-lists/${listId}/imports`) return queuedImportJob;
      if (request.path === `/v1/contact-imports/${importJobId}`) return importJob;
      if (request.path === `/v1/contact-imports/${importJobId}/outcomes?fromRow=1&limit=100`) return [
        { rowNo: 2, outcome: 'created', reason: null, contactId: '66666666-6666-4666-8666-666666666666' },
        { rowNo: 3, outcome: 'invalid', reason: 'email_invalid', contactId: null },
      ];
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(<AudiencePage api={api} surveyId={surveyId} tenantId="tenant-a" actorId="actor-a" />);
    await screen.findByText(/服务端当前可邀请/);
    fireEvent.change(screen.getByLabelText('导入内容'), { target: { value: 'name,email\n王同学,student@example.test\n坏数据,not-an-email' } });
    fireEvent.click(screen.getByRole('button', { name: '提交批量导入' }));
    await waitFor(() => expect(screen.getByLabelText('联系人导入状态')).toHaveTextContent('已处理 2/2 行；新增 1、更新 0、重复 0、无效 1'));
    expect(await screen.findByLabelText('导入行结果')).toHaveTextContent('第 2 行：已新增');
    expect(screen.getByLabelText('导入行结果')).toHaveTextContent('第 3 行：无效（email_invalid）');
    expect(screen.queryByText('student@example.test')).not.toBeInTheDocument();
    expect(sessionStorage.getItem(importStorageKey)).toBe(importJobId);
    expect(requests.find((request) => request.path.endsWith('/imports'))?.body).toEqual({
      format: 'csv', content: 'name,email\n王同学,student@example.test\n坏数据,not-an-email',
    });
  });

  test.each([403, 404])('forgets an inaccessible restored import after %i instead of displaying its old summary', async (status) => {
    sessionStorage.setItem(importStorageKey, importJobId);
    let statusReads = 0;
    const api = apiFrom((request) => {
      if (request.path === '/v1/contact-lists') return [list];
      if (request.path === `/v1/surveys/${surveyId}/audience`) return audience;
      if (request.path === `/v1/contact-imports/${importJobId}`) {
        statusReads += 1;
        return Promise.reject(new ApiError(status === 403 ? 'forbidden' : 'not_found', '导入作业不可访问', status));
      }
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(<AudiencePage api={api} surveyId={surveyId} tenantId="tenant-a" actorId="actor-a" />);
    await screen.findByText(/服务端当前可邀请/);
    fireEvent.change(screen.getByLabelText('用于当前问卷的名单'), { target: { value: listId } });
    expect(await screen.findByRole('alert')).toHaveTextContent('导入作业不可访问或已不存在');
    expect(statusReads).toBe(1);
    expect(sessionStorage.getItem(importStorageKey)).toBeNull();
    expect(screen.queryByLabelText('联系人导入状态')).not.toBeInTheDocument();
  });
});
