import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import type { ApiClient } from '../../shared/api/http';
import {
  addTenantTemplateVersion, approveTemplate, rejectTemplate, submitTemplate, unpublishTemplate,
  type TemplateCapabilities, type TemplateView,
} from '../../shared/api/templates';

type Action = 'addVersion' | 'submit' | 'approve' | 'reject' | 'unpublish';

interface TemplateActionsProps {
  api: ApiClient;
  tenantId: string;
  template: TemplateView;
  sourceSurveyId: string | null;
  capabilities: TemplateCapabilities;
  onRefresh(): void;
}

export function TemplateActions({ api, tenantId, template, sourceSurveyId, capabilities, onRefresh }: TemplateActionsProps) {
  const queryClient = useQueryClient();
  const submitLock = useRef(false);
  const [showReject, setShowReject] = useState(false);
  const [reason, setReason] = useState('');
  const [validation, setValidation] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async (action: Action) => {
      switch (action) {
        case 'addVersion':
          if (!sourceSurveyId) throw new Error('来源问卷不可用');
          return addTenantTemplateVersion(api, template.id, sourceSurveyId);
        case 'submit': return submitTemplate(api, template.id);
        case 'approve': return approveTemplate(api, template.id);
        case 'reject': return rejectTemplate(api, template.id, reason.trim());
        case 'unpublish': return unpublishTemplate(api, template.id);
      }
    },
    onSuccess: async () => {
      setValidation(null);
      setSuccess('操作已完成');
      setShowReject(false);
      setReason('');
      await queryClient.invalidateQueries({ queryKey: ['templates', tenantId] });
      onRefresh();
    },
    onError: (error) => {
      if (error && typeof error === 'object' && 'status' in error && error.status === 409) onRefresh();
    },
    onSettled: () => { submitLock.current = false; },
  });

  function run(action: Action) {
    if (submitLock.current) return;
    setSuccess(null);
    setValidation(null);
    if (action === 'reject') {
      const clean = reason.trim();
      if (!clean) { setValidation('请填写驳回原因'); return; }
      if (clean.length > 1000) { setValidation('驳回原因不能超过 1000 字'); return; }
    }
    submitLock.current = true;
    mutation.mutate(action);
  }

  const busy = mutation.isPending;
  const conflict = mutation.error && typeof mutation.error === 'object'
    && 'status' in mutation.error && mutation.error.status === 409;
  const forbidden = mutation.error && typeof mutation.error === 'object'
    && 'status' in mutation.error && mutation.error.status === 403;

  return <section className="template-actions" aria-label="模板管理操作">
    <h2>管理模板</h2>
    {capabilities.canAddVersion && sourceSurveyId ? <button type="button" disabled={busy}
      onClick={() => run('addVersion')}>添加版本</button> : null}
    {capabilities.canSubmit ? <button type="button" disabled={busy}
      onClick={() => run('submit')}>提交审核</button> : null}
    {capabilities.canReview ? <>
      <button type="button" disabled={busy} onClick={() => run('approve')}>批准</button>
      <button type="button" disabled={busy} onClick={() => { setShowReject(true); setValidation(null); }}>驳回</button>
      {showReject ? <div className="template-reject-form">
        <label>驳回原因 <textarea aria-label="驳回原因" value={reason}
          onChange={(event) => setReason(event.target.value)} /></label>
        <button type="button" disabled={busy} onClick={() => run('reject')}>确认驳回</button>
      </div> : null}
    </> : null}
    {capabilities.canUnpublish ? <button type="button" disabled={busy}
      onClick={() => run('unpublish')}>下架</button> : null}
    {busy ? <p role="status">操作进行中</p> : null}
    {success ? <p role="status">{success}</p> : null}
    {validation ? <p role="alert">{validation}</p> : null}
    {mutation.isError ? <p role="alert">{conflict ? '模板状态已变化，请刷新后重试' : forbidden
      ? '无权执行此操作' : '操作失败，请稍后重试'}</p> : null}
  </section>;
}
