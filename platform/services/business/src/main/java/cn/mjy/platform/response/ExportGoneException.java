package cn.mjy.platform.response;

/** 作业已到期，文件已删除（410）。 */
public class ExportGoneException extends RuntimeException {

    public ExportGoneException(String message) {
        super(message);
    }
}
