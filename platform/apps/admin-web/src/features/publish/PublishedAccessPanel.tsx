import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Clipboard, ExternalLink, Link2, Link2Off, Plus, RefreshCw } from 'lucide-react';
import { Link, useInRouterContext } from 'react-router-dom';
import {
  createDeliveryLink,
  deliveryLinksQueryKey,
  fetchDeliveryQr,
  listDeliveryLinks,
  revokeDeliveryLink,
  type LinkView,
} from '../../shared/api/delivery';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient } from '../../shared/api/http';
import {
  getResponseSummary,
  responseSummaryQueryKey,
  type ResponseSummary,
} from '../../shared/api/responses';
import '../responses/responses.css';

interface PublishedAccessPanelProps {
  api: ApiClient;
  published: boolean;
  surveyId: string;
  tenantId: string;
  accessToken?: string;
  onUnauthorized?: () => void | Promise<void>;
  fetchQr?: (linkId: string, signal: AbortSignal) => Promise<Blob>;
}

export function PublishedAccessPanel({
  api,
  published,
  surveyId,
  tenantId,
  accessToken,
  onUnauthorized,
  fetchQr: fetchQrOverride,
}: PublishedAccessPanelProps) {
  const queryClient = useQueryClient();
  const [label, setLabel] = useState('正式投放');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const links = useQuery({
    queryKey: deliveryLinksQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => listDeliveryLinks(api, surveyId, signal),
    enabled: published,
    retry: false,
  });
  const summary = useQuery({
    queryKey: responseSummaryQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getResponseSummary(api, surveyId, signal),
    enabled: published,
    retry: false,
  });

  const refreshAfterLinkChange = async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: deliveryLinksQueryKey(tenantId, surveyId),
        exact: true,
      }),
      queryClient.invalidateQueries({
        queryKey: responseSummaryQueryKey(tenantId, surveyId),
        exact: true,
      }),
    ]);
  };
  const createLink = useMutation({
    mutationFn: () => createDeliveryLink(api, surveyId, {
      label: label.trim(),
      shortLink: true,
    }),
    onMutate: () => setActionError(null),
    onSuccess: () => refreshAfterLinkChange(),
    onError: (error) => setActionError(publicMessage(error)),
  });
  const revokeLink = useMutation({
    mutationFn: (linkId: string) => revokeDeliveryLink(api, linkId),
    onMutate: () => setActionError(null),
    onSuccess: () => refreshAfterLinkChange(),
    onError: (error) => setActionError(publicMessage(error)),
  });
  const qrLoader = useMemo(() => fetchQrOverride ?? ((linkId: string, signal: AbortSignal) =>
    fetchDeliveryQr({
      getToken: () => accessToken ?? null,
      onUnauthorized: onUnauthorized ? () => onUnauthorized() : undefined,
    }, linkId, { signal })), [accessToken, fetchQrOverride, onUnauthorized]);

  if (!published) {
    return (
      <section className="aftercare-panel" aria-labelledby="aftercare-title">
        <h2 id="aftercare-title">投放与答卷</h2>
        <p className="aftercare-empty">发布问卷后即可创建正式答卷链接。</p>
      </section>
    );
  }

  const linksForbidden = isForbidden(links.error);
  const summaryForbidden = isForbidden(summary.error);
  const failed = (links.isError && !linksForbidden) || (summary.isError && !summaryForbidden);
  const pending = links.isPending || summary.isPending;

  return (
    <section className="aftercare-panel" aria-labelledby="aftercare-title">
      <div className="aftercare-panel__heading">
        <div>
          <h2 id="aftercare-title">投放与答卷</h2>
          <p>发布后在这里管理访问入口并跟踪回收情况。</p>
        </div>
        <ResponsesLink surveyId={surveyId} />
      </div>

      {failed ? (
        <div className="aftercare-error">
          <p role="alert">投放与答卷信息加载失败，请稍后重试。</p>
          <button type="button" onClick={() => void Promise.all([links.refetch(), summary.refetch()])}>
            <RefreshCw aria-hidden="true" />重新加载
          </button>
        </div>
      ) : null}
      {pending && !failed ? <p className="aftercare-empty">正在加载投放与答卷信息</p> : null}

      {!summaryForbidden && summary.data ? <ResponseSummaryStrip summary={summary.data} /> : null}
      {summaryForbidden ? <p className="aftercare-notice">你没有查看答卷摘要的权限。</p> : null}
      {linksForbidden ? (
        <p className="aftercare-notice">你可以查看答卷摘要，但没有管理投放链接的权限。</p>
      ) : null}

      {!linksForbidden && links.data ? (
        <>
          <form
            className="delivery-create"
            onSubmit={(event) => {
              event.preventDefault();
              if (label.trim()) createLink.mutate();
            }}
          >
            <label htmlFor="delivery-label">链接名称</label>
            <input
              id="delivery-label"
              maxLength={80}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
            />
            <button type="submit" disabled={!label.trim() || createLink.isPending}>
              <Plus aria-hidden="true" />创建链接
            </button>
          </form>
          {actionError ? <p className="aftercare-action-error" role="alert">{actionError}</p> : null}
          {links.data.length ? (
            <ul className="delivery-list" aria-label="投放链接">
              {links.data.map((link) => (
                <li key={link.id}>
                  <div className="delivery-list__details">
                    <div className="delivery-list__title">
                      <Link2 aria-hidden="true" />
                      <strong>{link.label}</strong>
                      {link.revokedAt ? <span>已撤销</span> : null}
                    </div>
                    <a href={link.shortUrl ?? link.url} target="_blank" rel="noreferrer">
                      {link.shortUrl ?? link.url}
                    </a>
                    {link.expiresAt ? <small>有效期至 {formatDateTime(link.expiresAt)}</small> : <small>长期有效</small>}
                  </div>
                  {!link.revokedAt ? <DeliveryQr link={link} load={qrLoader} /> : null}
                  <div className="delivery-list__actions">
                    {!link.revokedAt ? (
                      <>
                        <button
                          type="button"
                          aria-label={`复制${link.label}链接`}
                          onClick={() => void copyLink(link, setCopiedId, setActionError)}
                        >
                          <Clipboard aria-hidden="true" />{copiedId === link.id ? '已复制' : '复制'}
                        </button>
                        <button
                          type="button"
                          aria-label={`撤销${link.label}链接`}
                          disabled={revokeLink.isPending}
                          onClick={() => revokeLink.mutate(link.id)}
                        >
                          <Link2Off aria-hidden="true" />撤销
                        </button>
                      </>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          ) : <p className="aftercare-empty">尚未创建投放链接。</p>}
        </>
      ) : null}
    </section>
  );
}

function ResponseSummaryStrip({ summary }: { summary: ResponseSummary }) {
  return (
    <dl className="response-summary-strip">
      <div aria-label={`已完成 ${summary.total.engineCompleted}`}><dt>已完成</dt><dd>{summary.total.engineCompleted}</dd></div>
      <div aria-label={`填写中 ${summary.total.inProgress}`}><dt>填写中</dt><dd>{summary.total.inProgress}</dd></div>
      <div aria-label={`已删除 ${summary.total.deleted}`}><dt>已删除</dt><dd>{summary.total.deleted}</dd></div>
    </dl>
  );
}

function ResponsesLink({ surveyId }: { surveyId: string }) {
  const inRouter = useInRouterContext();
  const content = <>答卷与导出<ExternalLink aria-hidden="true" /></>;
  const href = `/surveys/${surveyId}/responses`;
  return inRouter
    ? <Link className="aftercare-primary-link" to={href}>{content}</Link>
    : <a className="aftercare-primary-link" href={href}>{content}</a>;
}

function DeliveryQr({ link, load }: {
  link: LinkView;
  load: (linkId: string, signal: AbortSignal) => Promise<Blob>;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    void load(link.id, controller.signal).then((blob) => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [link.id, load]);

  if (failed) return <span className="delivery-qr-error">二维码暂不可用</span>;
  if (!url) return <span className="delivery-qr-loading">正在加载二维码</span>;
  return <img className="delivery-qr" src={url} alt={`${link.label}二维码`} />;
}

async function copyLink(
  link: LinkView,
  setCopiedId: (id: string | null) => void,
  setError: (message: string | null) => void,
) {
  try {
    await navigator.clipboard.writeText(link.shortUrl ?? link.url);
    setCopiedId(link.id);
    setError(null);
  } catch {
    setError('复制失败，请打开链接后从浏览器地址栏复制。');
  }
}

function isForbidden(error: unknown) {
  return error instanceof ApiError && error.kind === 'forbidden';
}

function publicMessage(error: unknown) {
  return error instanceof ApiError ? error.message : '操作失败，请稍后重试';
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}
