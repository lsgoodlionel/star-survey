import { fireEvent, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { renderWithQuery } from '../../test/render';
import { PreviewPage } from './PreviewPage';
import type { PreviewClient, PreviewSessionView } from '../../shared/api/previews';

const surveyId = '11111111-1111-4111-8111-111111111111';
const tenantId = 'tenant-a';
const draft = {
  surveyId,
  version: 7,
  definition: {
    definitionVersion: 2,
    title: '员工体验调查',
    description: '这是一份尚未发布的内部草稿。',
    language: 'zh-Hans',
    groups: [
      {
        uuid: '22222222-2222-4222-8222-222222222222',
        title: '基础题型',
        questions: [
          { uuid: crypto.randomUUID(), code: 'QX', type: 'X', text: '请认真作答' },
          {
            uuid: crypto.randomUUID(),
            code: 'QL',
            type: 'L',
            text: '请选择一项',
            answers: [{ code: 'A1', text: '选项一' }, { code: 'A2', text: '选项二' }],
          },
          {
            uuid: crypto.randomUUID(),
            code: 'QM',
            type: 'M',
            text: '请选择多项',
            answers: [{ code: 'A1', text: '功能一' }, { code: 'A2', text: '功能二' }],
          },
          { uuid: crypto.randomUUID(), code: 'QS', type: 'S', text: '简短回答' },
          { uuid: crypto.randomUUID(), code: 'QT', type: 'T', text: '详细回答' },
          { uuid: crypto.randomUUID(), code: 'QZ', type: 'Z99', text: '复杂题型' },
        ],
      },
    ],
  },
};

const readySession: PreviewSessionView = {
  id: '33333333-3333-4333-8333-333333333333',
  requestId: '44444444-4444-4444-8444-444444444444',
  surveyId,
  draftVersion: 7,
  requestedBy: 'owner-a',
  engineInstanceId: 'engine-a',
  engineSid: 123456,
  generation: 'preview-generation-a',
  previewUrl: 'https://survey.example/v1/preview/access?token=opaque',
  expiresAt: '2099-10-09T09:30:00Z',
  status: 'ready',
  failure: null,
  cleanupAttempts: 0,
  createdAt: '2026-10-09T09:00:00Z',
  updatedAt: '2026-10-09T09:00:01Z',
  closedAt: null,
};

function previewClient(overrides: Partial<PreviewClient> = {}): PreviewClient {
  return {
    create: () => Promise.resolve(readySession),
    get: () => Promise.resolve(readySession),
    close: () => Promise.resolve({ ...readySession, status: 'closed', closedAt: '2026-10-09T09:05:00Z' }),
    ...overrides,
  };
}

function renderPreview(api: ApiClient, client = previewClient()) {
  return renderWithQuery(
    <MemoryRouter>
      <PreviewPage api={api} previewClient={client} surveyId={surveyId} tenantId={tenantId} />
    </MemoryRouter>,
  );
}

describe('PreviewPage', () => {
  test('linksBackToTheEditorWithoutReloadingTheSession', async () => {
    const api: ApiClient = { request: () => Promise.resolve(draft) as never };
    renderPreview(api);

    expect(await screen.findByRole('link', { name: '返回编辑' })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/edit`,
    );
  });

  test('labelsTheRendererAsQuickPreviewAndNeverCallsTheAnswerEngine', async () => {
    const requests: ApiRequest<unknown>[] = [];
    const api: ApiClient = {
      request: (request) => {
        requests.push(request as ApiRequest<unknown>);
        return Promise.resolve(draft) as never;
      },
    };

    renderPreview(api);

    expect(await screen.findByRole('heading', { name: '快速预览' })).toBeInTheDocument();
    expect(screen.getByText('员工体验调查')).toBeInTheDocument();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ path: `/v1/surveys/${surveyId}/draft` });
    expect(screen.queryByRole('button', { name: /提交|完成问卷/ })).not.toBeInTheDocument();
  });

  test('rendersBasicQuestionsReadOnlyAndUnsupportedQuestionsAsReadablePlaceholders', async () => {
    const api: ApiClient = { request: () => Promise.resolve(draft) as never };
    renderPreview(api);

    expect(await screen.findByText('请选择一项')).toBeInTheDocument();
    expect(screen.getByText('请选择多项')).toBeInTheDocument();
    expect(screen.getByText('简短回答')).toBeInTheDocument();
    expect(screen.getByText('详细回答')).toBeInTheDocument();
    expect(screen.getByText('请认真作答')).toBeInTheDocument();
    expect(screen.getByText('复杂题型')).toBeInTheDocument();
    expect(screen.getByText('题型 Z99 暂不支持交互预览，草稿数据保持不变。')).toBeInTheDocument();
    expect(screen.getAllByRole('radio').every((input) => input.hasAttribute('disabled'))).toBe(true);
    expect(screen.getAllByRole('checkbox').every((input) => input.hasAttribute('disabled'))).toBe(true);
    expect(screen.getAllByRole('textbox').every((input) => input.hasAttribute('readonly'))).toBe(true);
  });

  test('switchesBetweenStableDesktopAndMobilePreviewWidths', async () => {
    const api: ApiClient = { request: () => Promise.resolve(draft) as never };
    renderPreview(api);

    const frame = await screen.findByTestId('draft-preview-frame');
    expect(frame).toHaveAttribute('data-preview-mode', 'desktop');
    expect(frame).toHaveStyle({ width: '960px' });

    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '桌面端' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '移动端' })).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', { name: '移动端' }));
    expect(frame).toHaveAttribute('data-preview-mode', 'mobile');
    expect(frame).toHaveStyle({ width: '390px' });
    expect(screen.getByRole('button', { name: '桌面端' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: '移动端' })).toHaveAttribute('aria-pressed', 'true');
  });

  test('createsOneRealPreviewAtATimeAndOpensTheReadySessionInANewWindow', async () => {
    let resolveCreate!: (value: PreviewSessionView) => void;
    const create = vi.fn(() => new Promise<PreviewSessionView>((resolve) => { resolveCreate = resolve; }));
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    renderPreview({ request: () => Promise.resolve(draft) as never }, previewClient({ create }));

    const createButton = await screen.findByRole('button', { name: '创建真实预览' });
    fireEvent.click(createButton);
    fireEvent.click(createButton);

    expect(create).toHaveBeenCalledTimes(1);
    expect(createButton).toBeDisabled();
    expect(screen.getByText('正在创建隔离预览')).toBeInTheDocument();

    resolveCreate(readySession);
    expect(await screen.findByText('真实预览已就绪')).toBeInTheDocument();
    expect(screen.getByText(/2099/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '在新窗口打开真实预览' }));
    expect(open).toHaveBeenCalledWith(readySession.previewUrl, '_blank', 'noopener,noreferrer');
    expect(document.querySelector('iframe')).toBeNull();
    open.mockRestore();
  });

  test('endsARealPreviewExplicitlyAndShowsTheClosedState', async () => {
    const close = vi.fn().mockResolvedValue({
      ...readySession,
      status: 'closed',
      closedAt: '2026-10-09T09:05:00Z',
    });
    renderPreview({ request: () => Promise.resolve(draft) as never }, previewClient({ close }));

    fireEvent.click(await screen.findByRole('button', { name: '创建真实预览' }));
    expect(await screen.findByText('真实预览已就绪')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '结束真实预览' }));

    await waitFor(() => expect(close).toHaveBeenCalledWith(readySession.id));
    expect(await screen.findByText('真实预览已结束')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '再次创建真实预览' })).toBeInTheDocument();
  });

  test('showsAPollingFailureAndLetsTheUserRecoverThePendingSession', async () => {
    const creating = { ...readySession, status: 'creating' as const, previewUrl: null, engineSid: null };
    const get = vi.fn()
      .mockRejectedValueOnce(new Error('temporary polling failure'))
      .mockResolvedValueOnce(readySession);
    renderPreview(
      { request: () => Promise.resolve(draft) as never },
      previewClient({ create: vi.fn().mockResolvedValue(creating), get }),
    );

    fireEvent.click(await screen.findByRole('button', { name: '创建真实预览' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('真实预览状态读取失败');
    fireEvent.click(screen.getByRole('button', { name: '重试查询预览状态' }));

    expect(await screen.findByText('真实预览已就绪')).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });

  test('startsANewOperationAfterCloseAndKeepsTheClosedSessionInAuditHistory', async () => {
    const nextSession = {
      ...readySession,
      id: '55555555-5555-4555-8555-555555555555',
      requestId: '66666666-6666-4666-8666-666666666666',
    };
    const create = vi.fn()
      .mockResolvedValueOnce(readySession)
      .mockResolvedValueOnce(nextSession);
    renderPreview({ request: () => Promise.resolve(draft) as never }, previewClient({ create }));

    fireEvent.click(await screen.findByRole('button', { name: '创建真实预览' }));
    await screen.findByText('真实预览已就绪');
    fireEvent.click(screen.getByRole('button', { name: '结束真实预览' }));
    await screen.findByText('真实预览已结束');
    fireEvent.click(screen.getByRole('button', { name: '再次创建真实预览' }));

    expect(await screen.findByText('真实预览已就绪')).toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(2);
    expect(create.mock.calls[0]?.[1].requestId).not.toBe(create.mock.calls[1]?.[1].requestId);
    expect(screen.getByRole('region', { name: '真实预览历史' })).toHaveTextContent('已结束');
  });

  test('treatsAnExpiredReadySessionAsTerminalAndOffersANewPreview', async () => {
    const expired = { ...readySession, expiresAt: '2000-01-01T00:00:00Z' };
    renderPreview(
      { request: () => Promise.resolve(draft) as never },
      previewClient({ create: vi.fn().mockResolvedValue(expired) }),
    );

    fireEvent.click(await screen.findByRole('button', { name: '创建真实预览' }));

    expect(await screen.findByText('真实预览已过期')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '在新窗口打开真实预览' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '再次创建真实预览' })).toBeInTheDocument();
  });

  test('keepsTheRequestIdAfterFailureAndRetriesTheSameOperation', async () => {
    const create = vi.fn()
      .mockRejectedValueOnce(new Error('network details must stay hidden'))
      .mockResolvedValueOnce(readySession);
    renderPreview({ request: () => Promise.resolve(draft) as never }, previewClient({ create }));

    fireEvent.click(await screen.findByRole('button', { name: '创建真实预览' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('真实预览创建失败，请使用同一请求重试。');
    expect(screen.getByText(/请求编号：/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试真实预览' }));

    expect(await screen.findByText('真实预览已就绪')).toBeInTheDocument();
    expect(create).toHaveBeenCalledTimes(2);
    expect(create.mock.calls[0]?.[1].requestId).toBe(create.mock.calls[1]?.[1].requestId);
  });
});
