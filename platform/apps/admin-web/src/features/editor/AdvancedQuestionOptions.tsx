import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import type { ApiClient } from '../../shared/api/http';
import { AssetPicker } from '../branding/AssetPicker';
import type { EditableQuestion, JsonObject } from './model/definition';

const dictionarySchema = z.array(z.object({ code: z.string(), name: z.string(), maxDepth: z.number(), currentVersion: z.string().nullable() }));

export function AdvancedQuestionOptions({ api, tenantId, question, disabled, onChange }: {
  api: ApiClient; tenantId: string; question: EditableQuestion; disabled: boolean; onChange(options: JsonObject): void;
}) {
  const options = question.themeOptions ?? {};
  const isDictionary = question.source.theme === 'mjy-cascading-select';
  const dictionaries = useQuery({ queryKey: ['dictionaries', tenantId], enabled: isDictionary, retry: false,
    queryFn: ({ signal }) => api.request({ path: '/v1/dictionaries', schema: dictionarySchema, signal }) });
  if (isDictionary) {
    const levels = Array.isArray(options.levels) && options.levels.every((item) => typeof item === 'string') ? options.levels as string[] : [];
    const selected = (dictionaries.data ?? []).find((item) => item.code === options.dictionary);
    const available = selected && selected.currentVersion;
    return <fieldset disabled={disabled}><legend>多级下拉</legend>
      <label>字典<select aria-label="字典" value={typeof options.dictionary === 'string' ? options.dictionary : ''}
        onChange={(event) => onChange({ ...options, dictionary: event.target.value })}>
        <option value="">请选择已发布字典</option>
        {dictionaries.isSuccess && typeof options.dictionary === 'string' && options.dictionary && !available
          ? <option value={options.dictionary}>当前字典不可用：{options.dictionary}</option> : null}
        {(dictionaries.data ?? []).filter((item) => item.currentVersion).map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}
      </select></label>
      {dictionaries.isPending ? <p role="status">正在加载字典</p> : null}
      {dictionaries.isError ? <p role="alert">字典加载失败。<button type="button" onClick={() => void dictionaries.refetch()}>重试</button></p> : null}
      {selected && levels.length > selected.maxDepth ? <p role="alert">级别数超过所选字典的最大深度，请移除多余级别。</p> : null}
      {levels.map((level, index) => <label key={index}>第 {index + 1} 级标题<input aria-label={`第 ${index + 1} 级标题`} value={level}
        onChange={(event) => onChange({ ...options, levels: levels.map((value, i) => i === index ? event.target.value : value) })} /></label>)}
      <button type="button" disabled={levels.length >= Math.min(8, selected?.maxDepth ?? 8)} onClick={() => onChange({ ...options, levels: [...levels, ''] })}>添加级别</button>
      <button type="button" disabled={levels.length <= 1} onClick={() => onChange({ ...options, levels: levels.slice(0, -1) })}>移除最后一级</button>
    </fieldset>;
  }
  const slides = Array.isArray(options.slides) ? options.slides.filter((item): item is JsonObject => Boolean(item && typeof item === 'object' && !Array.isArray(item))) : [];
  return <fieldset disabled={disabled}><legend>轮播图</legend>
    {(question.answers ?? []).filter((answer) => !slides.some((slide) => slide.code === answer.code)).map((answer, index) =>
      <button key={`${answer.code}-${index}`} type="button" onClick={() => onChange({ ...options, slides: [...slides, { code: answer.code, assetId: '', alt: '' }] })}>为选项 {answer.code} 添加图片</button>)}
    {slides.map((slide, index) => <div key={index}>
      <p>选项 {String(slide.code ?? '')}</p>
      {!(question.answers ?? []).some((answer) => answer.code === slide.code) ? <button type="button" onClick={() => onChange({ ...options, slides: slides.filter((_, i) => i !== index) })}>删除无对应选项的图片 {index + 1}</button> : null}
      <AssetPicker api={api} tenantId={tenantId} label={`图片 ${index + 1}`} assetId={typeof slide.assetId === 'string' ? slide.assetId : ''} disabled={disabled}
        onChange={(assetId) => onChange({ ...options, slides: slides.map((item, i) => i === index ? { ...item, assetId } : item) })} />
      <label>替代文本 {index + 1}<input value={typeof slide.alt === 'string' ? slide.alt : ''}
        onChange={(event) => onChange({ ...options, slides: slides.map((item, i) => i === index ? { ...item, alt: event.target.value } : item) })} /></label>
    </div>)}
    <label className="editor-checkbox-row"><input type="checkbox" checked={options.autoplay === true}
      onChange={(event) => onChange({ ...options, autoplay: event.target.checked })} />自动轮播</label>
  </fieldset>;
}
