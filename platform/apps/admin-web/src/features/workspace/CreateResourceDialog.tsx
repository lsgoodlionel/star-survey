import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';

export type CreateResourceKind = 'project' | 'folder' | 'survey';

const labels: Record<CreateResourceKind, { heading: string; field: string; submit: string }> = {
  project: { heading: '新建项目', field: '名称', submit: '创建项目' },
  folder: { heading: '新建文件夹', field: '名称', submit: '创建文件夹' },
  survey: { heading: '新建问卷', field: '标题', submit: '创建问卷' },
};

interface CreateResourceDialogProps {
  kind: CreateResourceKind;
  pending: boolean;
  error?: string;
  returnFocus: HTMLElement | null;
  onClose(): void;
  onSubmit(value: string): void;
}

export function CreateResourceDialog({
  kind,
  pending,
  error,
  returnFocus,
  onClose,
  onSubmit,
}: CreateResourceDialogProps) {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const label = labels[kind];

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
    else dialog?.setAttribute('open', '');
    inputRef.current?.focus();
    return () => returnFocus?.focus();
  }, [returnFocus]);

  function handleKeyDown(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === 'Escape') {
      event.preventDefault();
      if (!pending) onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>('input:not(:disabled), button:not(:disabled)'),
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

  function submit(event: FormEvent) {
    event.preventDefault();
    const normalized = value.trim();
    if (normalized) onSubmit(normalized);
    else inputRef.current?.focus();
  }

  return (
    <div className="workspace-dialog-backdrop">
      <dialog
        ref={dialogRef}
        aria-labelledby="create-resource-title"
        className="workspace-dialog"
        onCancel={(event) => {
          event.preventDefault();
          if (!pending) onClose();
        }}
        onKeyDown={handleKeyDown}
      >
        <h2 id="create-resource-title">{label.heading}</h2>
        <form onSubmit={submit}>
          <label>
            <span>{label.field}</span>
            <input
              ref={inputRef}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              disabled={pending}
              required
            />
          </label>
          {error ? <p role="alert">{error}</p> : null}
          <div className="workspace-dialog-actions">
            <button type="button" className="secondary-action" onClick={onClose} disabled={pending}>
              取消
            </button>
            <button type="submit" disabled={pending}>
              {pending ? '正在创建' : label.submit}
            </button>
          </div>
        </form>
      </dialog>
    </div>
  );
}
