import { describe, expect, test } from 'vitest';
import gatewayFixture from '../../../test/fixtures/publish-gateway.json';
import { parseDefinition, serializeDefinition } from './definition';
import { readTimeWindow, updateTimeWindow, validateTimeWindow } from './timeWindow';

describe('editable access time window', () => {
  test('preserves unrelated policy rules and unknown survey fields', () => {
    const source = { ...structuredClone(gatewayFixture), future: { keep: true }, policy: {
      policyVersion: 1, access: { captcha: true }, limits: { maxDurationSeconds: 600 },
    } };
    const definition = parseDefinition(source);
    const updated = updateTimeWindow(definition, {
      opensAt: '2026-11-12T09:00', closesAt: '2026-11-13T18:00', timezone: 'Asia/Shanghai',
    });
    expect(serializeDefinition(updated)).toEqual({ ...source, policy: { ...source.policy,
      window: { opensAt: '2026-11-12T09:00', closesAt: '2026-11-13T18:00', timezone: 'Asia/Shanghai' },
    } });
    expect(source.policy).not.toHaveProperty('window');
    expect(readTimeWindow(updated).window?.opensAt).toBe('2026-11-12T09:00');
    expect(serializeDefinition(updateTimeWindow(updated, null))).toEqual(source);
  });

  test('removes an otherwise empty policy when its time window is cleared', () => {
    const original = parseDefinition(gatewayFixture);
    const withWindow = updateTimeWindow(original, { opensAt: '2026-11-12T09:00', timezone: 'Asia/Shanghai' });
    expect(serializeDefinition(updateTimeWindow(withWindow, null))).toEqual(gatewayFixture);
  });

  test('blocks empty, reversed, invalid timezone and raw native setting conflicts', () => {
    const original = parseDefinition(gatewayFixture);
    expect(validateTimeWindow(updateTimeWindow(original, { timezone: 'Asia/Shanghai' }))).toContain('开放时间和截止时间至少填写一项');
    expect(validateTimeWindow(updateTimeWindow(original, { opensAt: '2026-11-13T09:00', closesAt: '2026-11-12T09:00', timezone: 'Asia/Shanghai' }))).toContain('截止时间必须晚于开放时间');
    expect(validateTimeWindow(updateTimeWindow(original, { opensAt: '2026-11-12T09:00', closesAt: '2026-11-12T09:00:00', timezone: 'Asia/Shanghai' }))).toContain('截止时间必须晚于开放时间');
    expect(validateTimeWindow(updateTimeWindow(original, { opensAt: '2026-11-12T09:00', timezone: 'Moon/Base' }))).toContain('时区须为有效的 IANA 时区名称');
    expect(validateTimeWindow(updateTimeWindow(original, { opensAt: '2026-11-12T09:00', timezone: '+08:00' }))).toContain('时区须为有效的 IANA 时区名称');
    const withSetting = { ...original, settings: { ...original.settings, startdate: '2026-11-12 00:00:00' } };
    expect(validateTimeWindow(updateTimeWindow(withSetting, { opensAt: '2026-11-12T09:00', timezone: 'Asia/Shanghai' }))).toContain('时间窗不能与原生开始或截止设置同时使用');
  });

  test('does not overwrite an unsupported policy shape', () => {
    const source = { ...structuredClone(gatewayFixture), policy: { policyVersion: 2, future: true } };
    const definition = parseDefinition(source);
    expect(readTimeWindow(definition).editable).toBe(false);
    expect(updateTimeWindow(definition, { opensAt: '2026-11-12T09:00', timezone: 'Asia/Shanghai' })).toBe(definition);
  });

  test('keeps an unknown time window field read only and unchanged', () => {
    const source = { ...structuredClone(gatewayFixture), policy: { policyVersion: 1,
      window: { opensAt: '2026-11-12T09:00', timezone: 'Asia/Shanghai', futureRule: { keep: true } },
    } };
    const definition = parseDefinition(source);
    expect(readTimeWindow(definition).editable).toBe(false);
    expect(updateTimeWindow(definition, { closesAt: '2026-11-13T18:00', timezone: 'Asia/Shanghai' })).toBe(definition);
    expect(serializeDefinition(definition)).toEqual(source);
  });
});
