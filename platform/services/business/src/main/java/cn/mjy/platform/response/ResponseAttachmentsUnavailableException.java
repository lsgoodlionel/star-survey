package cn.mjy.platform.response;

/** 取不到附件字节（网关未配置、不可达、引擎报错）。<b>暂时性</b>：作业退避后重试，绝不判定为缺失。 */
public class ResponseAttachmentsUnavailableException extends RuntimeException {

    public ResponseAttachmentsUnavailableException(String message) {
        super(message);
    }
}
