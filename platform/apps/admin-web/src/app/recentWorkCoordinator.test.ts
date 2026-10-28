import { expect, test } from 'vitest';
import { RecentWorkCoordinatorRegistry } from './recentWorkCoordinator';

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

test('keepsSameIdentityRemountsOnOneSerialQueueThenReclaimsTheCoordinator', async () => {
  const registry = new RecentWorkCoordinatorRegistry<string>();
  const scope = {};
  const firstOwner = Symbol('first');
  const secondOwner = Symbol('second');
  const gate = deferred();
  const started = deferred();
  const processed: string[] = [];
  const coordinator = registry.acquire(scope, 'tenant-a:actor-a', firstOwner);
  const firstVisit = registry.activate(coordinator, firstOwner, 'edit', 'edit');

  registry.schedule(scope, 'tenant-a:actor-a', coordinator, firstVisit!, async () => {
    started.resolve();
    await gate.promise;
    processed.push('edit');
  });
  await started.promise;
  registry.release(scope, 'tenant-a:actor-a', coordinator, firstOwner);
  const remounted = registry.acquire(scope, 'tenant-a:actor-a', secondOwner);
  const secondVisit = registry.activate(remounted, secondOwner, 'preview', 'preview');
  registry.schedule(scope, 'tenant-a:actor-a', remounted, secondVisit!, async () => {
    processed.push('preview');
  });

  expect(remounted).toBe(coordinator);
  expect(registry.size(scope)).toBe(1);
  gate.resolve();
  await registry.whenIdle(coordinator);
  expect(processed).toEqual(['edit', 'preview']);
  expect(registry.size(scope)).toBe(1);

  registry.release(scope, 'tenant-a:actor-a', coordinator, secondOwner);
  await Promise.resolve();
  expect(registry.size(scope)).toBe(0);
});

test('reclaimsSettledCoordinatorsAcrossManyIdentityLifecycles', async () => {
  const registry = new RecentWorkCoordinatorRegistry<string>();
  const scope = {};

  for (let index = 0; index < 25; index += 1) {
    const owner = Symbol(String(index));
    const identity = `tenant-a:actor-${index}`;
    const coordinator = registry.acquire(scope, identity, owner);
    registry.release(scope, identity, coordinator, owner);
  }

  await Promise.resolve();
  expect(registry.size(scope)).toBe(0);
});
