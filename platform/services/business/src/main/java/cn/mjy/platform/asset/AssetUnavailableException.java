package cn.mjy.platform.asset;

/**
 * 资产服务当下不可用：主密钥或对外地址没配、存储写不进去。对外 503。
 *
 * <p>失败即关闭——签不出票就不发布、存不下就不收，绝不退化成"发一份图全裂的问卷"。
 */
public class AssetUnavailableException extends RuntimeException {

    public AssetUnavailableException(String message) {
        super(message);
    }

    public AssetUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
