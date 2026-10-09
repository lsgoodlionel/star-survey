import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Monitor, Smartphone } from 'lucide-react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { z } from 'zod';
import { surveyWorkflowHref, useSurveyShell } from '../../app/SurveyShell';
import { useAuth } from '../auth/AuthProvider';
import { parseDefinition } from '../editor/model/definition';
import type { ApiClient } from '../../shared/api/http';
import { getSurveyDraft, surveyDraftQueryKey } from '../../shared/api/surveys';
import { DraftRenderer } from './DraftRenderer';
import './preview.css';

interface PreviewPageProps {
  api: ApiClient;
  surveyId: string;
  tenantId: string;
}

type PreviewMode = 'desktop' | 'mobile';

export function PreviewPage(props: PreviewPageProps) {
  return <PreviewPageInstance key={`${props.tenantId}:${props.surveyId}`} {...props} />;
}

function PreviewPageInstance({ api, surveyId, tenantId }: PreviewPageProps) {
  const [mode, setMode] = useState<PreviewMode>('desktop');
  const surveyShell = useSurveyShell();
  const [searchParams] = useSearchParams();
  const selectedQuestion = searchParams.get('question');
  const draftQuery = useQuery({
    queryKey: surveyDraftQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurveyDraft(api, surveyId, signal),
  });

  if (draftQuery.isPending) return <p>正在加载快速预览</p>;
  if (draftQuery.isError || !draftQuery.data) return <p role="alert">快速预览暂时不可用。</p>;

  let definition;
  try {
    definition = parseDefinition(draftQuery.data.definition);
  } catch {
    return <p role="alert">草稿格式无法预览。</p>;
  }

  return (
    <main className="preview-page">
      <header className="preview-toolbar">
        {!surveyShell ? (
          <div>
            <p>只读视图</p>
            <h1>快速预览</h1>
          </div>
        ) : <h2 className="sr-only">快速预览</h2>}
        <div className="preview-toolbar-actions">
          {!surveyShell ? (
            <Link to={surveyWorkflowHref(surveyId, 'edit', selectedQuestion)}>
              <ArrowLeft aria-hidden="true" />
              返回编辑
            </Link>
          ) : null}
          <div className="preview-segments" role="group" aria-label="预览设备">
            <button
              type="button"
              aria-pressed={mode === 'desktop'}
              onClick={() => setMode('desktop')}
            >
              <Monitor aria-hidden="true" />
              桌面端
            </button>
            <button
              type="button"
              aria-pressed={mode === 'mobile'}
              onClick={() => setMode('mobile')}
            >
              <Smartphone aria-hidden="true" />
              移动端
            </button>
          </div>
        </div>
      </header>
      <div className="preview-stage">
        <div
          className="draft-preview-frame"
          data-testid="draft-preview-frame"
          data-preview-mode={mode}
          style={{ width: mode === 'desktop' ? '960px' : '390px' }}
        >
          <div className="draft-preview-label">快速预览 · 本地草稿渲染，不会提交答案</div>
          <DraftRenderer definition={definition} />
        </div>
      </div>
    </main>
  );
}

export function PreviewRoutePage() {
  const { api, session } = useAuth();
  const surveyId = z.string().uuid().safeParse(useParams().surveyId);
  if (!surveyId.success || !session) return <p role="alert">问卷标识无效</p>;
  return (
    <PreviewPage
      api={api}
      surveyId={surveyId.data}
      tenantId={session.me.tenantId}
    />
  );
}
