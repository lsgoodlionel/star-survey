package cn.mjy.platform.access;

/** 资源不存在或属于别的租户（行级安全下两者不可区分，也不应区分）。映射为 404。 */
public class ResourceNotFoundException extends RuntimeException {

    public ResourceNotFoundException(String message) {
        super(message);
    }
}
