import { useEffect, useRef, useState, type FormEvent } from 'react';

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
  onClose(): void;
  onSubmit(value: string): void;
}

export function CreateResourceDialog({
  kind,
  pending,
  error,
  onClose,
  onSubmit,
}: CreateResourceDialogProps) {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const label = labels[kind];

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    const normalized = value.trim();
    if (normalized) onSubmit(normalized);
    else inputRef.current?.focus();
  }

  return (
    <div className="workspace-dialog-backdrop">
      <section role="dialog" aria-modal="true" aria-labelledby="create-resource-title" className="workspace-dialog">
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
      </section>
    </div>
  );
}
