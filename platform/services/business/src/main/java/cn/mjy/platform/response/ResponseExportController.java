package cn.mjy.platform.response;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.net.URI;
import java.util.UUID;
import org.springframework.core.io.InputStreamResource;
import org.springframework.core.io.Resource;
import org.springframework.http.CacheControl;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/**
 * 答卷导出接口（蓝图 {@code POST /v1/exports}，ADR 0015）。创建返回 202 与作业号，导出在后台进行；
 * 下载每次再次授权。所有响应 {@code Cache-Control: no-store}。
 */
@RestController
public class ResponseExportController {

    static final String IDEMPOTENCY_HEADER = "Idempotency-Key";

    private final ResponseExportService exports;
    private final CurrentTenant currentTenant;

    public ResponseExportController(ResponseExportService exports, CurrentTenant currentTenant) {
        this.exports = exports;
        this.currentTenant = currentTenant;
    }

    @PostMapping("/v1/surveys/{id}/exports")
    public ResponseEntity<ExportJobView> create(@PathVariable UUID id, @RequestBody ExportRequest request,
            @RequestHeader(name = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ExportJobView job = exports.create(currentTenant.require(), id, request, idempotencyKey);
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .location(URI.create("/v1/exports/" + job.jobId()))
                .cacheControl(CacheControl.noStore())
                .body(job);
    }

    /** 蓝图写法：问卷放在请求体里。 */
    @PostMapping("/v1/exports")
    public ResponseEntity<ExportJobView> createFromBody(@RequestBody ExportRequest request,
            @RequestHeader(name = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        if (request == null || request.surveyId() == null) {
            throw new InvalidResponseQueryException("surveyId is required");
        }
        return create(request.surveyId(), request, idempotencyKey);
    }

    @GetMapping("/v1/exports/{jobId}")
    public ResponseEntity<ExportJobView> status(@PathVariable UUID jobId) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .body(exports.status(currentTenant.require(), jobId));
    }

    @PostMapping("/v1/exports/{jobId}/cancel")
    public ResponseEntity<ExportJobView> cancel(@PathVariable UUID jobId) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .body(exports.cancel(currentTenant.require(), jobId));
    }

    @GetMapping("/v1/exports/{jobId}/download")
    public ResponseEntity<Resource> download(@PathVariable UUID jobId) {
        ExportDownload file = exports.download(currentTenant.require(), jobId);
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .contentType(MediaType.parseMediaType(file.contentType()))
                .contentLength(file.size())
                .header("Content-Disposition", ContentDisposition.attachment().filename(file.fileName()).build()
                        .toString())
                .header("X-Content-Type-Options", "nosniff")
                .header("X-Content-SHA256", file.sha256())
                .body(new InputStreamResource(file.content()));
    }
}
