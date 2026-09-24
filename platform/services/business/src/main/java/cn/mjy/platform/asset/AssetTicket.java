package cn.mjy.platform.asset;

import cn.mjy.platform.shared.security.SignedParameters;
import java.util.Map;

/**
 * 一张签好的取件票：路径 ＋ 查询参数（{@code exp} 与 {@code sig}）。
 *
 * <p>整张票就是一个地址，交给作答者的浏览器；引擎与插件都不需要认识它。
 */
public record AssetTicket(String path, Map<String, String> query) {

    public AssetTicket {
        query = Map.copyOf(query);
    }

    /** 拼成绝对地址；{@code baseUrl} 末尾不带斜杠。 */
    public String url(String baseUrl) {
        return baseUrl + path + "?" + SignedParameters.toQueryString(query);
    }
}
