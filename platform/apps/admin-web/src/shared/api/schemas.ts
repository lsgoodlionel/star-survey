import { z } from 'zod';

export const meSchema = z.object({
  tenantId: z.string().min(1),
  actorId: z.string().min(1),
  roles: z.array(z.string()),
});

export const tokenViewSchema = z.object({
  accessToken: z.string().min(1),
  tokenType: z.literal('Bearer'),
  expiresIn: z.number().positive(),
  principalId: z.string().min(1),
  tenantId: z.string().min(1),
});

export const dashboardSectionSchema = z.enum([
  'approval',
  'publish',
  'preview',
  'export',
  'survey',
]);

export const dashboardTaskKindSchema = z.enum([
  'pending_approval',
  'publish_exception',
  'preview_exception',
  'export_exception',
  'draft_pending_publish',
]);

export const dashboardPublishStateSchema = z.enum([
  'draft',
  'pending_approval',
  'approved',
  'publishing',
  'published',
  'failed',
  'needs_reconciliation',
]);

export const dashboardActionSchema = z.enum(['edit', 'preview', 'publish', 'responses']);
export const recentWorkPageSchema = z.enum([
  'edit',
  'import',
  'preview',
  'publish',
  'responses',
  'version',
]);

const surveyIdSchema = z.string().uuid();
const surveyTargetPathSchema = z.string()
  .regex(
    /^\/surveys\/[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\/(?:edit|import|preview|publish|responses|versions\/[1-9][0-9]*)$/,
    'targetPath must be an existing survey-relative route',
  )
  .refine((targetPath) => {
    const version = targetPath.match(/\/versions\/([1-9][0-9]*)$/)?.[1];
    return version === undefined || Number.isSafeInteger(Number(version));
  }, 'targetPath version must be a safe positive integer');

function requireMatchingSurveyPath(
  value: { surveyId: string; targetPath: string },
  context: z.RefinementCtx,
) {
  if (!value.targetPath.startsWith(`/surveys/${value.surveyId}/`)) {
    context.addIssue({
      code: 'custom',
      path: ['targetPath'],
      message: 'targetPath must belong to surveyId',
    });
  }
}

export const dashboardSummarySchema = z.strictObject({
  pendingApprovals: z.number().int().nonnegative(),
  publishExceptions: z.number().int().nonnegative(),
  activePreviews: z.number().int().nonnegative(),
  activeExports: z.number().int().nonnegative(),
});

export const dashboardTaskSchema = z.strictObject({
  taskKey: z.string().min(1),
  kind: dashboardTaskKindSchema,
  surveyId: surveyIdSchema,
  surveyName: z.string().min(1),
  status: z.string().min(1),
  updatedAt: z.string().datetime({ offset: true }),
  targetPath: surveyTargetPathSchema,
}).superRefine(requireMatchingSurveyPath);

export const dashboardSurveySchema = z.strictObject({
  surveyId: surveyIdSchema,
  name: z.string().min(1),
  draftVersion: z.number().int().positive(),
  publishedVersion: z.number().int().positive().nullable(),
  publishState: dashboardPublishStateSchema,
  completedResponses: z.number().int().nonnegative().nullable(),
  updatedAt: z.string().datetime({ offset: true }),
  actions: z.array(dashboardActionSchema),
});

export const recentWorkSchema = z.strictObject({
  surveyId: surveyIdSchema,
  surveyName: z.string().min(1),
  page: recentWorkPageSchema,
  targetPath: surveyTargetPathSchema,
  visitedAt: z.string().datetime({ offset: true }),
}).superRefine((value, context) => {
  requireMatchingSurveyPath(value, context);
  const suffix = value.targetPath.slice(`/surveys/${value.surveyId}/`.length);
  const matchesPage = value.page === 'version'
    ? /^versions\/[1-9][0-9]*$/.test(suffix)
    : suffix === value.page;
  if (!matchesPage) {
    context.addIssue({
      code: 'custom',
      path: ['targetPath'],
      message: 'targetPath must match the recent work page',
    });
  }
});

export const dashboardViewSchema = z.strictObject({
  generatedAt: z.string().datetime({ offset: true }),
  visibleSections: z.array(dashboardSectionSchema),
  summary: dashboardSummarySchema,
  tasks: z.array(dashboardTaskSchema),
  surveys: z.array(dashboardSurveySchema),
  recentWork: z.array(recentWorkSchema),
});

export type Me = z.infer<typeof meSchema>;
export type TokenView = z.infer<typeof tokenViewSchema>;
export type DashboardSection = z.infer<typeof dashboardSectionSchema>;
export type DashboardTaskKind = z.infer<typeof dashboardTaskKindSchema>;
export type DashboardPublishState = z.infer<typeof dashboardPublishStateSchema>;
export type DashboardAction = z.infer<typeof dashboardActionSchema>;
export type RecentWorkPage = z.infer<typeof recentWorkPageSchema>;
export type DashboardSummary = z.infer<typeof dashboardSummarySchema>;
export type DashboardTask = z.infer<typeof dashboardTaskSchema>;
export type DashboardSurvey = z.infer<typeof dashboardSurveySchema>;
export type RecentWork = z.infer<typeof recentWorkSchema>;
export type DashboardView = z.infer<typeof dashboardViewSchema>;
