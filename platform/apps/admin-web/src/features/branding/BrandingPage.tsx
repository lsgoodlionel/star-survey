import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useBlocker, useParams } from 'react-router-dom';
import { z } from 'zod';
import { useSurveyPageReady, useSurveyShell } from '../../app/surveyShellContext';
import { useAuth } from '../auth/AuthProvider';
import { mergeBrandingDraft, parseBrandingDraft, validateBrandingTranslations,
  type BrandingFormState } from '../../shared/api/branding';
import { getResourceCapabilities } from '../../shared/api/resources';
import { surveyOverviewQueryKey } from '../../shared/api/approvals';
import { getSurveyDraft, saveDraft, surveyCapabilitiesQueryKey, surveyDetailQueryKey,
  surveyDraftQueryKey, type DraftView } from '../../shared/api/surveys';
import { AssetPicker } from './AssetPicker';
import { LanguageEditor } from './LanguageEditor';
import './branding.css';

interface BrandFields { primaryColor: string; pageTitle: string; footerText: string; logoAlt: string;
  decorativeLogo: boolean }
const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown, base: string) => typeof value === 'string' ? value
  : typeof object(value)[base] === 'string' ? object(value)[base] as string : '';
const color = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

export function BrandingPage() {
  const { api, session } = useAuth();
  const id = z.string().uuid().safeParse(useParams().surveyId);
  if (!session || !id.success) return <p role="alert">问卷标识无效</p>;
  return <LoadedBrandingPage key={`${session.me.tenantId}:${id.data}`} api={api} surveyId={id.data}
    tenantId={session.me.tenantId} />;
}

function LoadedBrandingPage({ api, surveyId, tenantId }: {
  api: ReturnType<typeof useAuth>['api']; surveyId: string; tenantId: string;
}) {
  const draft = useQuery({ queryKey: surveyDraftQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurveyDraft(api, surveyId, signal), retry: false });
  const capability = useQuery({ queryKey: surveyCapabilitiesQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getResourceCapabilities(api, surveyId, signal), retry: false });
  const ready = draft.isSuccess && capability.isSuccess && !draft.isFetching && !capability.isFetching;
  useSurveyPageReady('branding', ready);
  if (draft.isPending || capability.isPending) return <p role="status">正在加载品牌与语言</p>;
  if (draft.isError || capability.isError || !draft.data || !capability.data) {
    return <div role="alert">品牌草稿加载失败 <button type="button" onClick={() => {
      void draft.refetch(); void capability.refetch(); }}>重试</button></div>;
  }
  return <BrandingForm api={api} surveyId={surveyId} tenantId={tenantId}
    initialDraft={draft.data} canEdit={capability.data.canEdit} />;
}

