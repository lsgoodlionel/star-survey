package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.List;
import java.util.UUID;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 导入作业的进度与逐行结果。结果里没有行内容，可以安全展示给运营。 */
@RestController
@RequestMapping("/v1/contact-imports")
class ContactImportController {

    private static final int DEFAULT_PAGE = 100;

    private final CurrentTenant currentTenant;
    private final ContactImportService imports;

    ContactImportController(CurrentTenant currentTenant, ContactImportService imports) {
        this.currentTenant = currentTenant;
        this.imports = imports;
    }

    @GetMapping("/{jobId}")
    ImportJobView status(@PathVariable UUID jobId) {
        return imports.status(currentTenant.require(), jobId);
    }

    @GetMapping("/{jobId}/outcomes")
    List<ImportRowOutcome> outcomes(@PathVariable UUID jobId,
            @RequestParam(required = false, defaultValue = "1") int fromRow,
            @RequestParam(required = false, defaultValue = "" + DEFAULT_PAGE) int limit) {
        return imports.outcomes(currentTenant.require(), jobId, fromRow, limit);
    }
}
