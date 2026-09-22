package cn.mjy.platform.access;

/** 请求本身不合法（名称为空、父节点是问卷、移动项目、游标无效等）。映射为 400。 */
public class InvalidResourceRequestException extends RuntimeException {

    public InvalidResourceRequestException(String message) {
        super(message);
    }
}
