package cn.mjy.platform.delivery;

import java.util.List;

/**
 * 网站内嵌代码（R05-07）。生成一段可以直接粘进页面的 iframe，并给出必须配套的浏览器侧设置。
 *
 * <p>取值理由：
 * <ul>
 *   <li>{@code sandbox="allow-forms allow-scripts allow-same-origin allow-popups"}——问卷要提交表单、
 *       跑引擎自己的脚本、用自己的会话 Cookie，这四项缺一不可；但不给 {@code allow-top-navigation}，
 *       所以被嵌页面无法把宿主页整个跳走（问卷里的跳转 URL 白名单是另一道闸，见 R04-07）。
 *       引擎与宿主站不同源，{@code allow-same-origin} 只恢复引擎自己的源，不等于放开沙箱。</li>
 *   <li>{@code referrerpolicy="strict-origin-when-cross-origin"}——引擎只看到宿主站的源，
 *       看不到宿主页的完整地址（可能带宿主自己的业务参数）。</li>
 *   <li>宿主站若启用了 CSP，必须把引擎的源加进 {@code frame-src}，否则 iframe 会被拦掉；
 *       引擎侧的 {@code X-Frame-Options} / {@code frame-ancestors} 也要允许宿主站。
 *       这两条是部署配置，平台无法代为设置，只能在提示里写清楚。</li>
 * </ul>
 *
 * <p>只输出固定属性与一个由平台自己拼出的地址，不接受调用方的任意 HTML 片段。
 */
final class EmbedSnippet {

    static final String SANDBOX = "allow-forms allow-scripts allow-same-origin allow-popups";
    static final String REFERRER_POLICY = "strict-origin-when-cross-origin";
    static final int MIN_DIMENSION = 100;
    static final int MAX_DIMENSION = 4000;

    private EmbedSnippet() {
    }

    static String iframe(String url, int width, int height, String title) {
        requireDimension(width, "width");
        requireDimension(height, "height");
        return "<iframe src=\"" + escape(url) + "\""
                + " width=\"" + width + "\" height=\"" + height + "\""
                + " title=\"" + escape(title) + "\""
                + " loading=\"lazy\""
                + " referrerpolicy=\"" + REFERRER_POLICY + "\""
                + " sandbox=\"" + SANDBOX + "\""
                + " style=\"border:0;max-width:100%\"></iframe>";
    }

    /** 宿主站与引擎侧必须配套的设置；随内嵌代码一起返回，避免"嵌上去是空白"这类排查。 */
    static List<String> deploymentNotes(String engineOrigin) {
        return List.of(
                "宿主站的 Content-Security-Policy 需要包含 frame-src " + engineOrigin + "；",
                "引擎侧需要允许被该宿主站内嵌（frame-ancestors 或 X-Frame-Options）；",
                "跨站内嵌时浏览器按第三方 Cookie 处理作答会话，Safari 与 Chrome 的默认设置可能拦截，"
                        + "需要引擎的会话 Cookie 带 SameSite=None; Secure；",
                "以上三项是部署配置，平台无法代为设置。");
    }

    private static void requireDimension(int value, String name) {
        if (value < MIN_DIMENSION || value > MAX_DIMENSION) {
            throw DeliveryExceptions.invalid(name + " must be between " + MIN_DIMENSION + " and " + MAX_DIMENSION);
        }
    }

    private static String escape(String value) {
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }
}
