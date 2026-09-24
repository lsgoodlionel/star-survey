package cn.mjy.platform.asset;

/**
 * 资产或版本不存在。**跨租户也走这里**：别的租户的资产在行级安全下等同不存在，
 * 对外一律 404，绝不用 403 把"它确实存在"告诉对方。
 */
public class AssetNotFoundException extends RuntimeException {

    public AssetNotFoundException(String message) {
        super(message);
    }
}