function BrandingForm({ api, surveyId, tenantId, initialDraft, canEdit }: {
  api: ReturnType<typeof useAuth>['api']; surveyId: string; tenantId: string;
  initialDraft: DraftView; canEdit: boolean;
}) {
  const client = useQueryClient();
  const shell = useSurveyShell();
  const source = object(initialDraft.definition);
  const base = typeof source.language === 'string' ? source.language : 'zh-Hans';
  const initial = parseBrandingDraft(initialDraft.definition);
  const [draft, setDraft] = useState(initialDraft);
  const [state, setState] = useState<BrandingFormState>(initial);
  const [extraDirty, setExtraDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [saveLocked, setSaveLocked] = useState(false);
  const form = useForm<BrandFields>({ defaultValues: {
    primaryColor: initial.branding?.primaryColor ?? '',
    pageTitle: text(initial.branding?.pageTitle, base),
    footerText: text(initial.branding?.footerText, base),
    logoAlt: initial.branding?.logoAlt ?? '',
    decorativeLogo: Boolean(initial.branding?.logoAsset && initial.branding.logoAlt === ''),
  } });
  const selectedColor = useWatch({ control: form.control, name: 'primaryColor' });
  const dirty = form.formState.isDirty || extraDirty;
  const blocker = useBlocker(({ nextLocation }) => dirty && !nextLocation.pathname.startsWith('/login'));

  useEffect(() => { shell?.setUnsavedChanges(dirty);
    return () => shell?.setUnsavedChanges(false); }, [dirty, shell]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const save = useMutation({ mutationFn: (fields: BrandFields) => {
    const current = state.branding;
    const branding = { brandingVersion: 1 as const,
      ...(current?.logoFile ? { logoFile: current.logoFile } : {}),
      ...(current?.faviconFile ? { faviconFile: current.faviconFile } : {}),
      ...(fields.primaryColor.trim() ? { primaryColor: fields.primaryColor.trim() } : {}),
      ...(fields.pageTitle.trim() ? { pageTitle: fields.pageTitle.trim() === text(current?.pageTitle, base)
        ? current?.pageTitle ?? fields.pageTitle.trim() : fields.pageTitle.trim() } : {}),
      ...(fields.footerText.trim() ? { footerText: fields.footerText.trim() === text(current?.footerText, base)
        ? current?.footerText ?? fields.footerText.trim() : fields.footerText.trim() } : {}),
      ...(state.branding?.logoAsset ? { logoAsset: state.branding.logoAsset } : {}),
      ...(state.branding?.faviconAsset ? { faviconAsset: state.branding.faviconAsset } : {}),
      ...(state.branding?.logoAsset || fields.logoAlt.trim()
        ? { logoAlt: fields.decorativeLogo ? '' : fields.logoAlt.trim() } : {}),
    };
    return saveDraft(api, surveyId, draft.version, mergeBrandingDraft(draft.definition,
      { ...state, branding }));
  }, onSuccess: async (saved) => {
    setDraft(saved); setSavedVersion(saved.version); setError(null); setExtraDirty(false);
    form.reset(form.getValues());
    client.setQueryData(surveyDraftQueryKey(tenantId, surveyId), saved);
    await Promise.all([
      client.invalidateQueries({ queryKey: surveyDetailQueryKey(tenantId, surveyId) }),
      client.invalidateQueries({ queryKey: surveyOverviewQueryKey(tenantId, surveyId) }),
    ]);
  }, onSettled: () => { setSaveLocked(false); } });

  function submit(fields: BrandFields) {
    if (saveLocked || !canEdit) return;
    setSavedVersion(null); setError(null);
    if (fields.primaryColor && !color.test(fields.primaryColor)) { setError('主色须为 #rgb 或 #rrggbb'); return; }
    if (state.branding?.logoAsset && !fields.logoAlt.trim() && !fields.decorativeLogo) {
      setError('使用 Logo 时请填写替代文本'); return;
    }
    const translationError = validateBrandingTranslations(draft.definition, state.additionalLanguages,
      state.translations);
    if (translationError) { setError(translationError); return; }
    setSaveLocked(true); save.mutate(fields);
  }
  function assetChange(slot: 'logoAsset' | 'faviconAsset', assetId: string) {
    const brand = { brandingVersion: 1 as const, ...state.branding };
    if (assetId) brand[slot] = { assetId }; else delete brand[slot];
    if (slot === 'logoAsset') delete brand.logoFile;
    if (slot === 'faviconAsset') delete brand.faviconFile;
    setState({ ...state, branding: brand }); setExtraDirty(true);
  }
  return <main className="branding-page">
    <h1>品牌与语言</h1>
    {!canEdit ? <p role="status">您可查看设置，但无权修改此问卷。</p> : null}
    <form onSubmit={form.handleSubmit(submit)}>
      <section><h2>品牌外观</h2>
        <label>主色 <input disabled={!canEdit} aria-describedby="brand-color-help" {...form.register('primaryColor')} /></label>
        <p id="brand-color-help">格式为 #rgb 或 #rrggbb。</p>
        <input type="color" aria-label="主色选择器" disabled={!canEdit}
          value={color.test(selectedColor) && selectedColor.length === 7
            ? selectedColor : '#000000'}
          onChange={(event) => form.setValue('primaryColor', event.target.value, { shouldDirty: true })} />
        <label>页面标题 <input disabled={!canEdit} {...form.register('pageTitle')} /></label>
        <label>页脚文本 <textarea disabled={!canEdit} {...form.register('footerText')} /></label>
        <AssetPicker api={api} tenantId={tenantId} label="Logo" disabled={!canEdit}
          assetId={state.branding?.logoAsset?.assetId ?? ''} onChange={(id) => assetChange('logoAsset', id)} />
        <label>Logo 替代文本 <input disabled={!canEdit} {...form.register('logoAlt')} /></label>
        <label><input type="checkbox" disabled={!canEdit} {...form.register('decorativeLogo')} />Logo 仅作装饰</label>
        <AssetPicker api={api} tenantId={tenantId} label="网站图标" disabled={!canEdit}
          assetId={state.branding?.faviconAsset?.assetId ?? ''} onChange={(id) => assetChange('faviconAsset', id)} />
      </section>
      <LanguageEditor definition={draft.definition} languages={state.additionalLanguages}
        translations={state.translations} disabled={!canEdit} onChange={(languages, translations) => {
          setState({ ...state, additionalLanguages: languages, translations }); setExtraDirty(true);
        }} />
      {canEdit ? <button type="submit" disabled={save.isPending}>保存品牌与语言</button> : null}
      {save.isPending ? <p role="status">正在保存</p> : null}
      {savedVersion !== null ? <p role="status">已保存，草稿版本 {savedVersion}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {save.isError ? <p role="alert">{typeof save.error === 'object' && save.error && 'status' in save.error
        && save.error.status === 409 ? '草稿版本已变化，请重新加载后再保存' : '保存失败，请稍后重试'}</p> : null}
    </form>
    {blocker.state === 'blocked' ? <div role="dialog" aria-label="未保存修改">
      <p>当前品牌与语言有未保存修改，确定离开？</p>
      <button type="button" onClick={() => blocker.proceed()}>放弃修改并离开</button>
      <button type="button" onClick={() => blocker.reset()}>继续编辑</button>
    </div> : null}
  </main>;
}
