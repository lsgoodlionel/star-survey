package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.net.URI;
import java.util.List;
import java.util.UUID;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 联系人名单与导入提交。 */
@RestController
@RequestMapping("/v1/contact-lists")
class ContactListController {

    static final String IDEMPOTENCY_HEADER = "Idempotency-Key";

    record ListBody(String name, String kind, String dedupeKey) {
    }

    record ImportBody(String format, String content) {
    }

    private final CurrentTenant currentTenant;
    private final ContactListService lists;
    private final ContactImportService imports;

    ContactListController(CurrentTenant currentTenant, ContactListService lists, ContactImportService imports) {
        this.currentTenant = currentTenant;
        this.lists = lists;
        this.imports = imports;
    }

    @PostMapping
    ResponseEntity<ContactListView> create(@RequestBody ListBody body) {
        ContactKind kind = ContactKind.fromCode(body.kind())
                .orElseThrow(() -> new InvalidContactRequestException("kind must be one of staff, org_member,"
                        + " respondent"));
        DedupeKey key = DedupeKey.fromCode(body.dedupeKey())
                .orElseThrow(() -> new InvalidContactRequestException("dedupeKey must be one of email, phone,"
                        + " employee_id, external_id"));
        ContactListView created = lists.create(currentTenant.require(), body.name(), kind, key);
        return ResponseEntity.created(URI.create("/v1/contact-lists/" + created.id())).body(created);
    }

    @GetMapping
    List<ContactListView> list() {
        return lists.list(currentTenant.require());
    }

    @GetMapping("/{id}")
    ContactListView get(@PathVariable UUID id) {
        return lists.get(currentTenant.require(), id);
    }

    @PostMapping("/{id}/imports")
    ResponseEntity<ImportJobView> submit(@PathVariable UUID id, @RequestBody ImportBody body,
            @RequestHeader(name = IDEMPOTENCY_HEADER, required = false) String idempotencyKey) {
        ImportJobView job = imports.submit(currentTenant.require(), id,
                new ImportRequest(body.format(), body.content()), idempotencyKey);
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .location(URI.create("/v1/contact-imports/" + job.jobId()))
                .cacheControl(CacheControl.noStore()).body(job);
    }
}
