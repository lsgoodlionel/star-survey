package cn.mjy.platform.contacts;

import java.util.UUID;

/**
 * 联系人的可写字段。{@code null} 表示"本次不提供"：新建时留空，更新时保留原值。
 * 一律用 {@code withXxx} 复制出新实例，不就地修改。
 *
 * <p>{@code actorId} 只对员工联系人有意义（对应 access 模块的成员），
 * {@code principalId} 只对组织成员联系人有意义（对应 identity_binding 的主体）；
 * 两者都只是"指向"，不会让三类身份合并（ADR 0017 决定 1）。
 */
public record ContactDraft(String displayName, String email, String phone, String employeeId,
        ExternalContactIdentity identity, UUID orgUnitId, String actorId, UUID principalId) {

    public static ContactDraft empty() {
        return new ContactDraft(null, null, null, null, null, null, null, null);
    }

    public static ContactDraft named(String displayName) {
        return empty().withDisplayName(displayName);
    }

    public ContactDraft withDisplayName(String value) {
        return new ContactDraft(value, email, phone, employeeId, identity, orgUnitId, actorId, principalId);
    }

    public ContactDraft withEmail(String value) {
        return new ContactDraft(displayName, value, phone, employeeId, identity, orgUnitId, actorId, principalId);
    }

    public ContactDraft withPhone(String value) {
        return new ContactDraft(displayName, email, value, employeeId, identity, orgUnitId, actorId, principalId);
    }

    public ContactDraft withEmployeeId(String value) {
        return new ContactDraft(displayName, email, phone, value, identity, orgUnitId, actorId, principalId);
    }

    public ContactDraft withIdentity(ExternalContactIdentity value) {
        return new ContactDraft(displayName, email, phone, employeeId, value, orgUnitId, actorId, principalId);
    }

    public ContactDraft withUnit(UUID value) {
        return new ContactDraft(displayName, email, phone, employeeId, identity, value, actorId, principalId);
    }

    public ContactDraft withActor(String value) {
        return new ContactDraft(displayName, email, phone, employeeId, identity, orgUnitId, value, principalId);
    }

    public ContactDraft withPrincipal(UUID value) {
        return new ContactDraft(displayName, email, phone, employeeId, identity, orgUnitId, actorId, value);
    }
}
