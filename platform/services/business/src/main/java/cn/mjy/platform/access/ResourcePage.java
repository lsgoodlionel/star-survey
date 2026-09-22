package cn.mjy.platform.access;

import java.util.List;

/** 资源列表的一页。nextCursor 为空表示没有下一页；游标对调用方不透明。 */
public record ResourcePage(List<ResourceView> items, String nextCursor) {

    public ResourcePage {
        items = List.copyOf(items);
    }
}
