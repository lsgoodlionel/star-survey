package cn.mjy.platform.asset;

/**
 * 作答者上传当下取不到（网关未配置、不可达、拒绝，或应答不可信）。
 *
 * <p><b>绝不降级成"这份答卷没有上传"</b>：那会让一次配置错误变成一次静默的数据缺失，
 * 而且没有任何人察觉——与 ADR 0013 决定 8 的口径相同。
 */
public class ResponderUploadsUnavailableException extends RuntimeException {

    public ResponderUploadsUnavailableException(String message) {
        super(message);
    }
}
