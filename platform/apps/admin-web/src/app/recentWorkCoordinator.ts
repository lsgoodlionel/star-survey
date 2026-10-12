export interface RecentWorkVisit<T> {
  id: number;
  key: string;
  owner: symbol;
  value: T;
}

export interface RecentWorkCoordinator<T> {
  activeVisit: RecentWorkVisit<T> | null;
  nextId: number;
  owners: Set<symbol>;
  pendingTasks: number;
  queue: Promise<void>;
  scheduledVisits: Set<number>;
  cleanupGeneration: number;
}

export class RecentWorkCoordinatorRegistry<T> {
  private readonly coordinators = new WeakMap<object, Map<string, RecentWorkCoordinator<T>>>();

  get(scope: object, identity: string) {
    let identityMap = this.coordinators.get(scope);
    if (!identityMap) {
      identityMap = new Map();
      this.coordinators.set(scope, identityMap);
    }
    let coordinator = identityMap.get(identity);
    if (!coordinator) {
      coordinator = {
        activeVisit: null,
        nextId: 1,
        owners: new Set(),
        pendingTasks: 0,
        queue: Promise.resolve(),
        scheduledVisits: new Set(),
        cleanupGeneration: 0,
      };
      identityMap.set(identity, coordinator);
    }
    return coordinator;
  }

  acquire(scope: object, identity: string, owner: symbol) {
    const coordinator = this.get(scope, identity);
    this.retain(coordinator, owner);
    return coordinator;
  }

  retain(coordinator: RecentWorkCoordinator<T>, owner: symbol) {
    coordinator.cleanupGeneration += 1;
    coordinator.owners.add(owner);
  }

  release(
    scope: object,
    identity: string,
    coordinator: RecentWorkCoordinator<T>,
    owner: symbol,
  ) {
    coordinator.owners.delete(owner);
    if (coordinator.activeVisit?.owner === owner) coordinator.activeVisit = null;
    this.cleanupWhenIdle(scope, identity, coordinator);
  }

  activate(
    coordinator: RecentWorkCoordinator<T>,
    owner: symbol,
    key: string | null,
    value: T | null,
  ) {
    if (!key || value === null) {
      if (coordinator.activeVisit?.owner === owner) coordinator.activeVisit = null;
      return null;
    }
    if (coordinator.activeVisit?.owner === owner && coordinator.activeVisit.key === key) {
      return coordinator.activeVisit;
    }
    coordinator.scheduledVisits.clear();
    const visit = { id: coordinator.nextId++, key, owner, value };
    coordinator.activeVisit = visit;
    return visit;
  }

  schedule(
    scope: object,
    identity: string,
    coordinator: RecentWorkCoordinator<T>,
    visit: RecentWorkVisit<T>,
    work: () => Promise<void>,
  ) {
    if (coordinator.scheduledVisits.has(visit.id)) return;
    coordinator.scheduledVisits.add(visit.id);
    coordinator.pendingTasks += 1;
    const queued = coordinator.queue
      .catch(() => undefined)
      .then(async () => {
        if (coordinator.activeVisit?.id !== visit.id) return;
        await work();
      })
      .catch(() => undefined)
      .finally(() => {
        coordinator.pendingTasks -= 1;
        this.cleanupWhenIdle(scope, identity, coordinator);
      });
    coordinator.queue = queued;
  }

  whenIdle(coordinator: RecentWorkCoordinator<T>) {
    return coordinator.queue;
  }

  size(scope: object) {
    return this.coordinators.get(scope)?.size ?? 0;
  }

  private cleanupWhenIdle(
    scope: object,
    identity: string,
    coordinator: RecentWorkCoordinator<T>,
  ) {
    const generation = ++coordinator.cleanupGeneration;
    void Promise.resolve().then(() => {
      if (
        coordinator.cleanupGeneration !== generation
        || coordinator.owners.size > 0
        || coordinator.pendingTasks > 0
        || coordinator.activeVisit
      ) return;
      const identityMap = this.coordinators.get(scope);
      if (identityMap?.get(identity) !== coordinator) return;
      identityMap.delete(identity);
      if (identityMap.size === 0) this.coordinators.delete(scope);
    });
  }
}
