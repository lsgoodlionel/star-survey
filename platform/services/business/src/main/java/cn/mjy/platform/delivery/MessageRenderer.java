package cn.mjy.platform.delivery;

/**
 * 消息模板渲染。只认三个占位符，其余原样保留：
 * <ul>
 *   <li>{@code {{name}}}——收件人姓名，没有时替换成空串；</li>
 *   <li>{@code {{link}}}——这位收件人专属的作答地址（含其邀请码与签名参数）；</li>
 *   <li>{@code {{unsubscribe}}}——退订地址；邮件渠道的模板必须包含它。</li>
 * </ul>
 *
 * <p>刻意不做表达式求值、不做循环判断：模板来自租户用户，任何可执行的模板语言都是一条注入面。
 * 替换是一次性扫描，替换进去的内容不会被再次当作占位符扫描（否则地址里出现 {{…}} 就能套娃）。
 */
final class MessageRenderer {

    static final String NAME = "{{name}}";
    static final String LINK = "{{link}}";
    static final String UNSUBSCRIBE = "{{unsubscribe}}";

    private MessageRenderer() {
    }

    static String render(String template, String name, String link, String unsubscribeUrl) {
        StringBuilder out = new StringBuilder(template.length() + 128);
        int position = 0;
        while (position < template.length()) {
            int next = template.indexOf("{{", position);
            if (next < 0) {
                out.append(template, position, template.length());
                break;
            }
            out.append(template, position, next);
            String replacement = replacementAt(template, next, name, link, unsubscribeUrl);
            if (replacement == null) {
                out.append("{{");
                position = next + 2;
            } else {
                out.append(replacement);
                position = next + placeholderLength(template, next);
            }
        }
        return out.toString();
    }

    private static String replacementAt(String template, int at, String name, String link, String unsubscribeUrl) {
        if (template.startsWith(NAME, at)) {
            return name == null ? "" : name;
        }
        if (template.startsWith(LINK, at)) {
            return link;
        }
        if (template.startsWith(UNSUBSCRIBE, at)) {
            return unsubscribeUrl == null ? "" : unsubscribeUrl;
        }
        return null;
    }

    private static int placeholderLength(String template, int at) {
        if (template.startsWith(NAME, at)) {
            return NAME.length();
        }
        if (template.startsWith(LINK, at)) {
            return LINK.length();
        }
        return UNSUBSCRIBE.length();
    }
}
