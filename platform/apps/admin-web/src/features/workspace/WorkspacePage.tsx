import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FolderPlus, FilePlus2, Pencil, Plus } from 'lucide-react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import {
  createFolder,
  createProject,
  getResourceCapabilities,
  getResourcePath,
  resourceQueryKey,
  type ResourceView,
} from '../../shared/api/resources';
import { createSurvey } from '../../shared/api/surveys';
import { CreateResourceDialog, type CreateResourceKind } from './CreateResourceDialog';
import { ResourceTree, rootKey } from './ResourceTree';
import './workspace.css';

export function WorkspacePage() {
  const { api, session } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get('resource');
  const tenantId = session?.me.tenantId ?? '';
  const [localSelection, setLocalSelection] = useState<{
    tenantId: string;
    resource: ResourceView;
  } | null>(null);
  const [dialogState, setDialogState] = useState<{
    tenantId: string;
    kind: CreateResourceKind;
    trigger: HTMLElement;
  } | null>(null);
  const [expandedIds, setExpandedIds] = useState(() => new Set<string>());
  const [createdResourcesByTenant, setCreatedResourcesByTenant] = useState<
    Record<string, Record<string, ResourceView[]>>
  >({});
  const createdResources = createdResourcesByTenant[tenantId] ?? {};
  const activeDialog = dialogState?.tenantId === tenantId ? dialogState : null;
  const currentTenantId = useRef(tenantId);
  useLayoutEffect(() => {
    currentTenantId.current = tenantId;
  }, [tenantId]);
  const pathQuery = useQuery({
    queryKey: ['resource-path', tenantId, selectedId],
    queryFn: ({ signal }) => getResourcePath(api, selectedId!, signal),
    enabled: Boolean(
      tenantId &&
        selectedId &&
        !(localSelection?.tenantId === tenantId && localSelection.resource.id === selectedId),
    ),
    retry: false,
  });
  const resolvedPath = useMemo(() => pathQuery.data ?? [], [pathQuery.data]);
  const resolvedResource = resolvedPath.at(-1) ?? null;
  const selectedResource =
    resolvedResource ??
    (localSelection?.tenantId === tenantId && localSelection.resource.id === selectedId
      ? localSelection.resource
      : null);
  const effectiveExpandedIds = useMemo(() => {
    const next = new Set(expandedIds);
    for (const resource of resolvedPath.slice(0, -1)) next.add(resource.id);
    return next;
  }, [expandedIds, resolvedPath]);
  const tenantCapabilities = useQuery({
    queryKey: ['resource-capabilities', tenantId, null],
    queryFn: ({ signal }) => getResourceCapabilities(api, undefined, signal),
    enabled: Boolean(tenantId),
    retry: false,
  });
  const selectedCapabilities = useQuery({
    queryKey: ['resource-capabilities', tenantId, selectedId],
    queryFn: ({ signal }) => getResourceCapabilities(api, selectedId!, signal),
    enabled: Boolean(tenantId && selectedId),
    retry: false,
  });
  const canCreateProject = tenantCapabilities.data?.canCreateProject === true;
  const canCreateChildren = selectedCapabilities.data?.canCreateChildren === true;
  const selectedContainer = selectedResource?.kind === 'project' || selectedResource?.kind === 'folder';

  const selectResource = useCallback(
    (resource: ResourceView) => {
      setLocalSelection({ tenantId, resource });
      setSearchParams({ resource: resource.id }, { replace: true });
    },
    [setSearchParams, tenantId],
  );

  const createMutation = useMutation({
    mutationFn: async ({
      kind,
      value,
      mutationTenantId,
    }: {
      kind: CreateResourceKind;
      value: string;
      mutationTenantId: string;
    }) => {
      if (kind === 'project') {
        return { kind, mutationTenantId, parentId: null, resource: await createProject(api, value) };
      }
      if (!selectedResource || selectedResource.kind === 'survey') throw new Error('请先选择项目或文件夹');
      const parentId = selectedResource.id;
      if (kind === 'folder') {
        return {
          kind,
          mutationTenantId,
          parentId,
          resource: await createFolder(api, parentId, value),
        };
      }
      return {
        kind,
        mutationTenantId,
        parentId,
        survey: await createSurvey(api, parentId, value),
      };
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({
        queryKey: resourceQueryKey(created.mutationTenantId, created.parentId),
        exact: true,
      });
      if (created.mutationTenantId !== currentTenantId.current) return;
      setDialogState((current) =>
        current?.tenantId === created.mutationTenantId ? null : current,
      );
      if (created.kind === 'survey' && created.survey) {
        void navigate(`/surveys/${created.survey.id}/edit`);
        return;
      }
      if (!created.resource) return;
      const key = created.resource.parentId ?? rootKey;
      setCreatedResourcesByTenant((current) => {
        const tenantResources = current[created.mutationTenantId] ?? {};
        return {
          ...current,
          [created.mutationTenantId]: {
            ...tenantResources,
            [key]: [...(tenantResources[key] ?? []), created.resource!],
          },
        };
      });
      if (created.resource.parentId) {
        setExpandedIds((current) => new Set(current).add(created.resource!.parentId!));
      }
      selectResource(created.resource);
    },
  });

  const mutationBelongsToTenant = createMutation.variables?.mutationTenantId === tenantId;

  function openDialog(kind: CreateResourceKind, trigger: HTMLElement) {
    createMutation.reset();
    setDialogState({ tenantId, kind, trigger });
  }

  function closeDialog() {
    createMutation.reset();
    setDialogState((current) => (current?.tenantId === tenantId ? null : current));
  }

  function setExpanded(id: string, expanded: boolean) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (expanded) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  return (
    <section className="workspace-page">
      <aside
        className="workspace-sidebar"
        aria-label="工作区资源"
        inert={activeDialog ? true : undefined}
      >
        <div className="workspace-sidebar-heading">
          <h1>问卷工作台</h1>
          <div className="workspace-actions">
            {canCreateProject ? (
              <button
                type="button"
                title="新建项目"
                aria-label="新建项目"
                onClick={(event) => openDialog('project', event.currentTarget)}
              >
                <Plus aria-hidden="true" />
              </button>
            ) : null}
            {canCreateChildren && selectedContainer ? (
              <>
                <button
                  type="button"
                  title="新建文件夹"
                  aria-label="新建文件夹"
                  onClick={(event) => openDialog('folder', event.currentTarget)}
                >
                  <FolderPlus aria-hidden="true" />
                </button>
                <button
                  type="button"
                  title="新建问卷"
                  aria-label="新建问卷"
                  onClick={(event) => openDialog('survey', event.currentTarget)}
                >
                  <FilePlus2 aria-hidden="true" />
                </button>
              </>
            ) : null}
          </div>
        </div>
        <ResourceTree
          api={api}
          tenantId={tenantId}
          createdResources={createdResources}
          pinnedResources={resolvedPath}
          expandedIds={effectiveExpandedIds}
          selectedId={selectedId}
          onExpandedChange={setExpanded}
          onSelect={selectResource}
        />
      </aside>
      <div className="workspace-main" inert={activeDialog ? true : undefined}>
        {pathQuery.isError ? (
          <p role="alert">
            {pathQuery.error instanceof Error ? pathQuery.error.message : '操作失败，请稍后重试'}
          </p>
        ) : selectedResource ? (
          <>
            <p className="workspace-kind">{kindLabel(selectedResource.kind)}</p>
            <h2>{selectedResource.name}</h2>
            {selectedResource.kind === 'survey' && selectedCapabilities.data?.canEdit ? (
              <Link className="workspace-edit-link" to={`/surveys/${selectedResource.id}/edit`}>
                <Pencil size={17} aria-hidden="true" />
                编辑问卷
              </Link>
            ) : null}
          </>
        ) : (
          <div className="workspace-welcome">
            <h2>选择资源开始工作</h2>
            <p>从左侧打开项目、文件夹或问卷。</p>
          </div>
        )}
      </div>
      {activeDialog ? (
        <CreateResourceDialog
          kind={activeDialog.kind}
          pending={mutationBelongsToTenant && createMutation.isPending}
          error={
            mutationBelongsToTenant && createMutation.error instanceof Error
              ? createMutation.error.message
              : undefined
          }
          returnFocus={activeDialog.trigger}
          onClose={closeDialog}
          onSubmit={(value) =>
            createMutation.mutate({ kind: activeDialog.kind, value, mutationTenantId: tenantId })
          }
        />
      ) : null}
    </section>
  );
}

function kindLabel(kind: ResourceView['kind']) {
  if (kind === 'project') return '项目';
  if (kind === 'folder') return '文件夹';
  return '问卷';
}
