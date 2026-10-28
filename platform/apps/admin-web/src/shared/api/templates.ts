import { z } from 'zod';
import type { ApiClient } from './http';
import { surveyViewSchema } from './surveys';

const uuid = z.string().uuid();
const timestamp = z.string().datetime({ offset: true });
const version = z.number().int().positive();
export const templateScopeSchema = z.enum(['tenant', 'platform']);
export const templateStatusSchema = z.enum(['draft', 'pending', 'published', 'rejected', 'unpublished']);

export const templateViewSchema = z.object({
  id: uuid, scope: templateScopeSchema, name: z.string(), description: z.string().nullable(),
  status: templateStatusSchema, currentVersion: version, publishedVersion: version.nullable(),
  createdBy: z.string(), reviewedBy: z.string().nullable(), reviewedAt: timestamp.nullable(),
  reviewReason: z.string().nullable(), updatedAt: timestamp,
});
export const templateVersionSchema = z.object({
  versionNo: version, sourceSurveyId: uuid.nullable(), sourceDraftVersion: version.nullable(),
  createdBy: z.string(), createdAt: timestamp,
});
export const templateEventSchema = z.object({
  event: z.string(), actor: z.string(), versionNo: version.nullable(), reason: z.string().nullable(),
  copiedSurveyId: uuid.nullable(), occurredAt: timestamp,
});
export const templateDetailSchema = z.object({
  template: templateViewSchema, versions: z.array(templateVersionSchema), history: z.array(templateEventSchema),
});
export type TemplateView = z.infer<typeof templateViewSchema>;
export type TemplateDetail = z.infer<typeof templateDetailSchema>;
export type TemplateScopeFilter = z.infer<typeof templateScopeSchema> | 'all';
const templateListSchema = z.array(templateViewSchema);
const platformTemplateViewSchema = templateViewSchema.refine((item) => item.scope === 'platform');
const platformTemplateListSchema = z.array(platformTemplateViewSchema);

export const templateListQueryKey = (tenantId: string, scope: TemplateScopeFilter = 'all') =>
  ['templates', tenantId, 'list', scope] as const;
export const templateDetailQueryKey = (tenantId: string, id: string) =>
  ['templates', tenantId, 'detail', id] as const;
export const templatePendingQueryKey = (tenantId: string) =>
  ['templates', tenantId, 'pending'] as const;
export const platformTemplateListQueryKey = (tenantId: string) =>
  ['templates', tenantId, 'platform-list'] as const;
export const templateManagedQueryKey = (tenantId: string) =>
  ['templates', tenantId, 'managed'] as const;
export const templateCapabilitiesQueryKey = (tenantId: string, id: string) =>
  ['templates', tenantId, 'capabilities', id] as const;
export const platformTemplateDetailQueryKey = (tenantId: string, id: string) =>
  ['templates', tenantId, 'platform-detail', id] as const;
export const templateCapabilitiesSchema = z.object({
  canAddVersion: z.boolean(), canSubmit: z.boolean(), canReview: z.boolean(), canUnpublish: z.boolean(),
});
export type TemplateCapabilities = z.infer<typeof templateCapabilitiesSchema>;

const tenantPath = (id: string) => `/v1/survey-templates/${encodeURIComponent(id)}`;
const platformPath = (id: string) => `/v1/platform/survey-templates/${encodeURIComponent(id)}`;

export function listTemplates(api: ApiClient, scope: TemplateScopeFilter = 'all', signal?: AbortSignal) {
  return api.request({ path: `/v1/survey-templates?scope=${encodeURIComponent(scope)}`, schema: templateListSchema, signal });
}
export function listPendingTemplates(api: ApiClient, signal?: AbortSignal) {
  return api.request({ path: '/v1/survey-templates/pending', schema: templateListSchema, signal });
}
export function listManagedTemplates(api: ApiClient, signal?: AbortSignal) {
  return api.request({ path: '/v1/survey-templates/managed', schema: templateListSchema, signal });
}
export function getTemplateCapabilities(api: ApiClient, id: string, signal?: AbortSignal) {
  return api.request({ path: `${tenantPath(id)}/capabilities`, schema: templateCapabilitiesSchema, signal });
}
export function getTemplate(api: ApiClient, id: string, signal?: AbortSignal) {
  return api.request({ path: tenantPath(id), schema: templateDetailSchema, signal });
}
export function getTemplateVersion(api: ApiClient, id: string, versionNo: number, signal?: AbortSignal) {
  return api.request({ path: `${tenantPath(id)}/versions/${encodeURIComponent(String(versionNo))}`, schema: z.unknown(), signal });
}
export function createTenantTemplate(api: ApiClient, sourceSurveyId: string, name: string, description: string | null = null) {
  return api.request({ path: '/v1/survey-templates', method: 'POST', body: { sourceSurveyId, name, description }, schema: templateViewSchema });
}
export function addTenantTemplateVersion(api: ApiClient, id: string, sourceSurveyId: string) {
  return api.request({ path: `${tenantPath(id)}/versions`, method: 'POST', body: { sourceSurveyId }, schema: templateViewSchema });
}
export function submitTemplate(api: ApiClient, id: string) {
  return api.request({ path: `${tenantPath(id)}/submit`, method: 'POST', schema: templateViewSchema });
}
export function approveTemplate(api: ApiClient, id: string) {
  return api.request({ path: `${tenantPath(id)}/approve`, method: 'POST', schema: templateViewSchema });
}
export function rejectTemplate(api: ApiClient, id: string, reason: string) {
  return api.request({ path: `${tenantPath(id)}/reject`, method: 'POST', body: { reason }, schema: templateViewSchema });
}
export function unpublishTemplate(api: ApiClient, id: string, reason?: string) {
  return api.request({ path: `${tenantPath(id)}/unpublish`, method: 'POST', body: reason === undefined ? undefined : { reason }, schema: templateViewSchema });
}
export function copyTemplate(api: ApiClient, id: string, parentId: string, title?: string) {
  return api.request({ path: `${tenantPath(id)}/copies`, method: 'POST', body: { parentId, title }, schema: surveyViewSchema });
}
export function listPlatformTemplates(api: ApiClient, signal?: AbortSignal) {
  return api.request({ path: '/v1/platform/survey-templates', schema: platformTemplateListSchema, signal });
}
export function getPlatformTemplate(api: ApiClient, id: string, signal?: AbortSignal) {
  return api.request({ path: platformPath(id), schema: templateDetailSchema.refine((item) => item.template.scope === 'platform'), signal });
}
export function createPlatformTemplate(api: ApiClient, name: string, description: string | null, definition: unknown) {
  return api.request({ path: '/v1/platform/survey-templates', method: 'POST', body: { name, description, definition }, schema: platformTemplateViewSchema });
}
export function addPlatformTemplateVersion(api: ApiClient, id: string, definition: unknown) {
  return api.request({ path: `${platformPath(id)}/versions`, method: 'POST', body: { definition }, schema: platformTemplateViewSchema });
}
export function publishPlatformTemplate(api: ApiClient, id: string, versionNo: number) {
  return api.request({ path: `${platformPath(id)}/publish`, method: 'POST', body: { versionNo }, schema: platformTemplateViewSchema });
}
export function unpublishPlatformTemplate(api: ApiClient, id: string) {
  return api.request({ path: `${platformPath(id)}/unpublish`, method: 'POST', schema: platformTemplateViewSchema });
}
