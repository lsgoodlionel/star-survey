import { FolderKanban, X } from 'lucide-react';
import type { ApiClient } from '../../shared/api/http';
import type { ResourceView } from '../../shared/api/resources';
import { ResourceTree } from './ResourceTree';

interface ProjectNavProps {
  api: ApiClient;
  tenantId: string;
  projects: ResourceView[];
  projectId: string | null;
  selectedId: string | null;
  path: ResourceView[];
  open: boolean;
  loading: boolean;
  error: boolean;
  hasNextPage: boolean;
  loadingMore: boolean;
  onClose(): void;
  onRetry(): void;
  onLoadMore(): void;
  onSelect(resource: ResourceView): void;
}

export function ProjectNav({
  api,
  tenantId,
  projects,
  projectId,
  selectedId,
  path,
  open,
  loading,
  error,
  hasNextPage,
  loadingMore,
  onClose,
  onRetry,
  onLoadMore,
  onSelect,
}: ProjectNavProps) {
  return (
    <aside
      id="workspace-project-nav"
      className="workspace-project-nav"
      aria-label="项目与文件夹"
      data-open={open}
    >
      <div className="workspace-project-nav-heading">
        <h2>项目</h2>
        <button type="button" className="workspace-nav-close" aria-label="关闭项目导航" onClick={onClose}>
          <X aria-hidden="true" />
        </button>
      </div>
      {loading ? <p className="workspace-status" aria-live="polite">正在加载项目</p> : null}
      {error ? (
        <div className="workspace-status" role="alert">
          <span>项目加载失败</span>
          <button type="button" onClick={onRetry}>重试</button>
        </div>
      ) : null}
      {!loading && !error && projects.length === 0 ? (
        <p className="workspace-status">暂无项目</p>
      ) : null}
      <ul className="project-list">
        {projects.map((project) => (
          <li key={project.id}>
            <button
              type="button"
              className="project-nav-item"
              aria-current={project.id === projectId ? 'page' : undefined}
              title={project.name}
              onClick={() => onSelect(project)}
            >
              <FolderKanban aria-hidden="true" />
              <span>{project.name}</span>
            </button>
            {project.id === projectId ? (
              <ResourceTree
                api={api}
                tenantId={tenantId}
                projectId={project.id}
                path={path}
                selectedId={selectedId}
                onSelect={onSelect}
              />
            ) : null}
          </li>
        ))}
      </ul>
      {hasNextPage ? (
        <button
          type="button"
          className="workspace-nav-more"
          disabled={loadingMore}
          onClick={onLoadMore}
        >
          {loadingMore ? '正在加载' : '加载更多项目'}
        </button>
      ) : null}
    </aside>
  );
}
