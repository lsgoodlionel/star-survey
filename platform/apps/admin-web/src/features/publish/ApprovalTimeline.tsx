import type { ApprovalRequest, ApprovalStatus } from '../../shared/api/approvals';

export const approvalStatusLabels: Record<ApprovalStatus, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
  withdrawn: '已撤回',
  voided: '已作废',
  published: '已发布',
};

interface ApprovalTimelineProps {
  approvals: ApprovalRequest[];
}

export function ApprovalTimeline({ approvals }: ApprovalTimelineProps) {
  if (approvals.length === 0) return <p className="publish-empty">尚无审批记录</p>;

  return (
    <ol className="approval-timeline" aria-label="审批记录">
      {approvals.map((approval) => (
        <li key={approval.id}>
          <div className="approval-timeline__heading">
            <strong>{approvalStatusLabels[approval.status]}</strong>
            <span>草稿版本 {approval.draftVersion}</span>
          </div>
          <dl className="publish-metadata">
            <div><dt>申请人</dt><dd>{approval.applicant}</dd></div>
            <div><dt>提交时间</dt><dd>{formatDate(approval.submittedAt)}</dd></div>
            {approval.decidedBy ? <div><dt>处理人</dt><dd>{approval.decidedBy}</dd></div> : null}
          </dl>
          {approval.reason ? <p className="approval-reason">{approval.reason}</p> : null}
        </li>
      ))}
    </ol>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN');
}
