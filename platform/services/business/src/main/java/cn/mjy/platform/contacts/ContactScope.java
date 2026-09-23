package cn.mjy.platform.contacts;

/**
 * 调用者在通讯录里的数据范围（ADR 0017 决定 4）。只有两种：
 * 全租户（持有租户级 manage-settings 的租户管理员），或"范围行指定的部门及其子孙"。
 * 后者没有范围行时看不到任何联系人——失败即关闭，绝不回退成全租户。
 */
record ContactScope(String actorId, boolean wholeTenant) {

    static ContactScope wholeTenant(String actorId) {
        return new ContactScope(actorId, true);
    }

    static ContactScope departments(String actorId) {
        return new ContactScope(actorId, false);
    }
}
