import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Folder, FolderKanban } from 'lucide-react';
import type { ApiClient } from '../../shared/api/http';
import {
  listResources,
  resourceQueryKey,
  type ResourceView,
} from '../../shared/api/resources';

export type ResourceAction = 'rename' | 'move' | 'archive' | 'restore';

interface ResourceActionDialogsProps {
  api: ApiClient;
  tenantId: string;
  action: ResourceAction;
  resource: ResourceView;
  pending: boolean;
  error?: string;
  returnFocus: HTMLElement | null;
  onClose(): void;
  onSubmit(value?: string): void;
}

const actionText = {
  rename: { title: '重命名资源', submit: '保存名称' },
  move: { title: '移动资源', submit: '确认移动' },
  archive: { title: '归档资源', submit: '确认归档' },
  restore: { title: '恢复资源', submit: '确认恢复' },
} satisfies Record<ResourceAction, { title: string; submit: string }>;

export function ResourceActionDialogs({
  api,
  tenantId,
  action,
  resource,
  pending,
  error,
  returnFocus,
  onClose,
  onSubmit,
}: ResourceActionDialogsProps) {
  const [value, setValue] = useState(action === 'rename' ? resource.name : '');
  const firstField = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const text = actionText[action];

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
    else dialog?.setAttribute('open', '');
    (firstField.current ?? dialog?.querySelector<HTMLElement>('button:not(:disabled)'))?.focus();
    return () => returnFocus?.focus();
  }, [returnFocus]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if ((action === 'rename' || action === 'move') && !value.trim()) {
      firstField.current?.focus();
      return;
    }
    onSubmit(action === 'rename' ? value.trim() : action === 'move' ? value : undefined);
  }

  return (
    <div className="workspace-dialog-backdrop">
      <dialog
        ref={dialogRef}
        className="workspace-dialog"
        aria-labelledby="resource-action-title"
        onCancel={(event) => {
          event.preventDefault();
          if (!pending) onClose();
        }}
        onKeyDown={trapDialogFocus}
      >
        <h2 id="resource-action-title">{text.title}</h2>
        <form onSubmit={submit}>
          {action === 'rename' ? (
            <label>
              <span>名称</span>
              <input
                ref={firstField}
                value={value}
                disabled={pending}
                onChange={(event) => setValue(event.target.value)}
              />
            </label>
          ) : null}
          {action === 'move' ? (
            <MoveDestinationPicker
              api={api}
              tenantId={tenantId}
              excludedId={resource.id}
              value={value}
              disabled={pending}
              onChange={setValue}
            />
          ) : null}
          {action === 'archive' ? (
            <>
              <p>归档后，该资源及其下级内容将从使用中视图隐藏。</p>
              {resource.kind === 'survey' ? (
                <p className="workspace-dialog-warning">归档不会关闭已发布的公开答卷链接。</p>
              ) : null}
            </>
          ) : null}
          {action === 'restore' ? <p>恢复后，该资源将重新出现在使用中视图。</p> : null}
          {error ? <p role="alert">{retryableMessage(error)}</p> : null}
          <div className="workspace-dialog-actions">
            <button type="button" className="secondary-action" disabled={pending} onClick={onClose}>
              取消
            </button>
            <button type="submit" disabled={pending || (action === 'move' && !value)}>
              {pending ? '正在处理' : text.submit}
            </button>
          </div>
        </form>
      </dialog>
    </div>
  );
}

interface MoveDestinationPickerProps {
  api: ApiClient;
  tenantId: string;
  excludedId: string;
  value: string;
  disabled: boolean;
  onChange(value: string): void;
}

function MoveDestinationPicker(props: MoveDestinationPickerProps) {
  const query = useInfiniteQuery({
    queryKey: resourceQueryKey(props.tenantId, null, {
      kind: 'project',
      archived: 'active',
      sort: 'name_asc',
    }),
    queryFn: ({ pageParam, signal }) =>
      listResources(props.api, {
        kind: 'project',
        archived: 'active',
        sort: 'name_asc',
        cursor: pageParam,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  });
  const projects = query.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <fieldset className="move-destination-picker" disabled={props.disabled}>
      <legend>目标位置</legend>
      {query.isPending ? <p aria-live="polite">正在加载目标位置</p> : null}
      {query.isError ? (
        <div role="alert">
          <span>目标位置加载失败</span>
          <button type="button" onClick={() => void query.refetch()}>重试</button>
        </div>
      ) : null}
      <ul>
        {projects.map((project) => (
          <MoveDestinationNode key={project.id} resource={project} {...props} />
        ))}
      </ul>
      {query.hasNextPage ? (
        <button
          type="button"
          disabled={props.disabled || query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {query.isFetchingNextPage ? '正在加载' : '加载更多目标'}
        </button>
      ) : null}
    </fieldset>
  );
}

interface MoveDestinationNodeProps extends MoveDestinationPickerProps {
  resource: ResourceView;
}

function MoveDestinationNode({ resource, ...props }: MoveDestinationNodeProps) {
  const [expanded, setExpanded] = useState(false);
  const query = useInfiniteQuery({
    queryKey: resourceQueryKey(props.tenantId, resource.id, {
      kind: 'folder',
      archived: 'active',
      sort: 'name_asc',
    }),
    queryFn: ({ pageParam, signal }) =>
      listResources(props.api, {
        parentId: resource.id,
        kind: 'folder',
        archived: 'active',
        sort: 'name_asc',
        cursor: pageParam,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    enabled: expanded,
  });
  const folders = useMemo(
    () => (query.data?.pages.flatMap((page) => page.items) ?? [])
      .filter((item) => item.kind === 'folder' && item.id !== props.excludedId),
    [props.excludedId, query.data],
  );

  if (resource.id === props.excludedId) return null;

  return (
    <li>
      <div className="move-destination-row">
        <button
          type="button"
          aria-label={`${expanded ? '收起' : '展开'} ${resource.name}`}
          aria-expanded={expanded}
          disabled={props.disabled}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
        </button>
        <label title={resource.name}>
          <input
            type="radio"
            name="move-destination"
            value={resource.id}
            checked={props.value === resource.id}
            onChange={() => props.onChange(resource.id)}
          />
          {resource.kind === 'project'
            ? <FolderKanban aria-hidden="true" />
            : <Folder aria-hidden="true" />}
          <span>{resource.name}</span>
        </label>
      </div>
      {expanded ? (
        <div className="move-destination-children">
          {query.isPending ? <p aria-live="polite">正在加载文件夹</p> : null}
          {query.isError ? (
            <button type="button" onClick={() => void query.refetch()}>重试加载文件夹</button>
          ) : null}
          <ul>
            {folders.map((folder) => (
              <MoveDestinationNode key={folder.id} resource={folder} {...props} />
            ))}
          </ul>
          {query.hasNextPage ? (
            <button
              type="button"
              disabled={props.disabled || query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            >
              {query.isFetchingNextPage ? '正在加载' : '加载更多目标'}
            </button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function retryableMessage(message: string) {
  if (message.includes('请稍后重试')) return message.replace('请稍后重试', '请重试');
  return `${message}，请重试`;
}

function trapDialogFocus(event: KeyboardEvent<HTMLDialogElement>) {
  if (event.key !== 'Tab') return;
  const focusable = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>('input:not(:disabled), select:not(:disabled), button:not(:disabled)'),
  );
  if (focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable.at(-1)!;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
