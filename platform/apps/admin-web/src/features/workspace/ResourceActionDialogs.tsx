import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
} from 'react';
import type { ResourceView } from '../../shared/api/resources';

export type ResourceAction = 'rename' | 'move' | 'archive' | 'restore';

interface ResourceActionDialogsProps {
  action: ResourceAction;
  resource: ResourceView;
  destinations: ResourceView[];
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
  action,
  resource,
  destinations,
  pending,
  error,
  returnFocus,
  onClose,
  onSubmit,
}: ResourceActionDialogsProps) {
  const [value, setValue] = useState(action === 'rename' ? resource.name : destinations[0]?.id ?? '');
  const firstField = useRef<HTMLInputElement | HTMLSelectElement>(null);
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
                ref={firstField as RefObject<HTMLInputElement>}
                value={value}
                disabled={pending}
                onChange={(event) => setValue(event.target.value)}
              />
            </label>
          ) : null}
          {action === 'move' ? (
            <label>
              <span>目标位置</span>
              <select
                ref={firstField as RefObject<HTMLSelectElement>}
                value={value}
                disabled={pending}
                onChange={(event) => setValue(event.target.value)}
              >
                {destinations.map((destination) => (
                  <option key={destination.id} value={destination.id}>{destination.name}</option>
                ))}
              </select>
            </label>
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
            <button type="submit" disabled={pending || (action === 'move' && destinations.length === 0)}>
              {pending ? '正在处理' : text.submit}
            </button>
          </div>
        </form>
      </dialog>
    </div>
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
