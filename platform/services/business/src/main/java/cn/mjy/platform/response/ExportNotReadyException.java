package cn.mjy.platform.response;

/** 作业尚未完成（或已失败 / 取消），没有可下载的文件（409）。 */
public class ExportNotReadyException extends RuntimeException {

    public ExportNotReadyException(String message) {
        super(message);
    }
}
