package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryLinkRepository.LinkRow;
import cn.mjy.platform.delivery.DeliveryLinkRepository.ShortLinkTarget;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.SignedParameters;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 投放链接（R05-01、R05-07、R05-08）：作答地址、签名业务参数、短链、二维码与内嵌代码。
 *
 * <p>签名参数在建链接时一次算好并落库：此后任何人改其中一项（包括改 exp），验签都不成立。
 * 参数里可以带渠道来源、部门、预填字段等；平台不解释它们的含义，只保证不可篡改。
 *
 * <p>地址本身不落库，每次现算（见 {@link RespondentLinks}）。
 */
@Service
public class DeliveryLinkService {

    /** 短链码重复时的重试次数；13 位码约 64 位熵，一次就撞上的概率可以忽略，留三次只为"绝不返回失败"。 */
    private static final int SHORT_CODE_ATTEMPTS = 3;
    static final String SHORT_LINK_PATH = "/d/s/";
    private static final int MAX_LINKS_PER_PAGE = 200;

    private final TenantScope tenantScope;
    private final DeliveryLinkRepository links;
    private final RespondentLinks respondentLinks;
    private final DeliveryAccess access;
    private final DeliveryKeys keys;
    private final DeliveryAudit audit;
    private final DeliveryProperties properties;
    private final Clock clock;

    DeliveryLinkService(TenantScope tenantScope, DeliveryLinkRepository links, RespondentLinks respondentLinks,
            DeliveryAccess access, DeliveryKeys keys, DeliveryAudit audit, DeliveryProperties properties) {
        this.tenantScope = tenantScope;
        this.links = links;
        this.respondentLinks = respondentLinks;
        this.access = access;
        this.keys = keys;
        this.audit = audit;
        this.properties = properties;
        this.clock = Clock.systemUTC();
    }

    /** 建链接的请求；params 是业务参数（不含 exp/sig），ttl 为空时用配置的默认有效期。 */
    public record NewLink(String label, Map<String, String> params, Duration ttl, boolean shortLink) {

        public NewLink {
            params = params == null ? Map.of() : Map.copyOf(params);
        }
    }

    public LinkView create(TenantContext ctx, UUID surveyId, NewLink request) {
        access.require(ctx, DeliveryAccess.SEND, surveyId);
        String label = requireLabel(request.label());
        Instant expiresAt = clock.instant().plus(request.ttl() == null ? properties.linkTtl() : request.ttl());
        Map<String, String> signed = SignedParameters.sign(
                keys.linkKey(ctx.tenantId()), signingContext(surveyId), request.params(), expiresAt);
        UUID id = UUID.randomUUID();
        return tenantScope.call(ctx.tenantId(), () -> {
            // 先确认问卷已发布：没有作答地址的链接对用户毫无用处，不如当场 409。
            if (!respondentLinks.isPublished(ctx.tenantId(), surveyId)) {
                throw DeliveryExceptions.conflict("survey_not_published",
                        "survey has no live respondent route: " + surveyId);
            }
            links.insert(ctx.tenantId(), id, surveyId, label, signed,
                    expiresAt.atOffset(ZoneOffset.UTC), ctx.actorId());
            if (request.shortLink()) {
                assignShortCode(ctx.tenantId(), id);
            }
            audit.record(ctx, DeliveryAudit.LINK_CREATE, "link/" + id + " survey/" + surveyId + " label=" + label);
            return view(ctx.tenantId(), links.find(id).orElseThrow());
        });
    }

