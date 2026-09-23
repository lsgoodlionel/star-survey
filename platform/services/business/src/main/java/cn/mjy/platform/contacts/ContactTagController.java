package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.net.URI;
import java.util.List;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 标签接口：每租户一套，同名（不分大小写）重复创建返回原标签。 */
@RestController
@RequestMapping("/v1/contact-tags")
class ContactTagController {

    record TagBody(String name) {
    }

    private final CurrentTenant currentTenant;
    private final ContactTagService tags;

    ContactTagController(CurrentTenant currentTenant, ContactTagService tags) {
        this.currentTenant = currentTenant;
        this.tags = tags;
    }

    @PostMapping
    ResponseEntity<ContactTagView> create(@RequestBody TagBody body) {
        ContactTagView created = tags.create(currentTenant.require(), body.name());
        return ResponseEntity.created(URI.create("/v1/contact-tags/" + created.id())).body(created);
    }

    @GetMapping
    List<ContactTagView> list() {
        return tags.list(currentTenant.require());
    }
}
