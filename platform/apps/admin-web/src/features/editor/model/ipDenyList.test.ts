import { describe, expect, test } from 'vitest';
import gatewayFixture from '../../../test/fixtures/publish-gateway.json';
import { parseDefinition, serializeDefinition } from './definition';
import { readIpDenyList, updateIpDenyList, validateIpDenyList } from './ipDenyList';

describe('editable IP denial policy', () => {
  test('adds and clears denial without changing other policy rules', () => {
    const source = { ...structuredClone(gatewayFixture), future: { keep: true }, policy: {
      policyVersion: 1, window: { opensAt: '2026-11-12T09:00', timezone: 'Asia/Shanghai' },
      network: { allowIps: ['10.0.0.0/8'], denyRegions: ['CN-BJ'] },
    } };
    const original = parseDefinition(source);
    const updated = updateIpDenyList(original, ['127.0.0.0/8', '::1']);
    expect(serializeDefinition(updated)).toEqual({ ...source, policy: { ...source.policy,
      network: { ...source.policy.network, denyIps: ['127.0.0.0/8', '::1'] },
    } });
    expect(readIpDenyList(updated)).toEqual({ editable: true, denyIps: ['127.0.0.0/8', '::1'] });
    expect(serializeDefinition(updateIpDenyList(updated, []))).toEqual(source);
  });

  test('removes an otherwise empty policy when denial is cleared', () => {
    const original = parseDefinition(gatewayFixture);
    const withDenial = updateIpDenyList(original, ['0.0.0.0/0', '::/0']);
    expect(serializeDefinition(updateIpDenyList(withDenial, []))).toEqual(gatewayFixture);
  });

  test('does not silently erase another network setting when denial is cleared', () => {
    const source = { ...structuredClone(gatewayFixture), policy: { policyVersion: 1,
      network: { denyIps: ['::1'], regionUnknown: 'allow' },
    } };
    const cleared = updateIpDenyList(parseDefinition(source), []);
    expect(serializeDefinition(cleared)).toEqual({ ...source, policy: { policyVersion: 1,
      network: { regionUnknown: 'allow' },
    } });
    expect(validateIpDenyList(cleared)).toContain('清空 IP 拒绝清单后，网络策略没有有效规则');
    const emptyAllow = parseDefinition({ ...source, policy: { policyVersion: 1,
      network: { denyIps: ['::1'], allowIps: [], allowRegions: [], denyRegions: [] },
    } });
    expect(validateIpDenyList(updateIpDenyList(emptyAllow, []))).toContain('清空 IP 拒绝清单后，网络策略没有有效规则');
    const activeAllow = parseDefinition({ ...source, policy: { policyVersion: 1,
      network: { denyIps: ['::1'], allowIps: ['10.0.0.0/8'] },
    } });
    expect(validateIpDenyList(updateIpDenyList(activeAllow, []))).toEqual([]);
  });

  test('does not save an empty line as a network rule', () => {
    const definition = updateIpDenyList(parseDefinition(gatewayFixture), ['127.0.0.0/8', '  ']);
    expect(validateIpDenyList(definition)).toContain('IP 拒绝清单不能包含空行');
  });

  test('does not overwrite unsupported policy or network structures', () => {
    const sources = [
      { ...structuredClone(gatewayFixture), policy: { policyVersion: 2, network: { denyIps: ['::1'] } } },
      { ...structuredClone(gatewayFixture), policy: { policyVersion: 1, network: { denyIps: ['::1'], futureRule: true } } },
      { ...structuredClone(gatewayFixture), policy: { policyVersion: 1, network: { denyIps: '::1' } } },
    ];
    for (const source of sources) {
      const definition = parseDefinition(source);
      expect(readIpDenyList(definition).editable).toBe(false);
      expect(updateIpDenyList(definition, ['127.0.0.0/8'])).toBe(definition);
      expect(serializeDefinition(definition)).toEqual(source);
    }
  });
});
