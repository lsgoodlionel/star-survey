import { AlertTriangle, CheckCircle2, Clock3, XCircle } from 'lucide-react';
import type { SurveyOverview } from '../../shared/api/approvals';

interface PublishStatusProps {
  survey: SurveyOverview;
  awaitingPublishResult?: boolean;
}

const statusLabels: Record<SurveyOverview['status'], string> = {
  draft: '草稿',
  publishing: '发布处理中',
  published: '发布成功',
  publish_failed: '发布失败',
  pending_reconciliation: '发布结果正在核对',
};

const stageLabels: Record<string, string> = {
  validate: '定义校验',
  validation: '定义校验',
  compile: '问卷编译',
  create: '创建问卷',
  create_survey: '创建问卷',
  groups: '创建题组',
  questions: '创建题目',
  activate: '激活问卷',
  invitations: '创建邀请表',
  policy: '应用发布策略',
  verify: '发布校验',
  rollback: '失败回滚',
  expired: '发布请求已过期',
};

export function PublishStatus({ survey, awaitingPublishResult = false }: PublishStatusProps) {
  const attempt = survey.lastPublish;
  const status = awaitingPublishResult && survey.status === 'draft' ? 'pending_reconciliation' : survey.status;
  const Icon = status === 'published' ? CheckCircle2 : status === 'publish_failed' ? XCircle : Clock3;

  return (
    <section className="publish-status" aria-labelledby="publish-status-title">
      <div className={`publish-status__summary publish-status__summary--${status}`}>
        <Icon aria-hidden="true" size={20} />
        <div>
          <h2 id="publish-status-title">{statusLabels[status]}</h2>
          <p>当前草稿版本 {survey.draftVersion}</p>
        </div>
      </div>

      {attempt?.manualReviewAt ? (
        <div className="publish-warning" role="alert">
          <AlertTriangle aria-hidden="true" size={20} />
          <div><strong>需要人工复核</strong><p>自动核对已停止，请联系管理员处理本次发布。</p></div>
        </div>
      ) : null}

      {attempt?.orphanEngineSid != null ? (
        <div className="publish-warning" role="alert">
          <AlertTriangle aria-hidden="true" size={20} />
          <div><strong>存在孤儿问卷 {attempt.orphanEngineSid}</strong><p>引擎回滚未完成，需要管理员人工清理。</p></div>
        </div>
      ) : null}

      {attempt?.failedStage ? (
        <div className="publish-failure">
          <h3>{safeStageLabel(attempt.failedStage)}</h3>
          {attempt.failures.length > 0 ? (
            <ul>{attempt.failures.map((failure, index) => <li key={index}>{sanitizeFailure(failure)}</li>)}</ul>
          ) : <p>发布未完成，请稍后重试或联系管理员。</p>}
        </div>
      ) : null}
    </section>
  );
}

export function sanitizeFailure(value: string) {
  const unsafe = /authorization|bearer\s|password|secret|token|exception|stack\s*trace|\bat\s+[\w.$]+\s*\(/i;
  if (unsafe.test(value)) return '详细错误已隐藏，请联系管理员。';
  const firstLine = Array.from(value.split(/\r?\n/, 1)[0] ?? '')
    .filter((character) => {
      const code = character.charCodeAt(0);
      return code >= 32 && code !== 127;
    })
    .join('')
    .trim();
  if (!firstLine) return '发布服务返回了空错误详情。';
  return firstLine.length > 180 ? `${firstLine.slice(0, 177)}...` : firstLine;
}

function safeStageLabel(stage: string) {
  return stageLabels[stage] ?? '未知阶段';
}
