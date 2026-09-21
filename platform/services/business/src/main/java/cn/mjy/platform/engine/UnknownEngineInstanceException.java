package cn.mjy.platform.engine;

import java.util.Set;

/** 批次里有未登记（或已停用）的引擎实例；整批拒收、一行不写。 */
public class UnknownEngineInstanceException extends RuntimeException {

    private final Set<String> instances;

    public UnknownEngineInstanceException(Set<String> instances) {
        super("unknown engine instances: " + instances);
        this.instances = Set.copyOf(instances);
    }

    public Set<String> instances() {
        return instances;
    }
}
