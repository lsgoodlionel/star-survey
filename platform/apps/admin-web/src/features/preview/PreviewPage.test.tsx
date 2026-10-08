import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { renderWithQuery } from '../../test/render';
import { PreviewPage } from './PreviewPage';

const surveyId = '11111111-1111-4111-8111-111111111111';
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

describe('PreviewPage', () => {
  test('labelsTheRendererAsDraftPreviewAndNeverCallsTheAnswerEngine', async () => {
    const requests: ApiRequest<unknown>[] = [];
    const api: ApiClient = {
      request: (request) => {
        requests.push(request as ApiRequest<unknown>);
        return Promise.resolve(draft) as never;
      },
    };

    renderWithQuery(<PreviewPage api={api} surveyId={surveyId} />);

    expect(await screen.findByRole('heading', { name: '草稿预览' })).toBeInTheDocument();
    expect(screen.getByText('员工体验调查')).toBeInTheDocument();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ path: `/v1/surveys/${surveyId}/draft` });
    expect(screen.queryByRole('button', { name: /提交|完成问卷/ })).not.toBeInTheDocument();
  });

  test('rendersBasicQuestionsReadOnlyAndUnsupportedQuestionsAsReadablePlaceholders', async () => {
    const api: ApiClient = { request: () => Promise.resolve(draft) as never };
    renderWithQuery(<PreviewPage api={api} surveyId={surveyId} />);

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
    renderWithQuery(<PreviewPage api={api} surveyId={surveyId} />);

    const frame = await screen.findByTestId('draft-preview-frame');
    expect(frame).toHaveAttribute('data-preview-mode', 'desktop');
    expect(frame).toHaveStyle({ width: '960px' });

    fireEvent.click(screen.getByRole('tab', { name: '移动端' }));
    expect(frame).toHaveAttribute('data-preview-mode', 'mobile');
    expect(frame).toHaveStyle({ width: '390px' });
  });
});
