package cn.mjy.platform.engine;

import java.util.Objects;

/**
 * 投影的一次实际状态变化。
 *
 * @param from 变化前的状态；平台第一次听说这份答卷时为 {@code null}
 * @param to   变化后的状态
 */
public record Transition(ResponseState from, ResponseState to) {

    public Transition {
        Objects.requireNonNull(to, "to");
    }
}
