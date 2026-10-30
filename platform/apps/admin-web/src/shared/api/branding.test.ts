import { expect, test } from 'vitest';
import { mergeBrandingDraft, parseBrandingDraft, validateBrandingTranslations } from './branding';

const id = '10000000-0000-4000-8000-000000000001';

test('parses safe defaults from unknown and malformed drafts', () => {
  expect(parseBrandingDraft(null)).toEqual({ branding: null, additionalLanguages: [], translations: {} });
  expect(parseBrandingDraft({ branding: { customJs: 'alert(1)' }, additionalLanguages: [1], translations: [] }))
    .toEqual({ branding: null, additionalLanguages: [], translations: {} });
});

test('keeps valid brand text and author asset IDs without trusting materialized fields', () => {
  const state = parseBrandingDraft({ branding: { brandingVersion: 1, primaryColor: '#abc',
    pageTitle: { 'zh-Hans': '标题' }, logoAsset: { assetId: id },
    footerText: '页脚', logoAlt: '图示' }, additionalLanguages: ['en'],
    translations: { en: { title: 'Title' } } });
  expect(state.branding?.logoAsset).toEqual({ assetId: id });
  expect(state.translations.en).toEqual({ title: 'Title' });
  expect(parseBrandingDraft({ branding: { brandingVersion: 1, logoAsset: { assetId: id, url: 'https://host/a', assetVersion: 7 } } }).branding).toBeNull();
});

test('keeps cover background and localized watermark as closed brand fields', () => {
  const state = parseBrandingDraft({ branding: { brandingVersion: 1,
    coverAsset: { assetId: id }, backgroundAsset: { assetId: id },
    watermarkText: { 'zh-Hans': '内部资料', en: 'Internal' } } });
  expect(state.branding).toMatchObject({ coverAsset: { assetId: id },
    backgroundAsset: { assetId: id }, watermarkText: { 'zh-Hans': '内部资料', en: 'Internal' } });
});

test('merges only owned draft fields and never persists URL or assetVersion', () => {
  const original = { title: '问卷', groups: [{ uuid: id }], extension: { unknown: true },
    settings: { anonymized: 'Y' }, branding: { brandingVersion: 1, logoFile: 'old.png' } };
  const state = { branding: { brandingVersion: 1 as const, logoAsset: { assetId: id },
    primaryColor: '#123456' }, additionalLanguages: ['en'], translations: { en: { title: 'Title' } } };
  const merged = mergeBrandingDraft(original, state) as Record<string, unknown>;
  expect(merged).toMatchObject({ title: '问卷', groups: [{ uuid: id }], extension: { unknown: true },
    settings: { anonymized: 'Y' }, theme: 'zh-business', branding: state.branding,
    additionalLanguages: ['en'], translations: state.translations });
  expect(original.branding).toEqual({ brandingVersion: 1, logoFile: 'old.png' });
  expect(JSON.stringify(merged)).not.toMatch(/assetVersion|"url"/);
});
test('rejects translations that target deleted survey objects', () => {
  const definition = { language: 'zh-Hans', groups: [{ uuid: id, questions: [{
    uuid: '20000000-0000-4000-8000-000000000001', answers: [{ code: 'A1' }],
  }] }] };
  expect(validateBrandingTranslations(definition, ['en'], { en: {
    questions: { '30000000-0000-4000-8000-000000000001': { text: 'missing' } },
  } })).toContain('译文引用了不存在的题目');
  expect(validateBrandingTranslations(definition, ['en'], { en: {
    answers: [{ question: '20000000-0000-4000-8000-000000000001', code: 'A2', text: 'missing' }],
  } })).toContain('译文引用了不存在的选项');
});
