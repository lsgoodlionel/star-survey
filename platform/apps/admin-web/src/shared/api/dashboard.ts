import { z } from 'zod';
import type { ApiClient } from './http';
import {
  dashboardViewSchema,
  type DashboardTask,
  type DashboardSurvey,
  type DashboardView,
  type RecentWork,
  type RecentWorkPage,
} from './schemas';

export type { DashboardTask, DashboardSurvey, DashboardView, RecentWork };

export interface DashboardOptions {
  surveyLimit?: number;
  taskLimit?: number;
}

type RecentWorkNonVersionPage = Exclude<RecentWorkPage, 'version'>;

export type RecentWorkCommand =
  | { surveyId: string; page: RecentWorkNonVersionPage; version: null }
  | { surveyId: string; page: 'version'; version: number };

export const dashboardQueryKey = (tenantId: string) => ['dashboard', tenantId] as const;

export function getDashboard(
  api: ApiClient,
  options: DashboardOptions = {},
  signal?: AbortSignal,
) {
  const search = new URLSearchParams();
  if (options.surveyLimit !== undefined) search.set('surveyLimit', String(options.surveyLimit));
  if (options.taskLimit !== undefined) search.set('taskLimit', String(options.taskLimit));
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
  return api.request({
    path: '/v1/dashboard/recent-work',
    method: 'POST',
    body: command,
    schema: z.undefined(),
    signal,
  });
}
