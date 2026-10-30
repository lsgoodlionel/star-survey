package cn.mjy.platform.response;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.io.IOException;
import java.net.URI;
import java.util.List;
import java.util.UUID;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/v1/surveys/{surveyId}/word-templates")
class WordTemplateController {

    private final WordTemplateService templates;
    private final CurrentTenant currentTenant;

    WordTemplateController(WordTemplateService templates, CurrentTenant currentTenant) {
        this.templates = templates;
        this.currentTenant = currentTenant;
    }

    @PostMapping(consumes = "multipart/form-data")
    ResponseEntity<WordTemplateView> create(@PathVariable UUID surveyId, @RequestParam String name,
            @RequestParam("file") MultipartFile file) throws IOException {
        WordTemplateView created = templates.create(currentTenant.require(), surveyId, name, file.getBytes());
        return ResponseEntity.created(URI.create("/v1/surveys/" + surveyId + "/word-templates/" + created.id()))
                .cacheControl(CacheControl.noStore()).body(created);
    }

    @PostMapping(value = "/{id}/versions", consumes = "multipart/form-data")
    ResponseEntity<WordTemplateView> addVersion(@PathVariable UUID surveyId, @PathVariable UUID id,
            @RequestParam("file") MultipartFile file) throws IOException {
        return ResponseEntity.status(201).cacheControl(CacheControl.noStore())
                .body(templates.addVersion(currentTenant.require(), surveyId, id, file.getBytes()));
    }

    @GetMapping
    ResponseEntity<List<WordTemplateView>> list(@PathVariable UUID surveyId,
            @RequestParam(defaultValue = "100") int limit, @RequestParam(defaultValue = "0") int offset) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .body(templates.list(currentTenant.require(), surveyId, limit, offset));
    }

    @GetMapping("/{id}")
    ResponseEntity<WordTemplateView> get(@PathVariable UUID surveyId, @PathVariable UUID id) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .body(templates.get(currentTenant.require(), surveyId, id, null));
    }

    @GetMapping("/{id}/versions/{version}")
    ResponseEntity<WordTemplateView> version(@PathVariable UUID surveyId, @PathVariable UUID id,
            @PathVariable int version) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .body(templates.get(currentTenant.require(), surveyId, id, version));
    }
}
