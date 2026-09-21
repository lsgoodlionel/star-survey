package cn.mjy.platform.shared.security;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.support.TestTokens;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;

/**
 * 框架层错误（如 415）由容器转发到 /error。若 /error 也要求令牌，这次内部转发会被当成未认证，
 * 真实错误被掩盖成 401，调用方无从排查。放行错误转发，但不放宽任何业务接口。
 *
 * <p>必须用真实端口：MockMvc 不执行容器的错误转发，测不出这个问题。
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ErrorDispatchTest {

    private final HttpClient http = HttpClient.newHttpClient();

    @LocalServerPort
    private int port;

    @Autowired
    private TestTokens tokens;

    @Test
    void anAuthenticatedRequestKeepsItsRealErrorStatus() throws Exception {
        String token = tokens.issue("user-1", UUID.randomUUID(), List.of());

        int status = send(post("/v1/identity-bindings", "text/plain", "not json")
                .header("Authorization", "Bearer " + token));

        assertThat(status).isEqualTo(415);
    }

    @Test
    void businessEndpointsStillRequireAToken() throws Exception {
        assertThat(send(HttpRequest.newBuilder(uri("/v1/me")).GET())).isEqualTo(401);
        assertThat(send(post("/v1/identity-bindings", "text/plain", "x"))).isEqualTo(401);
    }

    @Test
    void theErrorPageItselfDoesNotLeakBusinessData() throws Exception {
        assertThat(send(HttpRequest.newBuilder(uri("/error")).GET())).isNotEqualTo(200);
    }

    private HttpRequest.Builder post(String path, String contentType, String body) {
        return HttpRequest.newBuilder(uri(path))
                .header("Content-Type", contentType)
                .POST(HttpRequest.BodyPublishers.ofString(body));
    }

    private URI uri(String path) {
        return URI.create("http://localhost:" + port + path);
    }

    private int send(HttpRequest.Builder request) throws Exception {
        return http.send(request.build(), HttpResponse.BodyHandlers.discarding()).statusCode();
    }
}
