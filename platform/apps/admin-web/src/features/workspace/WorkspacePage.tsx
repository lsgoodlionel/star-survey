import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, Menu, Move, Pencil, RotateCcw } from 'lucide-react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import {
  archiveResource,
  createFolder,
  createProject,
  getResourceCapabilities,
  getResourcePath,
  listResources,
  moveResource,
  renameResource,
  resourceQueryKey,
  restoreResource,
  type ResourceFilters,
  type ResourceView,
} from '../../shared/api/resources';
import { createSurvey } from '../../shared/api/surveys';
import { CreateResourceDialog, type CreateResourceKind } from './CreateResourceDialog';
import { ProjectNav } from './ProjectNav';
import { ResourceActionDialogs, type ResourceAction } from './ResourceActionDialogs';
import { ResourceList } from './ResourceList';
import { WorkspaceToolbar } from './WorkspaceToolbar';
import './workspace.css';

interface DialogState {
  tenantId: string;
  kind: 'create';
  createKind: CreateResourceKind;
  trigger: HTMLElement;
}

interface ActionDialogState {
  tenantId: string;
  kind: 'action';
  action: ResourceAction;
  resource: ResourceView;
  context: ActionContext;
  trigger: HTMLElement;
}

type WorkspaceDialogState = DialogState | ActionDialogState;

interface ActionContext {
  parentId: string | null;
  path: ResourceView[];
}

interface LocalSelection {
  tenantId: string;
  resource: ResourceView;
  resolvePath: boolean;
}

