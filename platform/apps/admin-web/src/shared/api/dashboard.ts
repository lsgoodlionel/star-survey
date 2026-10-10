import { z } from 'zod';
import type { ApiClient } from './http';
import {
  dashboardLimitSchema,
  dashboardViewSchema,
  recentWorkCommandSchema,
  type DashboardTask,
  type DashboardSurvey,
  type DashboardView,
  type RecentWork,
  type RecentWorkCommand,
} from './schemas';

export type {
  DashboardTask,
  DashboardSurvey,
  DashboardView,
  RecentWork,
  RecentWorkCommand,
};

const dashboardOptionsSchema = z.strictObject({
  surveyLimit: dashboardLimitSchema.optional(),
  taskLimit: dashboardLimitSchema.optional(),
});

export type DashboardOptions = z.infer<typeof dashboardOptionsSchema>;

export const dashboardQueryKey = (tenantId: string) => ['dashboard', tenantId] as const;

export function getDashboard(
  api: ApiClient,
  options: DashboardOptions = {},
  signal?: AbortSignal,
) {
  const validatedOptions = dashboardOptionsSchema.parse(options);
  const search = new URLSearchParams();
  if (validatedOptions.surveyLimit !== undefined) {
    search.set('surveyLimit', String(validatedOptions.surveyLimit));
  }
  if (validatedOptions.taskLimit !== undefined) {
    search.set('taskLimit', String(validatedOptions.taskLimit));
  }
  const query = search.toString();

  return api.request({
    path: `/v1/dashboard${query ? `?${query}` : ''}`,
    schema: dashboardViewSchema,
    signal,
  });
}

export function recordRecentWork(
  api: ApiClient,
  command: RecentWorkCommand,
  signal?: AbortSignal,
) {
  const validatedCommand = recentWorkCommandSchema.parse(command);
  return api.request({
    path: '/v1/dashboard/recent-work',
    method: 'POST',
    body: validatedCommand,
    schema: z.undefined(),
    signal,
  });
}
