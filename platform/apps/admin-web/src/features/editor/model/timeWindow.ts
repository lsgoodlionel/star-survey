import type { EditableSurveyDefinition, JsonObject } from './definition';

export interface TimeWindow {
  opensAt?: string;
  closesAt?: string;
  timezone: string;
}

interface TimeWindowState {
  editable: boolean;
  window: TimeWindow | null;
}

function object(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function readTimeWindow(definition: EditableSurveyDefinition): TimeWindowState {
  const policy = definition.source.policy;
  if (policy === undefined) return { editable: true, window: null };
  if (!object(policy) || policy.policyVersion !== 1) return { editable: false, window: null };
  const window = policy.window;
  if (window === undefined) return { editable: true, window: null };
  if (!object(window)
    || Object.keys(window).some((key) => !['opensAt', 'closesAt', 'timezone'].includes(key))
    || (window.opensAt !== undefined && typeof window.opensAt !== 'string')
    || (window.closesAt !== undefined && typeof window.closesAt !== 'string')
    || (window.timezone !== undefined && typeof window.timezone !== 'string')) {
    return { editable: false, window: null };
  }
  return { editable: true, window: {
    opensAt: window.opensAt as string | undefined,
    closesAt: window.closesAt as string | undefined,
    timezone: (window.timezone as string | undefined) ?? 'Asia/Shanghai',
  } };
}

export function updateTimeWindow(definition: EditableSurveyDefinition, next: TimeWindow | null): EditableSurveyDefinition {
  if (!readTimeWindow(definition).editable) return definition;
  const source = structuredClone(definition.source);
  const policy = object(source.policy) ? source.policy : { policyVersion: 1 };
  if (next) {
    policy.window = {
      ...(next.opensAt ? { opensAt: next.opensAt } : {}),
      ...(next.closesAt ? { closesAt: next.closesAt } : {}),
      timezone: next.timezone,
    };
    source.policy = policy;
  } else {
    delete policy.window;
    if (Object.keys(policy).length === 1) delete source.policy;
    else source.policy = policy;
  }
  return { ...definition, source };
}

export function validateTimeWindow(definition: EditableSurveyDefinition): string[] {
  const state = readTimeWindow(definition);
  if (!state.editable) return [];
  const window = state.window;
  if (!window) return [];
  const errors: string[] = [];
  if (!window.opensAt && !window.closesAt) errors.push('开放时间和截止时间至少填写一项');
  const format = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/;
  if ([window.opensAt, window.closesAt].some((value) => value && !format.test(value))) {
    errors.push('开放时间和截止时间须为不带偏移的本地日期时间');
  }
  try {
    if (window.timezone !== 'UTC' && !window.timezone.includes('/')) throw new RangeError('not an IANA region');
    new Intl.DateTimeFormat('en', { timeZone: window.timezone });
  }
  catch { errors.push('时区须为有效的 IANA 时区名称'); }
  const withSeconds = (value: string) => value.length === 16 ? `${value}:00` : value;
  if (window.opensAt && window.closesAt && withSeconds(window.opensAt) >= withSeconds(window.closesAt)) {
    errors.push('截止时间必须晚于开放时间');
  }
  if (definition.settings?.startdate !== undefined || definition.settings?.expires !== undefined) {
    errors.push('时间窗不能与原生开始或截止设置同时使用');
  }
  return errors;
}
