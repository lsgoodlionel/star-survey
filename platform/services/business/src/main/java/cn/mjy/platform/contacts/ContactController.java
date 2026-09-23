package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.net.URI;
import java.util.List;
import java.util.UUID;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 联系人接口。含个人信息，响应一律 no-store。 */
@RestController
@RequestMapping("/v1/contacts")
class ContactController {

    /** 新建 / 更新联系人的请求体。字段名与导入列名一致。 */
    record ContactBody(UUID listId, String kind, String displayName, String email, String phone,
            String employeeId, String provider, String appId, String externalId, UUID orgUnitId) {

        ContactDraft toDraft() {
            ContactDraft draft = ContactDraft.named(displayName).withEmail(email).withPhone(phone)
                    .withEmployeeId(employeeId).withUnit(orgUnitId);
            if (provider != null || appId != null || externalId != null) {
                if (provider == null || appId == null || externalId == null) {
                    throw new InvalidContactRequestException(
                            "provider, appId and externalId must be given together");
                }
                draft = draft.withIdentity(new ExternalContactIdentity(provider, appId, externalId));
            }
            return draft;
        }
    }

    record LinkBody(UUID contactId, String reason) {
    }

    record TagBody(UUID tagId) {
    }

    private final CurrentTenant currentTenant;
    private final ContactDirectoryService contacts;
    private final ContactTagService tags;

    ContactController(CurrentTenant currentTenant, ContactDirectoryService contacts, ContactTagService tags) {
        this.currentTenant = currentTenant;
        this.contacts = contacts;
        this.tags = tags;
    }

    @PostMapping
    ResponseEntity<ContactView> create(@RequestBody ContactBody body) {
        ContactKind kind = ContactKind.fromCode(body.kind())
                .orElseThrow(() -> new InvalidContactRequestException("kind must be one of staff, org_member,"
                        + " respondent"));
        ContactView created = contacts.create(currentTenant.require(), body.listId(), kind, body.toDraft());
        return ResponseEntity.created(URI.create("/v1/contacts/" + created.id()))
                .cacheControl(CacheControl.noStore()).body(created);
    }

    @GetMapping
    ResponseEntity<ContactPage> search(@RequestParam(required = false) UUID listId,
            @RequestParam(required = false) UUID orgUnitId,
            @RequestParam(required = false, defaultValue = "true") boolean includeDescendants,
            @RequestParam(required = false) String tag,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit) {
        ContactQuery query = new ContactQuery(listId, orgUnitId, includeDescendants, tag, cursor, limit);
        return noStore(contacts.search(currentTenant.require(), query));
    }

    @GetMapping("/{id}")
    ResponseEntity<ContactView> get(@PathVariable UUID id) {
        return noStore(contacts.get(currentTenant.require(), id));
    }

    @PatchMapping("/{id}")
    ResponseEntity<ContactView> update(@PathVariable UUID id, @RequestBody ContactBody body) {
        return noStore(contacts.update(currentTenant.require(), id, body.toDraft()));
    }

    @PostMapping("/{id}/disable")
    ResponseEntity<ContactView> disable(@PathVariable UUID id) {
        return noStore(contacts.disable(currentTenant.require(), id));
    }

    @GetMapping("/{id}/links")
    ResponseEntity<List<ContactLinkView>> links(@PathVariable UUID id) {
        return noStore(contacts.links(currentTenant.require(), id));
    }

    @PostMapping("/{id}/links")
    ResponseEntity<ContactLinkView> link(@PathVariable UUID id, @RequestBody LinkBody body) {
        ContactLinkView link = contacts.link(currentTenant.require(), id, body.contactId(), body.reason());
        return ResponseEntity.created(URI.create("/v1/contact-links/" + link.id()))
                .cacheControl(CacheControl.noStore()).body(link);
    }

    @PostMapping("/{id}/tags")
    ResponseEntity<Void> assignTag(@PathVariable UUID id, @RequestBody TagBody body) {
        tags.assign(currentTenant.require(), id, body.tagId());
        return ResponseEntity.noContent().build();
    }

    @DeleteMapping("/{id}/tags/{tagId}")
    ResponseEntity<Void> unassignTag(@PathVariable UUID id, @PathVariable UUID tagId) {
        tags.unassign(currentTenant.require(), id, tagId);
        return ResponseEntity.noContent().build();
    }

    private static <T> ResponseEntity<T> noStore(T body) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(body);
    }
}
