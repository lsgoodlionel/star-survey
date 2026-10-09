import { fireEvent, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import { ApiError } from '../../shared/api/errors';
import type { LinkView } from '../../shared/api/delivery';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { renderWithQuery } from '../../test/render';
import { PublishedAccessPanel } from './PublishedAccessPanel';

const surveyId = '11111111-1111-4111-8111-111111111111';
const linkId = '22222222-2222-4222-8222-222222222222';
const activeLink = {
  id: linkId,
  surveyId,
  label: '正式投放',
  signedParams: {},
  url: 'https://survey.example.test/s/abc',
  shortUrl: 'https://s.example.test/abc',
  expiresAt: null,
  revokedAt: null,
  createdAt: '2026-10-09T08:00:00Z',
};
const summary = {
  surveyId,
  versions: [{ version: 2, counts: { inProgress: 1, engineCompleted: 3, deleted: 0 } }],
  total: { inProgress: 1, engineCompleted: 3, deleted: 0 },
};

function apiFrom(handler: (request: ApiRequest<unknown>) => unknown): ApiClient {
  return { request: (request) => Promise.resolve(handler(request as ApiRequest<unknown>)) as never };
}

describe('PublishedAccessPanel', () => {
  test('creates copies and revokes a published link while showing backend QR and response entry', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:qr');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const fetchQr = vi.fn().mockResolvedValue(new Blob(['qr'], { type: 'image/png' }));
    let links: LinkView[] = [activeLink];
    const api = apiFrom((request) => {
      const method = request.method ?? 'GET';
      if (method === 'GET' && request.path === `/v1/delivery/surveys/${surveyId}/links`) return links;
      if (method === 'GET' && request.path === `/v1/surveys/${surveyId}/responses/summary`) return summary;
      if (method === 'POST' && request.path === `/v1/delivery/surveys/${surveyId}/links`) {
        links = [{ ...activeLink, id: '33333333-3333-4333-8333-333333333333', label: '客户邀请' }];
        return links[0];
      }
      if (method === 'DELETE' && request.path === `/v1/delivery/links/${links[0].id}`) {
        links = [{ ...links[0], revokedAt: '2026-10-09T09:00:00Z' }];
        return links[0];
      }
      throw new Error(`Unhandled request: ${method} ${request.path}`);
    });

    const rendered = renderWithQuery(
      <MemoryRouter>
        <PublishedAccessPanel
          api={api}
          fetchQr={fetchQr}
          published
          surveyId={surveyId}
          tenantId="tenant-a"
        />
      </MemoryRouter>,
    );
    const invalidate = vi.spyOn(rendered.queryClient, 'invalidateQueries');

    expect(await screen.findByRole('heading', { name: '投放与答卷' })).toBeInTheDocument();
    expect(await screen.findByLabelText('已完成 3')).toBeInTheDocument();
    expect(screen.getByLabelText('填写中 1')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '答卷与导出' })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/responses`,
    );
    expect(await screen.findByRole('img', { name: '正式投放二维码' })).toHaveAttribute('src', 'blob:qr');
    expect(fetchQr).toHaveBeenCalledWith(linkId, expect.any(AbortSignal));
    expect(createObjectURL).toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '复制正式投放链接' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(activeLink.shortUrl));

    fireEvent.change(screen.getByLabelText('链接名称'), { target: { value: '客户邀请' } });
    fireEvent.click(screen.getByRole('button', { name: '创建链接' }));
    expect(await screen.findByText('客户邀请')).toBeInTheDocument();
    expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({
      queryKey: ['delivery-links', 'tenant-a', surveyId],
      exact: true,
    }));
    expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({
      queryKey: ['responses', 'tenant-a', surveyId, 'summary'],
      exact: true,
    }));

    fireEvent.click(screen.getByRole('button', { name: '撤销客户邀请链接' }));
    expect(await screen.findByText('已撤销')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '复制客户邀请链接' })).not.toBeInTheDocument();
  });

  test('keeps unpublished, forbidden and failed states distinct and actionable', async () => {
    const api = apiFrom(() => {
      throw new Error('unpublished must not load delivery data');
    });
    const unpublished = renderWithQuery(
      <PublishedAccessPanel api={api} published={false} surveyId={surveyId} tenantId="tenant-a" />,
    );
    expect(screen.getByText('发布问卷后即可创建正式答卷链接。')).toBeInTheDocument();
    unpublished.unmount();

    const forbiddenApi = apiFrom((request) => {
      if (request.path.includes('/responses/summary')) return summary;
      throw new ApiError('forbidden', '无权执行当前操作', 403);
    });
    const forbidden = renderWithQuery(
      <PublishedAccessPanel api={forbiddenApi} published surveyId={surveyId} tenantId="tenant-a" />,
    );
    expect(await screen.findByText('你可以查看答卷摘要，但没有管理投放链接的权限。')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '答卷与导出' })).toBeInTheDocument();
    forbidden.unmount();

    const failedApi = apiFrom(() => { throw new ApiError('unavailable', '服务暂时不可用'); });
    renderWithQuery(
      <PublishedAccessPanel api={failedApi} published surveyId={surveyId} tenantId="tenant-a" />,
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('投放与答卷信息加载失败');
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument();
  });
});
