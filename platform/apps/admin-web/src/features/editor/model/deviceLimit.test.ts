import { describe, expect, test } from 'vitest';
import gatewayFixture from '../../../test/fixtures/publish-gateway.json';
import { parseDefinition, serializeDefinition } from './definition';
import { readDeviceLimit, updateDeviceLimit, validateDeviceLimit } from './deviceLimit';

describe('editable device response limit', () => {
  test('adds and clears device limit without replacing other rules', () => {
    const source = { ...structuredClone(gatewayFixture), future: { keep: true }, policy: {
      policyVersion: 1, network: { denyIps: ['10.0.0.0/8'] },
      limits: { maxDurationSeconds: 600, responses: [{ by: 'ip', max: 10 }] },
    } };
    const original = parseDefinition(source);
    const updated = updateDeviceLimit(original, 1);
    expect(serializeDefinition(updated)).toEqual({ ...source, policy: { ...source.policy,
      limits: { maxDurationSeconds: 600, responses: [{ by: 'ip', max: 10 }, { by: 'device', max: 1 }] },
    } });
    expect(readDeviceLimit(updated)).toEqual({ editable: true, max: 1 });
    expect(serializeDefinition(updateDeviceLimit(updated, null))).toEqual(source);
  });

  test('removes an otherwise empty policy when limit is cleared', () => {
    const original = parseDefinition(gatewayFixture);
    const limited = updateDeviceLimit(original, 1);
    expect(serializeDefinition(updateDeviceLimit(limited, null))).toEqual(gatewayFixture);
  });

  test('rejects out-of-range and noninteger limits before saving', () => {
    const original = parseDefinition(gatewayFixture);
    for (const max of [0, 10001, 1.5, NaN]) {
      expect(validateDeviceLimit(updateDeviceLimit(original, max))).toContain('同一设备答卷上限须为 1–10000 的整数');
    }
  });

  test('keeps unsupported or duplicate response limits read only', () => {
    const responses = [
      [{ by: 'device', max: 1 }, { by: 'device', max: 2 }],
      [{ by: 'future', max: 1 }],
      [{ by: 'device', max: '1' }],
      [{ by: 'device', max: 1, future: true }],
    ];
    for (const value of responses) {
      const source = { ...structuredClone(gatewayFixture), policy: { policyVersion: 1, limits: { responses: value } } };
      const original = parseDefinition(source);
      expect(readDeviceLimit(original).editable).toBe(false);
      expect(updateDeviceLimit(original, 3)).toBe(original);
      expect(serializeDefinition(original)).toEqual(source);
    }
  });
});
