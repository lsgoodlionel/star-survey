import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { createAppRoutes } from '../../app/router';
import { server } from '../../test/server';

vi.mock('../auth/AuthProvider', async () => {
  const { createApiClient } = await import('../../shared/api/http');
  return { useAuth: () => ({ api: createApiClient(), session: { token: 'test',
    me: { tenantId: 'tenant-a', actorId: 'author', roles: ['editor'] } } }) };
});

const surveyId = '30000000-0000-4000-8000-000000000001';
const definition = { definitionVersion: 2, title: '客户调查', language: 'zh-Hans',
  groups: [{ uuid: '40000000-0000-4000-8000-000000000001', title: '基础题组',
    questions: [{ uuid: '50000000-0000-4000-8000-000000000001', code: 'Q1', type: 'X', text: '原题目' }] }],
  settings: { anonymized: 'Y' } };

function renderPage(canEdit = true, assets: unknown[] = []) {
  server.use(
    http.get(`/v1/surveys/${surveyId}`, () => HttpResponse.json({ id: surveyId, title: '客户调查',
      status: 'draft', draftVersion: 3, publishedVersion: null, lastPublish: null })),
    http.get(`/v1/surveys/${surveyId}/draft`, () => HttpResponse.json({ surveyId, version: 3, definition })),
    http.get(`/v1/resources/${surveyId}`, () => HttpResponse.json({ id: surveyId, kind: 'survey',
      parentId: null, name: '客户调查', createdAt: '2026-10-27T00:00:00Z',
      updatedAt: '2026-10-27T00:00:00Z', archivedAt: null })),
    http.get('/v1/resource-capabilities', () => HttpResponse.json({
      canCreateProject: true, canCreateChildren: true, canEdit, canSubmitApproval: false,
      canPublishDirectly: false, canApprovePublish: false, canArchive: false, canRestore: false,
    })),
    http.get('/v1/assets', () => HttpResponse.json(assets)),
    http.post('/v1/dashboard/recent-work', () => new HttpResponse(null, { status: 204 })),
  );
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: [`/surveys/${surveyId}/branding`] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return { router, client };
}

test('saves brand and language fields while retaining the rest of the draft', async () => {
  let saved: unknown;
  server.use(http.put(`/v1/surveys/${surveyId}/draft`, async ({ request }) => {
    saved = await request.json();
    return HttpResponse.json({ surveyId, version: 4, definition: (saved as { definition: unknown }).definition });
  }));
  const user = userEvent.setup();
  renderPage();
  expect(await screen.findByRole('heading', { name: '品牌与语言' })).toBeInTheDocument();
  await user.type(screen.getByRole('textbox', { name: '主色' }), '#123456');
  await user.type(screen.getByRole('textbox', { name: '页面标题' }), '欢迎参与');
  await user.type(screen.getByRole('textbox', { name: '新增语言代码' }), 'en');
  await user.click(screen.getByRole('button', { name: '添加语言' }));
  await user.type(screen.getByRole('textbox', { name: 'en 问卷标题' }), 'Survey');
  await user.click(screen.getByRole('button', { name: '保存品牌与语言' }));
  expect(await screen.findByText(/已保存/)).toBeInTheDocument();
  expect(saved).toMatchObject({ expectedVersion: 3, definition: {
    settings: { anonymized: 'Y' }, theme: 'zh-business',
    branding: { brandingVersion: 1, primaryColor: '#123456', pageTitle: '欢迎参与' },
    additionalLanguages: ['en'], translations: { en: { title: 'Survey' } },
  } });
});

test('read-only branding page exposes no save action', async () => {
  renderPage(false);
  expect(await screen.findByRole('heading', { name: '品牌与语言' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '保存品牌与语言' })).not.toBeInTheDocument();
});

test('rejects invalid colors and retains local input on a draft conflict', async () => {
  let writes = 0;
  server.use(http.put(`/v1/surveys/${surveyId}/draft`, () => {
    writes++;
    return HttpResponse.json({ code: 'VERSION_CONFLICT' }, { status: 409 });
  }));
  const user = userEvent.setup();
  renderPage();
  const color = await screen.findByRole('textbox', { name: '主色' });
  await user.type(color, 'red');
  await user.click(screen.getByRole('button', { name: '保存品牌与语言' }));
  expect(screen.getByRole('alert')).toHaveTextContent('主色须为 #rgb 或 #rrggbb');
  expect(writes).toBe(0);
  await user.clear(color);
  await user.type(color, '#abc');
  await user.click(screen.getByRole('button', { name: '保存品牌与语言' }));
  expect(await screen.findByText('草稿版本已变化，请重新加载后再保存')).toBeInTheDocument();
  expect(color).toHaveValue('#abc');
  expect(writes).toBe(1);
});

