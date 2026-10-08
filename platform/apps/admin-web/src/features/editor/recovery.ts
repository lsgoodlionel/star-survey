export interface EditorRecoveryPayload {
  definition: unknown;
  selectedQuestionUuid: string | null;
  surveyId: string;
  tenantId: string;
  version: number;
}

let activeRecovery: EditorRecoveryPayload | null = null;
const savedRecoveries = new Map<string, EditorRecoveryPayload>();

export function registerActiveEditorRecovery(payload: EditorRecoveryPayload | null) {
  activeRecovery = payload ? structuredClone(payload) : null;
}

export function clearActiveEditorRecovery(tenantId: string, surveyId: string) {
  if (activeRecovery?.tenantId === tenantId && activeRecovery.surveyId === surveyId) {
    activeRecovery = null;
  }
}

export function captureEditorRecovery() {
  if (!activeRecovery) return;
  savedRecoveries.set(recoveryKey(activeRecovery.tenantId, activeRecovery.surveyId), structuredClone(activeRecovery));
}

export function takeEditorRecovery(tenantId: string, surveyId: string) {
  const key = recoveryKey(tenantId, surveyId);
  const recovery = savedRecoveries.get(key) ?? null;
  savedRecoveries.delete(key);
  return recovery ? structuredClone(recovery) : null;
}

export function discardEditorRecovery(tenantId: string, surveyId: string) {
  savedRecoveries.delete(recoveryKey(tenantId, surveyId));
}

function recoveryKey(tenantId: string, surveyId: string) {
  return JSON.stringify([tenantId, surveyId]);
}
