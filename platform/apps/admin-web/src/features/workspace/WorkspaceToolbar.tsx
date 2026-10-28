import { ChevronDown, Plus, Search } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { ResourceFilters } from '../../shared/api/resources';
import type { CreateResourceKind } from './CreateResourceDialog';

interface WorkspaceToolbarProps {
  filters: ResourceFilters;
  canCreateProject: boolean;
  canCreateChildren: boolean;
  onFiltersChange(filters: ResourceFilters): void;
  onCreate(kind: CreateResourceKind, trigger: HTMLElement): void;
}

export function WorkspaceToolbar({
  filters,
  canCreateProject,
  canCreateChildren,
  onFiltersChange,
  onCreate,
}: WorkspaceToolbarProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const canCreate = canCreateProject || canCreateChildren;

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menuOpen]);

  return (
    <div className="workspace-toolbar" aria-label="资源工具栏">
      <label className="workspace-search">
        <span className="visually-hidden">搜索资源</span>
        <Search aria-hidden="true" />
        <input
          type="search"
          aria-label="搜索资源"
          placeholder="搜索资源"
          value={filters.query ?? ''}
          onChange={(event) => onFiltersChange({ ...filters, query: event.target.value || undefined })}
        />
      </label>
      <label>
        <span>类型</span>
        <select
          aria-label="资源类型"
          value={filters.kind ?? ''}
          onChange={(event) =>
            onFiltersChange({
              ...filters,
              kind: (event.target.value || undefined) as ResourceFilters['kind'],
            })
          }
        >
          <option value="">全部类型</option>
          <option value="project">项目</option>
          <option value="folder">文件夹</option>
          <option value="survey">问卷</option>
        </select>
      </label>
      <label>
        <span>状态</span>
        <select
          aria-label="资源状态"
          value={filters.archived ?? 'active'}
          onChange={(event) =>
            onFiltersChange({
              ...filters,
              archived: event.target.value as ResourceFilters['archived'],
            })
          }
        >
          <option value="active">使用中</option>
          <option value="archived">已归档</option>
        </select>
      </label>
      <label>
        <span>排序</span>
        <select
          aria-label="排序方式"
          value={filters.sort ?? 'updated_desc'}
          onChange={(event) =>
            onFiltersChange({ ...filters, sort: event.target.value as ResourceFilters['sort'] })
          }
        >
          <option value="updated_desc">最近更新</option>
          <option value="name_asc">名称升序</option>
        </select>
      </label>
      {canCreate ? (
        <div className="workspace-create-menu" ref={menuRef}>
          <button
            ref={createButtonRef}
            type="button"
            className="primary-action"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <Plus aria-hidden="true" />
            新建
            <ChevronDown aria-hidden="true" />
          </button>
          {menuOpen ? (
            <div className="workspace-create-menu-popover" role="menu">
              {canCreateProject ? (
                <button type="button" role="menuitem" onClick={() => {
                  setMenuOpen(false);
                  if (createButtonRef.current) onCreate('project', createButtonRef.current);
                }}>新建项目</button>
              ) : null}
              {canCreateChildren ? (
                <>
                  <button type="button" role="menuitem" onClick={() => {
                    setMenuOpen(false);
                    if (createButtonRef.current) onCreate('folder', createButtonRef.current);
                  }}>新建文件夹</button>
                  <button type="button" role="menuitem" onClick={() => {
                    setMenuOpen(false);
                    if (createButtonRef.current) onCreate('survey', createButtonRef.current);
                  }}>新建问卷</button>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
