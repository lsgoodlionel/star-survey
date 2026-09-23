package cn.mjy.platform.contacts;

import org.springframework.stereotype.Component;

/**
 * 字段规范化与去重值计算（ADR 0017 决定 2）。单条写入与批量导入共用同一套规则，
 * 保证"手工建的人"与"导入的人"判重口径一致。任何不合法都抛
 * {@link ContactRowRejectedException} 并带稳定错误码。
 */
@Component
class ContactNormalizer {

    private static final int MAX_NAME = 200;

    /** 规范化后的草稿与它的去重值（去重键为空时为 null）。 */
    record Normalized(ContactDraft draft, String dedupeValue) {
    }

    Normalized normalize(DedupeKey key, ContactDraft raw) {
        String name = DedupeKey.trim(raw.displayName());
        if (name != null && name.length() > MAX_NAME) {
            throw new ContactRowRejectedException("invalid_name");
        }
        String email = require(DedupeKey.normalizeEmail(raw.email()), DedupeKey::isEmail, "invalid_email");
        String phone = require(DedupeKey.normalizePhone(raw.phone()), DedupeKey::isPhone, "invalid_phone");
        String employeeId = require(DedupeKey.trim(raw.employeeId()), DedupeKey::isEmployeeId,
                "invalid_employee_id");
        ExternalContactIdentity identity = raw.identity();
        if (identity != null && !identity.isWellFormed()) {
            throw new ContactRowRejectedException("invalid_external_id");
        }
        ContactDraft draft = new ContactDraft(name, email, phone, employeeId, identity, raw.orgUnitId(),
                DedupeKey.trim(raw.actorId()), raw.principalId());
        if (key == null) {
            return new Normalized(draft, null);
        }
        String dedupeValue = dedupeValue(key, draft);
        if (dedupeValue == null) {
            throw new ContactRowRejectedException(key.missingReason());
        }
        return new Normalized(draft, dedupeValue);
    }

    private static String dedupeValue(DedupeKey key, ContactDraft draft) {
        return switch (key) {
            case EMAIL -> draft.email();
            case PHONE -> draft.phone();
            case EMPLOYEE_ID -> draft.employeeId();
            case EXTERNAL_ID -> draft.identity() == null ? null : draft.identity().dedupeValue();
        };
    }

    private static String require(String normalized, java.util.function.Predicate<String> valid, String reason) {
        if (normalized != null && !valid.test(normalized)) {
            throw new ContactRowRejectedException(reason);
        }
        return normalized;
    }
}
