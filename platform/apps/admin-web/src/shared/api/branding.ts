import { z } from 'zod';

const text = z.union([z.string(), z.record(z.string(), z.string())]);
const authorAsset = z.object({ assetId: z.string().uuid() }).strict();
export const brandingSchema = z.object({
  brandingVersion: z.literal(1),
  primaryColor: z.string().regex(/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/).optional(),
  pageTitle: text.optional(), footerText: text.optional(),
  logoAsset: authorAsset.optional(), faviconAsset: authorAsset.optional(), logoAlt: z.string().optional(),
  logoFile: z.string().optional(), faviconFile: z.string().optional(),
}).strict().refine((brand) => !(brand.logoAsset && brand.logoFile) && !(brand.faviconAsset && brand.faviconFile));
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
