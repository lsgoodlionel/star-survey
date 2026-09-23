package cn.mjy.platform.contacts;

import java.util.List;

/** 一页联系人；nextCursor 为空表示没有下一页。 */
public record ContactPage(List<ContactView> items, String nextCursor) {

    public ContactPage {
        items = List.copyOf(items);
    }
}
