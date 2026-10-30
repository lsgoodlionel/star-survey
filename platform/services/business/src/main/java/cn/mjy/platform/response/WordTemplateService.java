package cn.mjy.platform.response;

import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
class WordTemplateService {

    static final int MAX_TEMPLATES_PER_SURVEY = 100;
    static final int MAX_VERSIONS_PER_TEMPLATE = 100;
    static final int MAX_LIST_LIMIT = 100;

    record Frozen(String reference, byte[] content, String sha256) {
        Frozen {
            content = content.clone();
        }
    }

    private final TenantScope tenantScope;
    private final ResponseAccess access;
    private final WordTemplateRepository templates;

    WordTemplateService(TenantScope tenantScope, ResponseAccess access, WordTemplateRepository templates) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.templates = templates;
    }

    @Transactional
    WordTemplateView create(TenantContext ctx, UUID surveyId, String name, byte[] content) {
        String normalized = name == null ? "" : name.trim();
        if (normalized.isEmpty() || normalized.length() > 200) {
            throw new InvalidResponseQueryException("name must contain 1-200 characters");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EXPORT_RAW_RESPONSES, surveyId);
            templates.lockSurvey(surveyId);
            if (templates.count(surveyId) >= MAX_TEMPLATES_PER_SURVEY) {
                throw new InvalidResponseQueryException("每份问卷最多可保存 100 个 Word 模板");
            }
            byte[] checked = validate(content);
            return view(templates.insert(ctx.tenantId(), surveyId, UUID.randomUUID(), normalized, checked,
                    sha256(checked), ctx.actorId()));
        });
    }

    @Transactional
    WordTemplateView addVersion(TenantContext ctx, UUID surveyId, UUID id, byte[] content) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EXPORT_RAW_RESPONSES, surveyId);
            int current = templates.lockCurrentVersion(surveyId, id)
                    .orElseThrow(() -> new ResponseNotFoundException("word template not found: " + id));
            if (current >= MAX_VERSIONS_PER_TEMPLATE) {
                throw new InvalidResponseQueryException("每个 Word 模板最多可保存 100 个版本");
            }
            byte[] checked = validate(content);
            return templates.addVersion(surveyId, id, checked, sha256(checked), ctx.actorId())
                    .map(WordTemplateService::view)
                    .orElseThrow(() -> new ResponseNotFoundException("word template not found: " + id));
        });
    }

    List<WordTemplateView> list(TenantContext ctx, UUID surveyId, int limit, int offset) {
        if (limit < 1 || limit > MAX_LIST_LIMIT || offset < 0) {
            throw new InvalidResponseQueryException("limit 必须为 1-100，offset 不能小于 0");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EXPORT_RAW_RESPONSES, surveyId);
            return templates.list(surveyId, limit, offset).stream().map(WordTemplateService::view).toList();
        });
    }

    WordTemplateView get(TenantContext ctx, UUID surveyId, UUID id, Integer version) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EXPORT_RAW_RESPONSES, surveyId);
            return (version == null ? templates.find(surveyId, id) : templates.findVersion(surveyId, id, version))
                    .map(WordTemplateService::view)
                    .orElseThrow(() -> new ResponseNotFoundException("word template not found: " + id));
        });
    }

    Frozen resolve(TenantContext ctx, UUID surveyId, String reference) {
        CustomReference custom = CustomReference.parse(reference);
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.EXPORT_RAW_RESPONSES, surveyId);
            WordTemplateRepository.Version version = templates.findVersion(surveyId, custom.id(), custom.version())
                    .orElseThrow(() -> new ResponseNotFoundException("word template not found: " + custom.id()));
            return new Frozen(reference, version.content(), version.sha256());
        });
    }

    private static byte[] validate(byte[] content) {
        if (content == null) {
            throw new InvalidResponseQueryException("template file is required");
        }
        byte[] copy = content.clone();
        try {
            DocxTemplateRenderer.validate(copy);
        } catch (DocxTemplateRenderer.TemplateException e) {
            throw new InvalidResponseQueryException(e.getMessage());
        } catch (IOException e) {
            throw new InvalidResponseQueryException("template is not a readable DOCX");
        }
        return copy;
    }

    private static WordTemplateView view(WordTemplateRepository.Version version) {
        return new WordTemplateView(version.id(), version.name(), version.version(), version.sha256(),
                version.byteSize(), version.createdAt());
    }

    private static WordTemplateView view(WordTemplateRepository.Metadata template) {
        return new WordTemplateView(template.id(), template.name(), template.version(), template.sha256(),
                template.byteSize(), template.createdAt());
    }

    private static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required", e);
        }
    }

    private record CustomReference(UUID id, int version) {
        static CustomReference parse(String value) {
            if (value == null || !value.startsWith("custom:")) {
                throw new InvalidResponseQueryException("templateVersion must be default or custom:<uuid>:<version>");
            }
            String[] parts = value.split(":", -1);
            if (parts.length != 3) {
                throw new InvalidResponseQueryException("templateVersion must be default or custom:<uuid>:<version>");
            }
            try {
                int version = Integer.parseInt(parts[2]);
                if (version < 1) {
                    throw new NumberFormatException();
                }
                return new CustomReference(UUID.fromString(parts[1]), version);
            } catch (IllegalArgumentException e) {
                throw new InvalidResponseQueryException("templateVersion must be default or custom:<uuid>:<version>");
            }
        }
    }
}
