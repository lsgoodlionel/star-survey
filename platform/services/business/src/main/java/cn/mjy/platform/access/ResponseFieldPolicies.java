package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantContext;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 答卷明细的字段级控制入口。查看或导出明细前先取得策略：没有明细权限直接拒绝
 * （统计查看者走不到这里），有明细权限但没有"查看敏感字段"时，敏感字段被遮蔽。
 *
 * <p>哪些字段敏感（手机号、证件号……）由问卷模块按题目元数据给出，这里只负责按权限遮蔽。
 */
@Service
public class ResponseFieldPolicies {

    private final AccessDecisionService access;

    ResponseFieldPolicies(AccessDecisionService access) {
        this.access = access;
    }

    /** 在线查看答卷明细。 */
    public ResponseFieldPolicy forResponses(TenantContext ctx, UUID surveyId, Set<String> sensitiveFields) {
        return policyFor(ctx, Permission.VIEW_RAW_RESPONSES, surveyId, sensitiveFields);
    }

    /** 导出答卷明细。导出同样遮蔽敏感字段。 */
    public ResponseFieldPolicy forExport(TenantContext ctx, UUID surveyId, Set<String> sensitiveFields) {
        return policyFor(ctx, Permission.EXPORT_RAW_RESPONSES, surveyId, sensitiveFields);
    }

    private ResponseFieldPolicy policyFor(TenantContext ctx, Permission action, UUID surveyId,
            Set<String> sensitiveFields) {
        access.require(ctx, action, surveyId);
        boolean reveal = access.can(ctx, Permission.VIEW_SENSITIVE_FIELDS, surveyId).allowed();
        return new ResponseFieldPolicy(sensitiveFields, reveal);
    }
}
