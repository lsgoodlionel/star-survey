import { createContext, useContext, useEffect } from 'react';
import type { RecentWorkCommand } from '../shared/api/dashboard';

export type RecentWorkReadiness =
  | { page: Exclude<RecentWorkCommand['page'], 'version'>; version: null }
  | { page: 'version'; version: number };

export interface SurveyShellContextValue {
  setUnsavedChanges(value: boolean): void;
  reportPageReady(readiness: RecentWorkReadiness): void;
}

export const SurveyShellContext = createContext<SurveyShellContextValue | null>(null);

export function useSurveyShell() {
  return useContext(SurveyShellContext);
}

export function useSurveyPageReady(
  page: RecentWorkCommand['page'],
  ready: boolean,
  version: number | null = null,
) {
  const surveyShell = useSurveyShell();
  const reportPageReady = surveyShell?.reportPageReady;
  useEffect(() => {
    if (!ready || !reportPageReady) return;
    if (page === 'version') {
      if (version !== null) reportPageReady({ page, version });
      return;
    }
    reportPageReady({ page, version: null });
  }, [page, ready, reportPageReady, version]);
}

export function surveyWorkflowHref(surveyId: string, path: string, question: string | null) {
  const href = `/surveys/${surveyId}/${path}`;
  return question ? `${href}?question=${encodeURIComponent(question)}` : href;
}