test('requires Logo alternative text unless it is explicitly decorative', async () => {
  const assetId = '60000000-0000-4000-8000-000000000001';
  let saved: unknown;
  server.use(
    http.put(`/v1/surveys/${surveyId}/draft`, async ({ request }) => {
      saved = await request.json();
      return HttpResponse.json({ surveyId, version: 4, definition: (saved as { definition: unknown }).definition });
    }),
  );
  const user = userEvent.setup();
  renderPage(true, [{ id: assetId, name: '标志', kind: 'image', status: 'active',
    currentVersion: 1, contentType: 'image/png', byteSize: 100, sha256: 'a'.repeat(64),
    createdAt: '2026-10-27T00:00:00Z', updatedAt: '2026-10-27T00:00:00Z' },
  { id: '70000000-0000-4000-8000-000000000001', name: '已归档', kind: 'image', status: 'archived',
    currentVersion: 1, contentType: 'image/png', byteSize: 100, sha256: 'a'.repeat(64),
    createdAt: '2026-10-27T00:00:00Z', updatedAt: '2026-10-27T00:00:00Z' }]);
  const logo = await screen.findByRole('combobox', { name: 'Logo' });
  await within(logo).findByRole('option', { name: '标志' });
  expect(within(logo).queryByRole('option', { name: '已归档' })).not.toBeInTheDocument();
  await user.selectOptions(logo, assetId);
  await user.click(screen.getByRole('button', { name: '保存品牌与语言' }));
  expect(screen.getByRole('alert')).toHaveTextContent('使用 Logo 时请填写替代文本');
  await user.click(screen.getByRole('checkbox', { name: 'Logo 仅作装饰' }));
  await user.click(screen.getByRole('button', { name: '保存品牌与语言' }));
  expect(await screen.findByText(/已保存/)).toBeInTheDocument();
  expect(saved).toMatchObject({ definition: { branding: { logoAsset: { assetId }, logoAlt: '' } } });
});

test('blocks route changes and browser unload while brand edits are unsaved', async () => {
  const user = userEvent.setup();
  const { router } = renderPage();
  await user.type(await screen.findByRole('textbox', { name: '主色' }), '#abc');
  const unload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
  void router.navigate(`/surveys/${surveyId}/edit`);
  expect(await screen.findByRole('dialog', { name: '未保存修改' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '继续编辑' }));
  expect(router.state.location.pathname).toBe(`/surveys/${surveyId}/branding`);
});

test('reports upload failure and lets the user retry with the same image', async () => {
  const assetId = '80000000-0000-4000-8000-000000000001';
  const asset = { id: assetId, name: '新标志.png', kind: 'image', status: 'active',
    currentVersion: 1, contentType: 'image/png', byteSize: 4, sha256: 'a'.repeat(64),
    createdAt: '2026-10-27T00:00:00Z', updatedAt: '2026-10-27T00:00:00Z' };
  let attempts = 0;
  const assets: unknown[] = [];
  server.use(http.post('/v1/assets', () => {
    attempts++;
    if (attempts > 1) assets.push(asset);
    return attempts === 1 ? HttpResponse.json({ code: 'UPLOAD_FAILED' }, { status: 503 })
      : HttpResponse.json(asset, { status: 201 });
  }));
  const user = userEvent.setup();
  renderPage(true, assets);
  const file = new File(['test'], '新标志.png', { type: 'image/png' });
  const upload = await screen.findByLabelText('上传Logo');
  await user.upload(upload, file);
  expect(await screen.findByText('图片上传失败，请重新选择文件重试')).toBeInTheDocument();
  await user.upload(upload, file);
  expect(await within(screen.getByRole('combobox', { name: 'Logo' }))
    .findByRole('option', { name: '新标志.png' })).toBeInTheDocument();
  expect(attempts).toBe(2);
});
