import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { z } from 'zod';
import type { ApiClient } from '../../shared/api/http';
import { addPlatformTemplateVersion, createPlatformTemplate } from '../../shared/api/templates';

const definitionSchema = z.object({
  definitionVersion: z.union([z.literal(1), z.literal(2)]),
  title: z.string().min(1), language: z.string().min(1), groups: z.array(z.unknown()),
}).passthrough();

export function PlatformTemplateImport({ api, tenantId, templateId }: {
  api: ApiClient; tenantId: string; templateId?: string;
}) {
  const queryClient = useQueryClient();
  const lock = useRef(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: async (definition: unknown) => templateId
      ? addPlatformTemplateVersion(api, templateId, definition)
      : createPlatformTemplate(api, name.trim(), description.trim() || null, definition),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['templates', tenantId] });
    },
    onSettled: () => { lock.current = false; },
  });

  async function importFile() {
    if (lock.current) return;
    setValidation(null);
    mutation.reset();
    if (!templateId && (!name.trim() || name.trim().length > 200 || description.trim().length > 1000)) {
      setValidation('模板名称须为 1–200 字，说明不能超过 1000 字');
      return;
    }
    if (!file || !file.name.toLowerCase().endsWith('.json')) {
      setValidation('请选择本地 .json 定义文件');
      return;
    }
    let definition: unknown;
    try {
      definition = definitionSchema.parse(JSON.parse(await file.text()));
    } catch {
      setValidation('定义文件格式无效');
      return;
    }
    lock.current = true;
    mutation.mutate(definition);
  }

  return <section className="platform-template-import">
    <h2>{templateId ? '导入平台模板新版本' : '导入平台模板'}</h2>
    {!templateId ? <>
      <label>平台模板名称 <input aria-label="平台模板名称" value={name}
        onChange={(event) => setName(event.target.value)} /></label>
      <label>说明 <input aria-label="说明" value={description}
        onChange={(event) => setDescription(event.target.value)} /></label>
    </> : null}
    <label>定义 JSON 文件 <input aria-label="定义 JSON 文件" type="file" accept=".json,application/json"
      onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
    <button type="button" disabled={mutation.isPending} onClick={() => void importFile()}>
      {templateId ? '导入新版本' : '导入平台模板'}
    </button>
    {mutation.isPending ? <p role="status">正在导入</p> : null}
    {validation ? <p role="alert">{validation}</p> : null}
    {mutation.isError ? <p role="alert">导入失败，请核对定义后重试</p> : null}
    {mutation.data ? <p role="status">导入成功。<Link to={`/templates/${mutation.data.id}?mode=platform`}>查看平台模板</Link></p> : null}
  </section>;
}
