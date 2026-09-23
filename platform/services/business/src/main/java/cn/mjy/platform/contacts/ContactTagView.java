package cn.mjy.platform.contacts;

import java.util.UUID;

/** 标签：每租户一套，名称不区分大小写唯一。 */
public record ContactTagView(UUID id, String name) {
}
