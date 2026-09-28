package cn.mjy.platform.response;

import cn.mjy.platform.response.ExportPlan.PlannedSource;
import cn.mjy.platform.response.ResponseAttachmentSource.Outcome;
import cn.mjy.platform.shared.TenantId;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 取件阶段（ADR 0015 增补四）：把一批答卷的附件字节逐份取回导出存储。只有附件包格式会走到这里。
 *
 * <p><b>内存有界</b>：一份附件一次，流式写进存储的一次上传，任何时刻只持有一个传输缓冲区——
 * 与一份答卷有多少附件、单份多大都无关。这正是 RemoteControl 的 {@code get_uploaded_files}
 * 做不到的事：它把整份答卷的全部文件 base64 塞进一个 JSON 应答。
 *
 * <p><b>失败项可重试</b>：每一份附件是独立的一件事。
 *
 * <ul>
 *   <li>已经有结论（字节在，或墓碑在）的那一份直接跳过——重跑一批不会重取已经取到的；</li>
 *   <li>引擎里已经没有、或超出单份上限的那一份留一个墓碑，<b>同一批里其余的照常继续</b>；</li>
 *   <li>暂时性失败（网关不可达）抛出，检查点不前移，整批稍后重来——重来时只补没取到的那些。</li>
 * </ul>
 *
 * <p>暂时性失败<b>绝不</b>被记成缺失：墓碑只由前两种永久结论产生。
 */
@Component
class ExportAttachmentFetcher {

    private static final Logger log = LoggerFactory.getLogger(ExportAttachmentFetcher.class);

    private final ExportFileStore files;
    private final ResponseAttachmentSource source;
    private final ResponseExportProperties properties;

    ExportAttachmentFetcher(ExportFileStore files, ResponseAttachmentSource source,
            ResponseExportProperties properties) {
        this.files = files;
        this.source = source;
        this.properties = properties;
    }

    /** @return 本次真正取回字节的份数（跳过的与判定缺失的不计） */
    int fetch(TenantId tenant, UUID jobId, ExportPlan plan, ExportRowEncoder.Encoded encoded) {
        ExportAttachments attachments = new ExportAttachments(files, ResponseExportWorker.jobPrefix(tenant, jobId));
        int stored = 0;
        try {
            for (ExportRecord record : encoded.records()) {
                for (List<String> row : record.attachments()) {
                    stored += fetchOne(attachments, plan, ExportAttachmentRef.of(row)) ? 1 : 0;
                }
            }
        } catch (IOException e) {
            throw new UncheckedIOException("could not store an export attachment", e);
        }
        return stored;
    }

    private boolean fetchOne(ExportAttachments attachments, ExportPlan plan, ExportAttachmentRef ref)
            throws IOException {
        if (attachments.resolved(ref)) {
            return false;
        }
        Outcome outcome;
        try (ExportFileStore.Upload upload = files.create(attachments.key(ref))) {
            outcome = source.fetch(query(plan, ref), upload.stream());
            if (outcome == Outcome.STORED) {
                upload.commit();
                return true;
            }
            // 没提交就关闭 ＝ 放弃：存储里不会留下半截文件。
        }
        attachments.markAbsent(ref, reason(outcome));
        log.info("export attachment absent: sid {} response {} column {} #{}: {}", ref.engineSid(), ref.responseId(),
                ref.fieldname(), ref.index(), reason(outcome));
        return false;
    }

    private AttachmentQuery query(ExportPlan plan, ExportAttachmentRef ref) {
        PlannedSource source = plan.source(ref.version());
        return new AttachmentQuery(source.engineInstanceId(), source.engineSid(), source.latestGeneration(),
                ref.responseId(), ref.fieldname(), ref.storedName(), properties.attachmentMaxBytes());
    }

    private static String reason(Outcome outcome) {
        return outcome == Outcome.TOO_LARGE ? ExportAttachments.REASON_TOO_LARGE
                : ExportAttachments.REASON_NOT_FOUND;
    }
}
