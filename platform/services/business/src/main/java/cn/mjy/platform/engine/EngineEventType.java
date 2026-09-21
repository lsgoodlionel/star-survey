package cn.mjy.platform.engine;

import java.util.Arrays;
import java.util.Optional;

/** 引擎侧事实类型（{@code MjyEventLog::TYPE_*}，schemaVersion 1）及其推动投影到达的状态。 */
public enum EngineEventType {

    SAVED("response.saved", ResponseState.IN_PROGRESS),
    COMPLETED("response.completed", ResponseState.ENGINE_COMPLETED),
    DELETED("response.deleted", ResponseState.DELETED);

    private final String wireName;
    private final ResponseState target;

    EngineEventType(String wireName, ResponseState target) {
        this.wireName = wireName;
        this.target = target;
    }

    public String wireName() {
        return wireName;
    }

    public ResponseState target() {
        return target;
    }

    public static Optional<EngineEventType> fromWire(String name) {
        return Arrays.stream(values()).filter(type -> type.wireName.equals(name)).findFirst();
    }
}