export function WorkspacePage() {
  const { api, session } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const tenantId = session?.me.tenantId ?? '';
  const projectId = searchParams.get('project');
  const parentId = searchParams.get('parent') ?? projectId;
  const resourceId = searchParams.get('resource');
  const filters = readFilters(searchParams);
  const [projectNavOpen, setProjectNavOpen] = useState(false);
  const [recentResources, setRecentResources] = useState<ResourceView[]>([]);
  const [localSelection, setLocalSelection] = useState<LocalSelection | null>(null);
  const [localPath, setLocalPath] = useState<ResourceView[]>([]);
  const [dialogState, setDialogState] = useState<WorkspaceDialogState | null>(null);
  const currentTenantId = useRef(tenantId);
  const previousTenantId = useRef(tenantId);

  useLayoutEffect(() => {
    currentTenantId.current = tenantId;
  }, [tenantId]);

  useEffect(() => {
    if (previousTenantId.current === tenantId) return;
    previousTenantId.current = tenantId;
    setRecentResources([]);
    setLocalSelection(null);
    setLocalPath([]);
    setDialogState(null);
    setProjectNavOpen(false);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete('project');
      next.delete('parent');
      next.delete('resource');
      return next;
    }, { replace: true });
  }, [setSearchParams, tenantId]);

  const projectsQuery = useInfiniteQuery({
    queryKey: resourceQueryKey(tenantId, null, {
      kind: 'project',
      archived: 'active',
      sort: 'name_asc',
    }),
    queryFn: ({ pageParam, signal }) =>
      listResources(api, {
        kind: 'project',
        archived: 'active',
        sort: 'name_asc',
        cursor: pageParam,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    enabled: Boolean(tenantId),
    retry: false,
  });
  const projects = projectsQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const selectedProject = projects.find((project) => project.id === projectId) ?? null;

  const selectedFromLocal =
    localSelection?.tenantId === tenantId && localSelection.resource.id === resourceId
      ? localSelection.resource
      : null;
  const resourcePathQuery = useQuery({
    queryKey: ['resource-path', tenantId, resourceId],
    queryFn: ({ signal }) => getResourcePath(api, resourceId!, signal),
    enabled: Boolean(tenantId && resourceId && (!selectedFromLocal || localSelection?.resolvePath)),
    retry: false,
  });
  const containerPathQuery = useQuery({
    queryKey: ['resource-path', tenantId, parentId],
    queryFn: ({ signal }) => getResourcePath(api, parentId!, signal),
    enabled: Boolean(tenantId && parentId && parentId !== projectId && !selectedFromLocal),
    retry: false,
  });

  useEffect(() => {
    const path = resourcePathQuery.data;
    if (!path?.length || currentTenantId.current !== tenantId || path.at(-1)?.id !== resourceId) return;
    const project = path.find((item) => item.kind === 'project');
    const resource = path.at(-1)!;
    const container =
      resource.kind === 'project'
        ? resource
        : resource.kind === 'folder'
          ? resource
          : path.at(-2) ?? project;
    if (!project || !container) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set('project', project.id);
      next.set('parent', container.id);
      next.set('resource', resource.id);
      return next;
    }, { replace: true });
  }, [resourceId, resourcePathQuery.data, setSearchParams, tenantId]);

  const localPathProjectId = localPath.find((item) => item.kind === 'project')?.id;
  let navigationPath = localPathProjectId === projectId ? localPath : [];
  if (!navigationPath.length && containerPathQuery.data?.length) {
    navigationPath = containerPathQuery.data;
  }
  if (!navigationPath.length && resourcePathQuery.data?.length) {
    const last = resourcePathQuery.data.at(-1);
    navigationPath = last?.kind === 'survey'
      ? resourcePathQuery.data.slice(0, -1)
      : resourcePathQuery.data;
  }
  if (!navigationPath.length && selectedProject) navigationPath = [selectedProject];
  const selectedResource =
    selectedFromLocal ??
    (resourcePathQuery.data?.at(-1)?.id === resourceId ? resourcePathQuery.data.at(-1)! : null);

  const currentQuery = useInfiniteQuery({
    queryKey: resourceQueryKey(tenantId, parentId ?? null, filters),
    queryFn: ({ pageParam, signal }) =>
      listResources(api, {
        parentId: parentId ?? null,
        ...filters,
        cursor: pageParam,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    enabled: Boolean(tenantId && (parentId || !projectId)),
    retry: false,
  });
  const currentResources = currentQuery.data?.pages.flatMap((page) => page.items) ?? [];

  const tenantCapabilities = useQuery({
    queryKey: ['resource-capabilities', tenantId, null],
    queryFn: ({ signal }) => getResourceCapabilities(api, undefined, signal),
    enabled: Boolean(tenantId),
    retry: false,
  });
  const containerCapabilities = useQuery({
    queryKey: ['resource-capabilities', tenantId, parentId],
    queryFn: ({ signal }) => getResourceCapabilities(api, parentId!, signal),
    enabled: Boolean(tenantId && parentId),
    retry: false,
  });
  const selectedCapabilities = useQuery({
    queryKey: ['resource-capabilities', tenantId, resourceId],
    queryFn: ({ signal }) => getResourceCapabilities(api, resourceId!, signal),
    enabled: Boolean(tenantId && resourceId && selectedResource),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: async ({ kind, name, mutationTenantId }: {
      kind: CreateResourceKind;
      name: string;
      mutationTenantId: string;
    }) => {
      if (kind === 'project') {
        return { kind, mutationTenantId, parentId: null, resource: await createProject(api, name) };
      }
      if (!parentId) throw new Error('请先选择项目或文件夹');
      if (kind === 'folder') {
        return { kind, mutationTenantId, parentId, resource: await createFolder(api, parentId, name) };
      }
      return { kind, mutationTenantId, parentId, survey: await createSurvey(api, parentId, name) };
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({
        queryKey: ['resources', created.mutationTenantId, created.parentId],
      });
      if (created.mutationTenantId !== currentTenantId.current) return;
      setDialogState(null);
      if (created.kind === 'survey' && created.survey) {
        void navigate(`/surveys/${created.survey.id}/edit`);
      }
    },
  });

  const actionMutation = useMutation({
    mutationFn: async ({ action, resource, value, mutationTenantId, context }: {
      action: ResourceAction;
      resource: ResourceView;
      value?: string;
      mutationTenantId: string;
      context: ActionContext;
    }) => {
      let updated: ResourceView;
      if (action === 'rename') updated = await renameResource(api, resource.id, value!);
      else if (action === 'move') updated = await moveResource(api, resource.id, value!);
      else if (action === 'archive') updated = await archiveResource(api, resource.id);
      else updated = await restoreResource(api, resource.id);
      return { action, resource, updated, value, mutationTenantId, context };
    },
    onSuccess: async (result) => {
      const invalidations = [
        queryClient.invalidateQueries({
          queryKey: ['resources', result.mutationTenantId, result.resource.parentId],
        }),
        queryClient.invalidateQueries({
          queryKey: ['resource-capabilities', result.mutationTenantId, result.resource.id],
        }),
      ];
      if (result.action === 'move' && result.value) {
        invalidations.push(
          queryClient.invalidateQueries({
            queryKey: ['resources', result.mutationTenantId, result.value],
          }),
        );
      }
      if (result.resource.kind === 'project') {
        invalidations.push(
          queryClient.invalidateQueries({ queryKey: ['resources', result.mutationTenantId, null] }),
        );
      }
      if (
        result.resource.kind === 'folder' &&
        (result.action === 'move' || result.action === 'archive')
      ) {
        invalidations.push(
          queryClient.invalidateQueries({
            queryKey: ['resources', result.mutationTenantId, result.resource.id],
          }),
        );
      }
      invalidations.push(
        queryClient.invalidateQueries({
          queryKey: ['resource-path', result.mutationTenantId],
        }),
      );
      await Promise.all(invalidations);
      if (result.mutationTenantId !== currentTenantId.current) return;
      setDialogState(null);
      if (result.action === 'rename') {
        setLocalPath((current) => current.map((resource) =>
          resource.id === result.updated.id ? result.updated : resource));
        setLocalSelection({
          tenantId: result.mutationTenantId,
          resource: result.updated,
          resolvePath: false,
        });
        return;
      }
      setRecentResources((current) =>
        current.filter((resource) => resource.id !== result.resource.id),
      );
      setLocalSelection(null);
      const exitsCurrentContainer =
        result.resource.id === result.context.parentId &&
        (result.action === 'move' || result.action === 'archive');
      if (exitsCurrentContainer) {
        const oldParentId = result.resource.parentId;
        const oldParentIndex = result.context.path.findIndex((item) => item.id === oldParentId);
        const parentPath = oldParentIndex >= 0
          ? result.context.path.slice(0, oldParentIndex + 1)
          : [];
        const parentProjectId = parentPath.find((item) => item.kind === 'project')?.id ?? null;
        setLocalPath(parentPath);
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          if (parentProjectId) next.set('project', parentProjectId);
          else next.delete('project');
          if (oldParentId) next.set('parent', oldParentId);
          else next.delete('parent');
          next.delete('resource');
          return next;
        }, { replace: true });
        return;
      }
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.delete('resource');
        return next;
      }, { replace: true });
    },
  });

  const activeDialog = dialogState?.tenantId === tenantId ? dialogState : null;
  const createPending =
    createMutation.isPending && createMutation.variables?.mutationTenantId === tenantId;
  const actionPending =
    actionMutation.isPending && actionMutation.variables?.mutationTenantId === tenantId;

  function updateUrl(changes: Record<string, string | null>, replace = false) {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    }, { replace });
  }

  function selectResource(resource: ResourceView) {
    setLocalSelection({ tenantId, resource, resolvePath: false });
    setProjectNavOpen(false);
    if (resource.kind === 'project') {
      setLocalPath([resource]);
      updateUrl({ project: resource.id, parent: resource.id, resource: resource.id });
      return;
    }
    if (resource.kind === 'folder') {
      const project = navigationPath.find((item) => item.kind === 'project') ?? selectedProject;
      const parentIndex = navigationPath.findIndex((item) => item.id === resource.parentId);
      const nextPath = parentIndex >= 0
        ? [...navigationPath.slice(0, parentIndex + 1), resource]
        : project ? [project, resource] : [resource];
      setLocalPath(nextPath);
      updateUrl({
        project: project?.id ?? projectId,
        parent: resource.id,
        resource: resource.id,
      });
      return;
    }
    setRecentResources((current) => [
      resource,
      ...current.filter((item) => item.id !== resource.id),
    ].slice(0, 5));
    updateUrl({ resource: resource.id });
  }

  function selectRecentResource(resource: ResourceView) {
    setLocalSelection({ tenantId, resource, resolvePath: true });
    setLocalPath([]);
    updateUrl({ resource: resource.id });
  }

  function updateFilters(nextFilters: ResourceFilters) {
    updateUrl({
      query: nextFilters.query?.trim() || null,
      kind: nextFilters.kind ?? null,
      archived: nextFilters.archived ?? 'active',
      sort: nextFilters.sort ?? 'updated_desc',
    });
  }

  function openCreate(kind: CreateResourceKind, trigger: HTMLElement) {
    createMutation.reset();
    setDialogState({ tenantId, kind: 'create', createKind: kind, trigger });
  }

  function openAction(action: ResourceAction, trigger: HTMLElement) {
    if (!selectedResource) return;
    actionMutation.reset();
    setDialogState({
      tenantId,
      kind: 'action',
      action,
      resource: selectedResource,
      context: { parentId, path: navigationPath },
      trigger,
    });
  }

  const selectedCaps = selectedCapabilities.data;
  const canRename = Boolean(selectedResource && selectedResource.kind !== 'survey' && selectedCaps?.canEdit);
  const canMove = Boolean(
    (selectedResource?.kind === 'folder' || selectedResource?.kind === 'survey') &&
    selectedCaps?.canEdit,
  );
  const canArchive = filters.archived !== 'archived' && selectedCaps?.canArchive === true;
  const canRestore = filters.archived === 'archived' && selectedCaps?.canRestore === true;

  return (
    <section className="workspace-page">
      <ProjectNav
        api={api}
        tenantId={tenantId}
        projects={projects}
        projectId={projectId}
        selectedId={parentId}
        path={navigationPath}
        open={projectNavOpen}
        loading={projectsQuery.isPending}
        error={projectsQuery.isError}
        hasNextPage={projectsQuery.hasNextPage}
        loadingMore={projectsQuery.isFetchingNextPage}
        onClose={() => setProjectNavOpen(false)}
        onRetry={() => void projectsQuery.refetch()}
        onLoadMore={() => void projectsQuery.fetchNextPage()}
        onSelect={selectResource}
      />
      <main className="workspace-main" inert={activeDialog ? true : undefined}>
        <header className="workspace-main-heading">
          <div>
            <h1>资源工作台</h1>
            <nav aria-label="当前位置" className="workspace-breadcrumb">
              <ol>
                {navigationPath.map((item, index) => (
                  <li key={item.id}>
                    {index > 0 ? <span aria-hidden="true">/</span> : null}
                    <button type="button" title={item.name} onClick={() => selectResource(item)}>
                      {item.name}
                    </button>
                  </li>
                ))}
                {navigationPath.length === 0 ? <li>全部项目</li> : null}
              </ol>
            </nav>
          </div>
          <button
            type="button"
            className="workspace-project-toggle"
            aria-label={projectNavOpen ? '关闭项目导航' : '打开项目导航'}
            aria-controls="workspace-project-nav"
            aria-expanded={projectNavOpen}
            onClick={() => setProjectNavOpen((open) => !open)}
          >
            <Menu aria-hidden="true" />
          </button>
        </header>
        <WorkspaceToolbar
          filters={filters}
          canCreateProject={tenantCapabilities.data?.canCreateProject === true}
          canCreateChildren={Boolean(parentId && containerCapabilities.data?.canCreateChildren)}
          onFiltersChange={updateFilters}
          onCreate={openCreate}
        />
        {selectedResource ? (
          <div className="workspace-selection-bar" aria-label="所选资源操作">
            <div>
              <strong title={selectedResource.name}>{selectedResource.name}</strong>
              <span>{selectedResource.kind === 'survey' ? '问卷' : selectedResource.kind === 'folder' ? '文件夹' : '项目'}</span>
            </div>
            <div className="workspace-selection-actions">
              {selectedResource.kind === 'survey' ? (
                <Link to={`/surveys/${selectedResource.id}/edit`}>
                  <Pencil aria-hidden="true" />
                  {selectedCaps?.canEdit ? '编辑问卷' : '查看问卷'}
                </Link>
              ) : null}
              {canRename ? (
                <button type="button" onClick={(event) => openAction('rename', event.currentTarget)}>
                  <Pencil aria-hidden="true" />重命名
                </button>
              ) : null}
              {canMove ? (
                <button type="button" onClick={(event) => openAction('move', event.currentTarget)}>
                  <Move aria-hidden="true" />移动
                </button>
              ) : null}
              {canArchive ? (
                <button type="button" onClick={(event) => openAction('archive', event.currentTarget)}>
                  <Archive aria-hidden="true" />归档
                </button>
              ) : null}
              {canRestore ? (
                <button type="button" onClick={(event) => openAction('restore', event.currentTarget)}>
                  <RotateCcw aria-hidden="true" />恢复
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
        <ResourceList
          resources={currentResources}
          selectedId={resourceId}
          loading={currentQuery.isPending}
          error={currentQuery.isError
            ? currentQuery.error instanceof Error
              ? currentQuery.error.message
              : '操作失败，请稍后重试'
            : undefined}
          hasNextPage={currentQuery.hasNextPage}
          loadingMore={currentQuery.isFetchingNextPage}
          onSelect={selectResource}
          onRetry={() => void currentQuery.refetch()}
          onLoadMore={() => void currentQuery.fetchNextPage()}
        />
        {recentResources.length ? (
          <section className="workspace-recent" aria-label="最近打开">
            <h2>最近打开</h2>
            <ul>
              {recentResources.map((resource) => (
                <li key={resource.id}>
                  <button type="button" title={resource.name} onClick={() => selectRecentResource(resource)}>
                    {resource.name}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </main>
      {activeDialog?.kind === 'create' ? (
        <CreateResourceDialog
          kind={activeDialog.createKind}
          pending={createPending}
          error={createPending || !(createMutation.error instanceof Error) ? undefined : createMutation.error.message}
          returnFocus={activeDialog.trigger}
          onClose={() => {
            createMutation.reset();
            setDialogState(null);
          }}
          onSubmit={(name) =>
            createMutation.mutate({
              kind: activeDialog.createKind,
              name,
              mutationTenantId: tenantId,
            })
          }
        />
      ) : null}
      {activeDialog?.kind === 'action' ? (
        <ResourceActionDialogs
          api={api}
          tenantId={tenantId}
          action={activeDialog.action}
          resource={activeDialog.resource}
          pending={actionPending}
          error={actionPending || !(actionMutation.error instanceof Error) ? undefined : actionMutation.error.message}
          returnFocus={activeDialog.trigger}
          onClose={() => {
            actionMutation.reset();
            setDialogState(null);
          }}
          onSubmit={(value) =>
            actionMutation.mutate({
              action: activeDialog.action,
              resource: activeDialog.resource,
              value,
              mutationTenantId: tenantId,
              context: activeDialog.context,
            })
          }
        />
      ) : null}
    </section>
  );
}

function readFilters(searchParams: URLSearchParams): ResourceFilters {
  const kind = searchParams.get('kind');
  const archived = searchParams.get('archived');
  const sort = searchParams.get('sort');
  return {
    query: searchParams.get('query') || undefined,
    kind: kind === 'project' || kind === 'folder' || kind === 'survey' ? kind : undefined,
    archived: archived === 'archived' ? 'archived' : 'active',
    sort: sort === 'name_asc' ? 'name_asc' : 'updated_desc',
  };
}
