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
      <div
        aria-atomic="true"
        aria-live="polite"
        className={`publish-status__summary publish-status__summary--${status}`}
        role="status"
      >
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
          <FailureDetails
            failures={attempt.failures}
            gatewayStatus={attempt.gatewayStatus}
            stage={attempt.failedStage}
          />
        </div>
      ) : null}
    </section>
  );
}

function FailureDetails({ failures, gatewayStatus, stage }: {
  failures: string[];
  gatewayStatus: number | null;
  stage: string;
}) {
  const messages = gatewayStatus === 422 && (stage === 'validate' || stage === 'validation')
    ? failures.map(validationMessage).filter((message): message is string => message != null)
    : [];
  if (messages.length === 0) return <p>发布失败，详细信息已隐藏。</p>;
  return <ul>{messages.map((message, index) => <li key={`${message}-${index}`}>{message}</li>)}</ul>;
}

const validationMessages: Record<string, string> = {
  E_ANSWER_CODE_DUPLICATE: '选项代码重复',
  E_ANSWER_CODE_INVALID: '选项代码格式不正确',
  E_DUPLICATE_UUID: '题目或题组标识重复',
  E_EMPTY_GROUP: '题组内没有题目',
  E_EMPTY_SURVEY: '问卷内没有题目',
  E_MISSING_ANSWERS: '题目缺少选项',
  E_MISSING_SUBQUESTIONS: '题目缺少子题',
  E_QUESTION_CODE_DUPLICATE: '题目代码重复',
  E_QUESTION_CODE_INVALID: '题目代码格式不正确',
  E_QUESTION_CODE_TOO_LONG: '题目代码过长',
  E_UNSUPPORTED_TYPE: '包含不支持的题型',
};

const validationFailurePattern = /^([A-Z][A-Z0-9_]{1,63})(?: ([^ :\r\n]{1,160}): ([^\r\n]{1,300}))?$/;
const validationPathPattern = /^(?:[A-Za-z_][A-Za-z0-9_]{0,31})(?:\[(?:(?:0|[1-9][0-9]{0,5})|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})?\])?(?:\.(?:[A-Za-z_][A-Za-z0-9_]{0,31})(?:\[(?:(?:0|[1-9][0-9]{0,5})|[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})?\])?)*$/;
const sensitiveFailureContent = /authorization|bearer|password|secret|stack\s*trace|traceback|exception|api[_-]?key|private[_-]?key/i;

function validationMessage(value: string) {
  if (value.length > 512 || sensitiveFailureContent.test(value)) return null;
  const match = validationFailurePattern.exec(value);
  if (!match) return null;
  const message = validationMessages[match[1]];
  if (!message) return null;
  const path = match[2];
  if (path && !validationPathPattern.test(path)) return null;
  return path ? `${message}（${path}）` : message;
}

function safeStageLabel(stage: string) {
  return stageLabels[stage] ?? '未知阶段';
}
