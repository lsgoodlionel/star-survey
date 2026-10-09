import { useMemo, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Folder } from 'lucide-react';
import type { ApiClient } from '../../shared/api/http';
import { listResources, resourceQueryKey, type ResourceView } from '../../shared/api/resources';

interface ResourceTreeProps {
  api: ApiClient;
  tenantId: string;
  projectId: string;
  path: ResourceView[];
  selectedId: string | null;
  onSelect(resource: ResourceView): void;
}

export function ResourceTree({
  api,
  tenantId,
  projectId,
  path,
  selectedId,
  onSelect,
}: ResourceTreeProps) {
  const pathIds = useMemo(() => new Set(path.map((item) => item.id)), [path]);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());

  return (
    <ul className="resource-tree" aria-label="当前项目文件夹">
      <FolderBranch
        api={api}
        tenantId={tenantId}
        parentId={projectId}
        pathIds={pathIds}
        expandedIds={expandedIds}
        selectedId={selectedId}
        onExpandedChange={(id) => {
          setExpandedIds((current) => {
            const next = new Set(current);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
          });
        }}
        onSelect={onSelect}
      />
    </ul>
  );
}

interface FolderBranchProps {
  api: ApiClient;
  tenantId: string;
  parentId: string;
  pathIds: Set<string>;
  expandedIds: Set<string>;
  selectedId: string | null;
  onExpandedChange(id: string): void;
  onSelect(resource: ResourceView): void;
}

function FolderBranch(props: FolderBranchProps) {
  const query = useInfiniteQuery({
    queryKey: resourceQueryKey(props.tenantId, props.parentId, {
      kind: 'folder',
      archived: 'active',
      sort: 'name_asc',
    }),
    queryFn: ({ pageParam, signal }) =>
      listResources(props.api, {
        parentId: props.parentId,
        cursor: pageParam,
        kind: 'folder',
        archived: 'active',
        sort: 'name_asc',
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  });
  const folders = (query.data?.pages.flatMap((page) => page.items) ?? []).filter(
    (item) => item.kind === 'folder',
  );

  if (query.isPending) return <li className="resource-tree-status" aria-live="polite">正在加载文件夹</li>;
  if (query.isError) {
    return (
      <li className="resource-tree-status">
        <button type="button" onClick={() => void query.refetch()}>重试加载文件夹</button>
      </li>
    );
  }

  return (
    <>
      {folders.map((folder) => {
        const expanded = props.expandedIds.has(folder.id) || props.pathIds.has(folder.id);
        return (
          <li key={folder.id}>
            <div className="resource-tree-row">
              <button
                type="button"
                className="tree-icon-button"
                aria-label={`${expanded ? '收起' : '展开'} ${folder.name}`}
                aria-expanded={expanded}
                onClick={() => props.onExpandedChange(folder.id)}
              >
                {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
              </button>
              <button
                type="button"
                className="resource-tree-item"
                aria-current={props.selectedId === folder.id ? 'page' : undefined}
                title={folder.name}
                onClick={() => props.onSelect(folder)}
              >
                <Folder aria-hidden="true" />
                <span>{folder.name}</span>
              </button>
            </div>
            {expanded ? (
              <ul>
                <FolderBranch {...props} parentId={folder.id} />
              </ul>
            ) : null}
          </li>
        );
      })}
      {query.hasNextPage ? (
        <li className="resource-tree-more">
          <button
            type="button"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {query.isFetchingNextPage ? '正在加载' : '加载更多文件夹'}
          </button>
        </li>
      ) : null}
    </>
  );
}
