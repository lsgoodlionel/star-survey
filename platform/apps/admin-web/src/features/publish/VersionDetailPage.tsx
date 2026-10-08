import { useQuery } from '@tanstack/react-query';
import { LockKeyhole } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import type { ApiClient } from '../../shared/api/http';
import { getPublishedVersion, versionQueryKey } from '../../shared/api/approvals';
import { parsePositiveIntegerParam, parseSurveyIdParam } from './routeParams';
import './publish.css';

interface VersionDetailPageProps {
  api: ApiClient;
  surveyId: string;
  version: number;
}

export function VersionDetailPage({ api, surveyId, version }: VersionDetailPageProps) {
  const query = useQuery({
    queryKey: versionQueryKey(surveyId, version),
    queryFn: ({ signal }) => getPublishedVersion(api, surveyId, version, signal),
  });

  if (query.isPending) return <p className="publish-loading">正在加载版本</p>;
  if (query.error || !query.data) return <p role="alert">该版本不存在或不可访问。</p>;

  const item = query.data;
  return (
    <main className="version-detail">
      <header className="version-detail__header">
        <div><p className="publish-eyebrow">不可变发布快照</p><h1>已发布版本 {item.version}</h1></div>
        <span className={`live-state ${item.live ? 'live-state--active' : ''}`}><LockKeyhole size={16} aria-hidden="true" />{item.live ? '当前在线' : '历史版本'}</span>
      </header>

      <dl className="version-binding">
        <div><dt>引擎绑定</dt><dd>{item.engineInstanceId} / {item.engineSid}</dd></div>
        <div><dt>发布时间</dt><dd>{formatDate(item.publishedAt)}</dd></div>
        <div><dt>发布人</dt><dd>{item.publishedBy}</dd></div>
        <div><dt>草稿版本</dt><dd>{item.draftVersion}</dd></div>
        <div><dt>编译器</dt><dd>{item.compilerVersion}</dd></div>
        <div><dt>指纹</dt><dd className="version-fingerprint">{item.fingerprint}</dd></div>
      </dl>

      <section className="version-fields" aria-labelledby="version-fields-title">
        <h2 id="version-fields-title">题目字段映射</h2>
        <div className="version-table-wrap">
          <table><thead><tr><th>题目代码</th><th>题型</th><th>答卷字段</th><th>子项</th><th>尺度</th></tr></thead>
            <tbody>{item.fields.map((field, index) => <tr key={`${field.questionUuid}-${field.fieldname}-${index}`}><td>{field.code}</td><td>{field.type}</td><td>{field.fieldname}</td><td>{field.aid || '—'}</td><td>{field.scale}</td></tr>)}</tbody>
          </table>
        </div>
      </section>

      <section className="version-definition" aria-labelledby="version-definition-title">
        <h2 id="version-definition-title">问卷定义快照</h2>
        <pre>{JSON.stringify(item.definition, null, 2)}</pre>
      </section>
    </main>
  );
}

export function VersionDetailRoutePage() {
  const { api } = useAuth();
  const params = useParams();
  const surveyId = parseSurveyIdParam(params.surveyId);
  const versionNumber = parsePositiveIntegerParam(params.version);
  if (!surveyId || versionNumber == null) return <p role="alert">版本标识无效</p>;
  return <VersionDetailPage api={api} surveyId={surveyId} version={versionNumber} />;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN');
}
