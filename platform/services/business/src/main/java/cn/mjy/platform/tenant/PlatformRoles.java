package cn.mjy.platform.tenant;

/** 平台级角色名。租户内角色由 access 模块管理，这里只放控制平面需要的角色。 */
public final class PlatformRoles {

    /** 平台运营：唯一可以开通、暂停、关闭租户与登记引擎实例的角色。 */
    public static final String OPERATOR = "platform_operator";

    private PlatformRoles() {
    }
}
