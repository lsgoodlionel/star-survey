import type { EditableSurveyDefinition, JsonObject } from './definition';

interface IpDenyListState {
  editable: boolean;
  denyIps: string[];
}

const policyKeys = new Set(['policyVersion', 'window', 'access', 'limits', 'network']);
const networkKeys = new Set(['allowIps', 'denyIps', 'allowRegions', 'denyRegions', 'regionUnknown']);

function object(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function readIpDenyList(definition: EditableSurveyDefinition): IpDenyListState {
  const policy = definition.source.policy;
  if (policy === undefined) return { editable: true, denyIps: [] };
  if (!object(policy) || policy.policyVersion !== 1 || Object.keys(policy).some((key) => !policyKeys.has(key))) {
    return { editable: false, denyIps: [] };
  }
  const network = policy.network;
  if (network === undefined) return { editable: true, denyIps: [] };
  if (!object(network) || Object.keys(network).some((key) => !networkKeys.has(key))
    || (network.denyIps !== undefined && (!Array.isArray(network.denyIps)
      || network.denyIps.some((value: unknown) => typeof value !== 'string')))) {
    return { editable: false, denyIps: [] };
  }
  return { editable: true, denyIps: [...(network.denyIps as string[] | undefined ?? [])] };
}

export function updateIpDenyList(definition: EditableSurveyDefinition, denyIps: string[]): EditableSurveyDefinition {
  if (!readIpDenyList(definition).editable) return definition;
  const source = structuredClone(definition.source);
  const policy = object(source.policy) ? source.policy : { policyVersion: 1 };
  const network = object(policy.network) ? policy.network : {};
  if (denyIps.length) network.denyIps = [...denyIps];
  else delete network.denyIps;
  if (Object.keys(network).length) policy.network = network;
  else delete policy.network;
  if (Object.keys(policy).length === 1) delete source.policy;
  else source.policy = policy;
  return { ...definition, source };
}

export function validateIpDenyList(definition: EditableSurveyDefinition): string[] {
  const state = readIpDenyList(definition);
  if (!state.editable) return [];
  const errors = state.denyIps.some((value) => !value.trim()) ? ['IP 拒绝清单不能包含空行'] : [];
  const policy = definition.source.policy;
  const network = object(policy) ? policy.network : undefined;
  if (object(network) && !['allowIps', 'denyIps', 'allowRegions', 'denyRegions'].some((key) =>
    Array.isArray(network[key]) && network[key].length > 0)) {
    errors.push('清空 IP 拒绝清单后，网络策略没有有效规则');
  }
  return errors;
}
