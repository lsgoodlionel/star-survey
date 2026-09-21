package cn.mjy.platform.tenant.api;

/** 资源不存在，或者属于别的租户（对调用方而言两者不可区分，统一 404）。 */
public class NotFoundException extends RuntimeException {

    public NotFoundException(String message) {
        super(message);
    }
}
