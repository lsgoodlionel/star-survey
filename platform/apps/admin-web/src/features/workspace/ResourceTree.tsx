import { useRef, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Folder, FolderKanban, FileText } from 'lucide-react';
import type { ApiClient } from '../../shared/api/http';
import {
  listResources,
  resourceQueryKey,
  type ResourceKind,
  type ResourceView,
} from '../../shared/api/resources';

const rootKey = '__root__';

interface ResourceTreeProps {
  api: ApiClient;
  tenantId: string;
  createdResources: Record<string, ResourceView[]>;
  pinnedResources: ResourceView[];
  expandedIds: Set<string>;
  selectedId: string | null;
  onExpandedChange(id: string, expanded: boolean): void;
  onSelect(resource: ResourceView): void;
}

export function ResourceTree(props: ResourceTreeProps) {
  return (
    <ul aria-label="资源列表" className="resource-tree">
      <ResourceBranch {...props} parentId={null} enabled />
    </ul>
  );
}

interface ResourceBranchProps extends ResourceTreeProps {
  parentId: string | null;
  branchLabel?: string;
  enabled: boolean;
}

function ResourceBranch({
  api,
  tenantId,
  createdResources,
  pinnedResources,
  expandedIds,
  selectedId,
  onExpandedChange,
  onSelect,
  parentId,
  branchLabel,
  enabled,
}: ResourceBranchProps) {
  const loadingMoreRef = useRef(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const query = useInfiniteQuery({
    queryKey: resourceQueryKey(tenantId, parentId),
    queryFn: ({ pageParam, signal }) => listResources(api, { parentId, cursor: pageParam, signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    enabled,
  });
  const fetched = query.data?.pages.flatMap((page) => page.items) ?? [];
  const created = createdResources[parentId ?? rootKey] ?? [];
  const pinned = pinnedResources.filter((resource) => resource.parentId === parentId);
  const resources = [...fetched, ...created, ...pinned].filter(
    (resource, index, all) => all.findIndex((candidate) => candidate.id === resource.id) === index,
  );

  if (query.isError) {
    const message = query.error instanceof Error ? query.error.message : '操作失败，请稍后重试';
    return <li role="alert">{message}</li>;
  }
  if (query.isPending) return <li className="resource-tree-status">正在加载资源</li>;
  if (resources.length === 0 && parentId === null) {
    return <li className="resource-tree-status">暂无资源</li>;
  }

  return (
    <>
      {resources.map((resource) => {
        const expandable = resource.kind !== 'survey';
        const expanded = expandedIds.has(resource.id);
        return (
          <li key={resource.id} className="resource-tree-node">
            <div className="resource-tree-row">
              {expandable ? (
                <button
                  type="button"
                  className="tree-icon-button"
                  aria-label={`${expanded ? '收起' : '展开'} ${resource.name}`}
                  onClick={() => onExpandedChange(resource.id, !expanded)}
                >
                  {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
                </button>
              ) : (
                <span className="tree-icon-placeholder" aria-hidden="true" />
              )}
              <button
                type="button"
                aria-current={selectedId === resource.id ? 'true' : undefined}
                aria-expanded={expandable ? expanded : undefined}
                className="resource-tree-item"
                onClick={() => onSelect(resource)}
              >
                <ResourceIcon kind={resource.kind} />
                <span>{resource.name}</span>
              </button>
            </div>
            {expanded ? (
              <ul>
                <ResourceBranch
                  api={api}
                  tenantId={tenantId}
                  createdResources={createdResources}
                  pinnedResources={pinnedResources}
                  expandedIds={expandedIds}
                  selectedId={selectedId}
                  onExpandedChange={onExpandedChange}
                  onSelect={onSelect}
                  parentId={resource.id}
                  branchLabel={resource.name}
                  enabled
                />
              </ul>
            ) : null}
          </li>
        );
      })}
      {query.hasNextPage ? (
        <li className="resource-tree-more">
          <button
            type="button"
            onClick={() => {
              if (loadingMoreRef.current) return;
              loadingMoreRef.current = true;
              setLoadingMore(true);
              void query.fetchNextPage().finally(() => {
                loadingMoreRef.current = false;
                setLoadingMore(false);
              });
            }}
            disabled={loadingMore || query.isFetchingNextPage}
          >
            {loadingMore || query.isFetchingNextPage
              ? '正在加载'
              : `加载更多${branchLabel ? ` ${branchLabel}` : ''}`}
          </button>
        </li>
      ) : null}
    </>
  );
}

function ResourceIcon({ kind }: { kind: ResourceKind }) {
  if (kind === 'project') return <FolderKanban aria-hidden="true" />;
  if (kind === 'folder') return <Folder aria-hidden="true" />;
  return <FileText aria-hidden="true" />;
}

export { rootKey };
