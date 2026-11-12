import type { EditableSurveyDefinition, JsonObject } from './definition';

interface DeviceLimitState {
  editable: boolean;
  max: number | null;
}

const policyKeys = new Set(['policyVersion', 'window', 'access', 'limits', 'network']);
const limitKeys = new Set(['responses', 'maxDurationSeconds']);
const identities = new Set(['token', 'device', 'ip']);

function object(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function readDeviceLimit(definition: EditableSurveyDefinition): DeviceLimitState {
  const policy = definition.source.policy;
  if (policy === undefined) return { editable: true, max: null };
  if (!object(policy) || policy.policyVersion !== 1 || Object.keys(policy).some((key) => !policyKeys.has(key))) {
    return { editable: false, max: null };
  }
  const limits = policy.limits;
  if (limits === undefined) return { editable: true, max: null };
  if (!object(limits) || Object.keys(limits).some((key) => !limitKeys.has(key))) {
    return { editable: false, max: null };
  }
  const responses = limits.responses;
  if (responses === undefined) return { editable: true, max: null };
  if (!Array.isArray(responses)) return { editable: false, max: null };
  const seen = new Set<string>();
  for (const response of responses) {
    if (!object(response) || Object.keys(response).some((key) => !['by', 'max'].includes(key))
      || typeof response.by !== 'string' || !identities.has(response.by)
      || typeof response.max !== 'number' || seen.has(response.by)) {
      return { editable: false, max: null };
    }
    seen.add(response.by);
  }
  const device = responses.find((response) => response.by === 'device') as JsonObject | undefined;
  return { editable: true, max: device ? device.max as number : null };
}

export function updateDeviceLimit(definition: EditableSurveyDefinition, max: number | null): EditableSurveyDefinition {
  if (!readDeviceLimit(definition).editable) return definition;
  const source = structuredClone(definition.source);
  const policy = object(source.policy) ? source.policy : { policyVersion: 1 };
  const limits = object(policy.limits) ? policy.limits : {};
  const responses = Array.isArray(limits.responses) ? [...limits.responses] as JsonObject[] : [];
  const index = responses.findIndex((response) => response.by === 'device');
  if (max === null) {
    if (index >= 0) responses.splice(index, 1);
  } else if (index >= 0) {
    responses[index] = { ...responses[index], max };
  } else {
    responses.push({ by: 'device', max });
  }
  if (responses.length) limits.responses = responses;
  else delete limits.responses;
  if (Object.keys(limits).length) policy.limits = limits;
  else delete policy.limits;
  if (Object.keys(policy).length === 1) delete source.policy;
  else source.policy = policy;
  return { ...definition, source };
}

export function validateDeviceLimit(definition: EditableSurveyDefinition): string[] {
  const state = readDeviceLimit(definition);
  return state.editable && state.max !== null && (!Number.isInteger(state.max) || state.max < 1 || state.max > 10000)
    ? ['同一设备答卷上限须为 1–10000 的整数'] : [];
}
