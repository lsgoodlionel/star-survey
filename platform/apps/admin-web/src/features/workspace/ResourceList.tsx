import { FileText, Folder, FolderKanban } from 'lucide-react';
import type { ResourceView } from '../../shared/api/resources';

interface ResourceListProps {
  resources: ResourceView[];
  selectedId: string | null;
  loading: boolean;
  error?: string;
  hasNextPage: boolean;
  loadingMore: boolean;
  onSelect(resource: ResourceView): void;
  onRetry(): void;
  onLoadMore(): void;
}

export function ResourceList({
  resources,
  selectedId,
  loading,
  error,
  hasNextPage,
  loadingMore,
  onSelect,
  onRetry,
  onLoadMore,
}: ResourceListProps) {
  if (loading) return <p className="workspace-list-state" aria-live="polite">正在加载当前位置资源</p>;
  if (error) {
    return (
      <div className="workspace-list-state" role="alert">
        <p>{error}</p>
        <button type="button" onClick={onRetry}>重试</button>
      </div>
    );
  }
  if (resources.length === 0) return <p className="workspace-list-state">当前位置暂无资源</p>;

  return (
    <>
      <ul className="workspace-resource-list" aria-label="当前位置资源">
        {resources.map((resource) => (
          <li key={resource.id} aria-current={selectedId === resource.id ? 'true' : undefined}>
            <span className="resource-kind-icon">{kindIcon(resource.kind)}</span>
            <button
              type="button"
              className="resource-name"
              aria-current={selectedId === resource.id ? 'true' : undefined}
              title={resource.name}
              onClick={() => onSelect(resource)}
            >
              {resource.name}
            </button>
            <span className="resource-kind-label">{kindLabel(resource.kind)}</span>
            <time dateTime={resource.updatedAt}>{formatUpdatedAt(resource.updatedAt)}</time>
          </li>
        ))}
      </ul>
      {hasNextPage ? (
        <button
          type="button"
          className="workspace-load-more"
          disabled={loadingMore}
          onClick={onLoadMore}
        >
          {loadingMore ? '正在加载' : '加载更多'}
        </button>
      ) : null}
    </>
  );
}

function kindIcon(kind: ResourceView['kind']) {
  if (kind === 'project') return <FolderKanban aria-hidden="true" />;
  if (kind === 'folder') return <Folder aria-hidden="true" />;
  return <FileText aria-hidden="true" />;
}

function kindLabel(kind: ResourceView['kind']) {
  if (kind === 'project') return '项目';
  if (kind === 'folder') return '文件夹';
  return '问卷';
}

function formatUpdatedAt(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN');
}
