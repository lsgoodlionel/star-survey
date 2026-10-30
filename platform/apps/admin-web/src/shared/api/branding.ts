import { z } from 'zod';

const text = z.union([z.string(), z.record(z.string(), z.string())]);
const authorAsset = z.object({ assetId: z.string().uuid() }).strict();
export const brandingSchema = z.object({
  brandingVersion: z.literal(1),
  primaryColor: z.string().regex(/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/).optional(),
  pageTitle: text.optional(), footerText: text.optional(),
  logoAsset: authorAsset.optional(), faviconAsset: authorAsset.optional(),
  coverAsset: authorAsset.optional(), backgroundAsset: authorAsset.optional(),
  logoAlt: z.string().optional(), watermarkText: text.optional(),
  logoFile: z.string().optional(), faviconFile: z.string().optional(),
  coverFile: z.string().optional(), backgroundFile: z.string().optional(),
}).strict().refine((brand) => !(brand.logoAsset && brand.logoFile)
  && !(brand.faviconAsset && brand.faviconFile)
  && !(brand.coverAsset && brand.coverFile)
  && !(brand.backgroundAsset && brand.backgroundFile));
export type Branding = z.infer<typeof brandingSchema>;
export interface BrandingFormState {
  branding: Branding | null;
  additionalLanguages: string[];
  translations: Record<string, unknown>;
}

const languagesSchema = z.array(z.string());
const translationsSchema = z.record(z.string(), z.unknown());
const objectSchema = z.record(z.string(), z.unknown());

export function parseBrandingDraft(definition: unknown): BrandingFormState {
  const object = objectSchema.safeParse(definition);
  if (!object.success) return { branding: null, additionalLanguages: [], translations: {} };
  const branding = brandingSchema.safeParse(object.data.branding);
  const additionalLanguages = languagesSchema.safeParse(object.data.additionalLanguages);
  const translations = translationsSchema.safeParse(object.data.translations);
  return {
    branding: branding.success ? branding.data : null,
    additionalLanguages: additionalLanguages.success ? additionalLanguages.data : [],
    translations: translations.success ? translations.data : {},
  };
}

export function mergeBrandingDraft(definition: unknown, state: BrandingFormState): unknown {
  const source = objectSchema.safeParse(definition);
  if (!source.success) throw new Error('问卷草稿定义无效');
  const branding = state.branding === null ? null : brandingSchema.parse(state.branding);
  const additionalLanguages = languagesSchema.parse(state.additionalLanguages);
  const translations = translationsSchema.parse(state.translations);
  const merged: Record<string, unknown> = { ...source.data,
    additionalLanguages, translations };
  if (branding) {
    merged.branding = branding;
    merged.theme = 'zh-business';
  } else {
    delete merged.branding;
  }
  return merged;
}

/** 校验译文只引用当前草稿中仍存在的对象，避免保存后在发布网关才发现失效目标。 */
export function validateBrandingTranslations(
  definition: unknown, languages: string[], translations: Record<string, unknown>,
): string | null {
  const source = objectSchema.safeParse(definition);
  if (!source.success) return '问卷草稿定义无效';
  const base = source.data.language;
  if (languages.includes(String(base)) || new Set(languages).size !== languages.length) {
    return '附加语言不能包含基础语言或重复语言';
  }
  const record = (value: unknown): Record<string, unknown> =>
    value && typeof value === 'object' && !Array.isArray(value)
      ? value as Record<string, unknown> : {};
  const list = (value: unknown): Record<string, unknown>[] =>
    Array.isArray(value) ? value.map(record) : [];
  const groups = list(source.data.groups);
  const questions = groups.flatMap((group) => list(group.questions));
  const ids = {
    groups: new Set(groups.map((item) => item.uuid)),
    questions: new Set(questions.map((item) => item.uuid)),
    subquestions: new Set(questions.flatMap((item) => list(item.subquestions).map((sub) => sub.uuid))),
  };
  for (const [language, raw] of Object.entries(translations)) {
    if (!languages.includes(language)) return '译文语言必须在附加语言列表中';
    const value = record(raw);
    for (const section of ['groups', 'questions', 'subquestions'] as const) {
      if (Object.keys(record(value[section])).some((id) => !ids[section].has(id))) {
        return `译文引用了不存在的${section === 'groups' ? '题组' : section === 'questions' ? '题目' : '子题'}`;
      }
    }
    for (const item of list(value.answers)) {
      const question = questions.find((candidate) => candidate.uuid === item.question);
      if (!question || !list(question.answers).some((answer) => answer.code === item.code
        && (answer.scale ?? 0) === (item.scale ?? 0))) return '译文引用了不存在的选项';
    }
  }
  return null;
}
