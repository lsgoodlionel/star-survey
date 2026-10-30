package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.Map;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.PostMapping;

@RestController
@RequestMapping("/v1/delivery/email-settings")
final class EmailSettingsController {
    private final CurrentTenant currentTenant; private final EmailSettingsService service;
    EmailSettingsController(CurrentTenant currentTenant, EmailSettingsService service) { this.currentTenant = currentTenant; this.service = service; }
    @GetMapping EmailSettingsService.View view() { return service.view(currentTenant.require()); }
    @PutMapping EmailSettingsService.View save(@RequestBody Map<String, String> body) {
        return service.save(currentTenant.require(), new EmailSettingsService.Command(body.get("provider"), body.get("authMode"), body.get("fromAddress"), body.get("username"), body.get("credential")));
    }
    @DeleteMapping EmailSettingsService.View clear() { TenantContext actor = currentTenant.require(); service.clear(actor); return service.view(actor); }
    @PostMapping("/verify") EmailSettingsService.VerifyResult verify(@RequestBody Map<String, String> body) {
        return service.verify(currentTenant.require(), new EmailSettingsService.Command(body.get("provider"), body.get("authMode"), body.get("fromAddress"), body.get("username"), body.get("credential")));
    }
    @PostMapping("/test-send") EmailSettingsService.TestSendResult sendTest(@RequestBody Map<String, String> body) {
        return service.sendTest(currentTenant.require(), new EmailSettingsService.TestSendCommand(
                body.get("recipient"), body.get("subject"), body.get("body")));
    }
}