    public LinkView find(TenantContext ctx, UUID linkId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            LinkRow row = row(linkId);
            access.require(ctx, DeliveryAccess.READ, row.surveyId());
            return view(ctx.tenantId(), row);
        });
    }

    public List<LinkView> list(TenantContext ctx, UUID surveyId) {
        access.require(ctx, DeliveryAccess.READ, surveyId);
        return tenantScope.call(ctx.tenantId(), () -> links.listBySurvey(surveyId, MAX_LINKS_PER_PAGE).stream()
                .map(row -> view(ctx.tenantId(), row))
                .toList());
    }

    /** 作废链接：短链与二维码随即失效（R04-06"分享链接失效后不可访问"）。幂等。 */
    public LinkView revoke(TenantContext ctx, UUID linkId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            LinkRow row = row(linkId);
            access.require(ctx, DeliveryAccess.SEND, row.surveyId());
            if (links.revoke(linkId)) {
                audit.record(ctx, DeliveryAudit.LINK_REVOKE, "link/" + linkId);
            }
            return view(ctx.tenantId(), links.find(linkId).orElseThrow());
        });
    }

    /** 二维码：直接编码作答地址（含签名参数）。 */
    public QrCode qrCode(TenantContext ctx, UUID linkId) {
        return QrCode.encodeText(find(ctx, linkId).url());
    }

    /** 内嵌代码与配套的部署提示。 */
    public Embed embed(TenantContext ctx, UUID linkId, int width, int height) {
        LinkView link = find(ctx, linkId);
        String origin = origin(link.url());
        return new Embed(EmbedSnippet.iframe(link.url(), width, height, "问卷"),
                EmbedSnippet.deploymentNotes(origin));
    }

    /** 内嵌代码及其部署提示。 */
    public record Embed(String snippet, List<String> notes) {

        public Embed {
            notes = List.copyOf(notes);
        }
    }

    /**
     * 短链解析（匿名调用）。短链码形状不对时根本不查库；查不到、已作废、已过期一律同一个 404，
     * 调用方分辨不出"不存在"与"已失效"，因此枚举不出任何信息。
     */
    public String resolveShortLink(String code) {
        if (!ShortCodes.isWellFormed(code)) {
            throw DeliveryExceptions.notFound("short link not found");
        }
        ShortLinkTarget target = links.resolveShortCode(code)
                .orElseThrow(() -> DeliveryExceptions.notFound("short link not found"));
        return tenantScope.call(target.tenant(), () -> {
            LinkRow row = links.find(target.linkId())
                    .orElseThrow(() -> DeliveryExceptions.notFound("short link not found"));
            LinkView link = view(target.tenant(), row);
            if (!link.isActive(OffsetDateTime.now(clock))) {
                throw DeliveryExceptions.notFound("short link not found");
            }
            return link.url();
        });
    }

    private void assignShortCode(TenantId tenant, UUID linkId) {
        for (int attempt = 0; attempt < SHORT_CODE_ATTEMPTS; attempt++) {
            if (links.registerShortCode(tenant, linkId, ShortCodes.random())) {
                return;
            }
        }
        throw new DeliveryUnavailableException("could not allocate a free short link code");
    }

    private LinkRow row(UUID linkId) {
        return links.find(linkId).orElseThrow(() -> DeliveryExceptions.notFound("link not found: " + linkId));
    }

    private LinkView view(TenantId tenant, LinkRow row) {
        String url = respondentLinks.respondentUrl(tenant, row.surveyId(), row.signedParams());
        String shortUrl = row.shortCode() == null
                ? null
                : properties.requirePublicBaseUrl() + SHORT_LINK_PATH + row.shortCode();
        return new LinkView(row.id(), row.surveyId(), row.label(), row.signedParams(), url, shortUrl,
                row.expiresAt(), row.revokedAt(), row.createdAt());
    }

    /** 签名上下文带问卷公开 UUID：一份问卷的签名参数挪到另一份问卷上一定验不过。 */
    static String signingContext(UUID surveyId) {
        return "link/v1/" + surveyId;
    }

    /** 收件人专属参数（邀请码与应答者标识）也要签名，否则改一下就能冒充别人作答。 */
    Map<String, String> signRecipientParams(TenantId tenant, UUID surveyId, Map<String, String> base,
            Map<String, String> extra, Instant expiresAt) {
        Map<String, String> merged = new TreeMap<>();
        base.forEach((name, value) -> {
            if (!SignedParameters.isReserved(name)) {
                merged.put(name, value);
            }
        });
        merged.putAll(extra);
        return SignedParameters.sign(keys.linkKey(tenant), signingContext(surveyId), merged, expiresAt);
    }

    String respondentUrl(TenantId tenant, UUID surveyId, Map<String, String> query) {
        return respondentLinks.respondentUrl(tenant, surveyId, query);
    }

    Optional<LinkRow> rowInScope(UUID linkId) {
        return links.find(linkId);
    }

    private static String requireLabel(String label) {
        if (label == null || label.isBlank() || label.length() > 64) {
            throw DeliveryExceptions.invalid("label must be 1 to 64 characters");
        }
        return label;
    }

    private static String origin(String url) {
        int schemeEnd = url.indexOf("://");
        int pathStart = schemeEnd < 0 ? -1 : url.indexOf('/', schemeEnd + 3);
        return pathStart < 0 ? url : url.substring(0, pathStart);
    }
}
