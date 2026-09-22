package cn.mjy.platform.response;

/** 取不到作答值（网关未配置、不可达、引擎报错、应答不可信）→ 503，不返回缺作答的页。 */
public class ResponseAnswersUnavailableException extends RuntimeException {

    public ResponseAnswersUnavailableException(String message) {
        super(message);
    }
}
