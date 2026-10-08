import { useCallback, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { FolderPlus, FilePlus2, Plus } from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { createFolder, createProject, type ResourceView } from '../../shared/api/resources';
import { createSurvey } from '../../shared/api/surveys';
import { CreateResourceDialog, type CreateResourceKind } from './CreateResourceDialog';
import { ResourceTree, rootKey } from './ResourceTree';
import './workspace.css';

const projectCreators = new Set(['tenant_owner', 'org_admin']);
const editors = new Set(['tenant_owner', 'org_admin', 'project_manager', 'editor']);

export function WorkspacePage() {
  const { api, session } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get('resource');
  const [selectedResource, setSelectedResource] = useState<ResourceView | null>(null);
  const [dialog, setDialog] = useState<CreateResourceKind | null>(null);
  const [expandedIds, setExpandedIds] = useState(() => new Set<string>());
  const [createdResources, setCreatedResources] = useState<Record<string, ResourceView[]>>({});
  const roles = session?.me.roles ?? [];
  const canCreateProject = roles.some((role) => projectCreators.has(role));
  const canEdit = roles.some((role) => editors.has(role));
  const selectedContainer = selectedResource?.kind === 'project' || selectedResource?.kind === 'folder';

  const selectResource = useCallback(
    (resource: ResourceView) => {
      setSelectedResource(resource);
      setSearchParams({ resource: resource.id }, { replace: true });
    },
    [setSearchParams],
  );

  const createMutation = useMutation({
    mutationFn: async ({ kind, value }: { kind: CreateResourceKind; value: string }) => {
      if (kind === 'project') return { kind, resource: await createProject(api, value) };
      if (!selectedResource || selectedResource.kind === 'survey') throw new Error('请先选择项目或文件夹');
      if (kind === 'folder') {
        return { kind, resource: await createFolder(api, selectedResource.id, value) };
      }
      return { kind, survey: await createSurvey(api, selectedResource.id, value) };
    },
    onSuccess: (created) => {
      setDialog(null);
      if (created.kind === 'survey' && created.survey) {
        void navigate(`/surveys/${created.survey.id}/edit`);
        return;
      }
      if (!created.resource) return;
      const key = created.resource.parentId ?? rootKey;
      setCreatedResources((current) => ({
        ...current,
        [key]: [...(current[key] ?? []), created.resource!],
      }));
      if (created.resource.parentId) {
        setExpandedIds((current) => new Set(current).add(created.resource!.parentId!));
      }
      selectResource(created.resource);
    },
  });

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
      <aside className="workspace-sidebar" aria-label="工作区资源">
        <div className="workspace-sidebar-heading">
          <h1>问卷工作台</h1>
          <div className="workspace-actions">
            {canCreateProject ? (
              <button type="button" title="新建项目" aria-label="新建项目" onClick={() => setDialog('project')}>
                <Plus aria-hidden="true" />
              </button>
            ) : null}
            {canEdit && selectedContainer ? (
              <>
                <button
                  type="button"
                  title="新建文件夹"
                  aria-label="新建文件夹"
                  onClick={() => setDialog('folder')}
                >
                  <FolderPlus aria-hidden="true" />
                </button>
                <button
                  type="button"
                  title="新建问卷"
                  aria-label="新建问卷"
                  onClick={() => setDialog('survey')}
                >
                  <FilePlus2 aria-hidden="true" />
                </button>
              </>
            ) : null}
          </div>
        </div>
        <ResourceTree
          api={api}
          createdResources={createdResources}
          expandedIds={expandedIds}
          selectedId={selectedId}
          onExpandedChange={setExpanded}
          onResourceResolved={setSelectedResource}
          onSelect={selectResource}
        />
      </aside>
      <div className="workspace-main">
        {selectedResource ? (
          <>
            <p className="workspace-kind">{kindLabel(selectedResource.kind)}</p>
            <h2>{selectedResource.name}</h2>
          </>
        ) : (
          <div className="workspace-welcome">
            <h2>选择资源开始工作</h2>
            <p>从左侧打开项目、文件夹或问卷。</p>
          </div>
        )}
      </div>
      {dialog ? (
        <CreateResourceDialog
          kind={dialog}
          pending={createMutation.isPending}
          error={createMutation.error instanceof Error ? createMutation.error.message : undefined}
          onClose={() => setDialog(null)}
          onSubmit={(value) => createMutation.mutate({ kind: dialog, value })}
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
