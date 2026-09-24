package cn.mjy.platform.response;

import cn.mjy.platform.survey.gateway.PublishGatewaySigner;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 契约 response-read-v1「附件取件」的 HTTP 实现：{@code POST <网关>/v1/responses/attachment}。
 *
 * <p>与作答读取共用网关地址与共享密钥，签名规则相同。<b>应答体是字节流</b>，本类把它直接倒进
 * 调用方给的 sink，不在内存里攒整份文件——"内存有界"在这一层的落法。
 *
 * <p>状态码的解读是**永久与暂时的分界**，不能含糊：
 *
 * <table>
 *   <tr><td>200</td><td>字节流</td><td>{@link Outcome#STORED}</td></tr>
 *   <tr><td>404</td><td>引擎里已经没有这一份</td><td>{@link Outcome#NOT_FOUND}，不重试</td></tr>
 *   <tr><td>413</td><td>超出单份上限</td><td>{@link Outcome#TOO_LARGE}，不重试</td></tr>
 *   <tr><td>其余</td><td>网关或引擎故障</td><td>抛出，作业退避后重试</td></tr>
 * </table>
 *
 * <p>附件内容是个人数据：本类只记录状态码与自然键，从不记录文件名之外的任何内容，也从不记录字节。
 */
@Component
public class HttpResponseAttachmentSource implements ResponseAttachmentSource {

    static final String ATTACHMENT_PATH = "/v1/responses/attachment";
    private static final int COPY_BUFFER_BYTES = 64 * 1024;
    private static final int HTTP_NOT_FOUND = 404;
    private static final int HTTP_TOO_LARGE = 413;
    /** 整趟传输的上限 ＝ 单次请求超时的几倍：大文件允许慢，但不允许无限慢。 */
    private static final int TRANSFER_DEADLINE_FACTOR = 10;

    private static final Logger log = LoggerFactory.getLogger(HttpResponseAttachmentSource.class);

    private final URI uri;
    private final byte[] secret;
    private final Duration timeout;
    /** 整趟传输的绝对上限；请求超时只管应答头到达之前那一段。 */
    private final Duration transferDeadline;
    private final Clock clock;
    private final JsonMapper json;
    private final HttpClient http;

    @Autowired
    public HttpResponseAttachmentSource(@Value("${platform.pubgw.url:}") String url,
            @Value("${platform.pubgw.secret:}") String secret,
            @Value("${platform.pubgw.attachment-timeout-seconds:120}") long timeoutSeconds,
            JsonMapper json) {
        // 与作答读取共用同一份地址解析：只认 http(s)，别的（或空）一律当成"没配"。
        this(HttpResponseAnswerSource.parseBase(url), secret, Duration.ofSeconds(timeoutSeconds),
                Clock.systemUTC(), json);
    }

    HttpResponseAttachmentSource(URI baseUrl, String secret, Duration timeout, Clock clock, JsonMapper json) {
        this.uri = baseUrl == null ? null : baseUrl.resolve(ATTACHMENT_PATH);
        this.secret = secret == null ? new byte[0] : secret.getBytes(StandardCharsets.UTF_8);
        this.timeout = timeout;
        this.transferDeadline = timeout.multipliedBy(TRANSFER_DEADLINE_FACTOR);
        this.clock = clock;
        this.json = json;
        this.http = HttpClient.newBuilder().connectTimeout(timeout).build();
    }

    public boolean isConfigured() {
        return uri != null && secret.length >= HttpResponseAnswerSource.MIN_SECRET_BYTES;
    }

    @Override
    public Outcome fetch(AttachmentQuery query, OutputStream sink) {
        if (!isConfigured()) {
            throw new ResponseAttachmentsUnavailableException("publish gateway is not configured");
        }
        HttpResponse<InputStream> response = send(body(query));
        try (InputStream content = response.body()) {
            return switch (response.statusCode()) {
                case 200 -> copy(query, content, sink);
                case HTTP_NOT_FOUND -> Outcome.NOT_FOUND;
                case HTTP_TOO_LARGE -> Outcome.TOO_LARGE;
                default -> throw refused(query, response.statusCode());
            };
        } catch (IOException e) {
            throw new ResponseAttachmentsUnavailableException(
                    "attachment stream broke: " + e.getClass().getSimpleName());
        }
    }

    /**
     * 倒字节，同时自己也数一遍上限。网关本该先挡住超限的那一份（413），数到超出说明网关没按契约办事——
     * 那是**不可信**，按暂时失败抛出，绝不当成"这一份太大"悄悄放过。此时一个字节都没有提交
     * （上传未 commit），存储里不会留半截文件。
     */
    private Outcome copy(AttachmentQuery query, InputStream content, OutputStream sink) throws IOException {
        byte[] buffer = new byte[COPY_BUFFER_BYTES];
        long deadline = System.nanoTime() + transferDeadline.toNanos();
        long written = 0;
        int read;
        while ((read = content.read(buffer)) >= 0) {
            written += read;
            if (written > query.maxBytes()) {
                throw new ResponseAttachmentsUnavailableException(
                        "gateway streamed more than the agreed limit for sid " + query.engineSid());
            }
            // 请求超时只管「应答头什么时候到」，管不住之后每一次 read()：
            // 一条每次都卡在超时线下一点的慢速流能把这个线程永远占住。所以整趟传输另有一个
            // 绝对截止时间——附件是有上限的，一次合法传输本来就该在里面做完。
            if (System.nanoTime() - deadline > 0) {
                throw new ResponseAttachmentsUnavailableException(
                        "gateway took longer than " + transferDeadline + " to stream one attachment");
            }
            sink.write(buffer, 0, read);
        }
        return Outcome.STORED;
    }

    private byte[] body(AttachmentQuery query) {
        ObjectNode envelope = json.createObjectNode();
        envelope.put("engineInstanceId", query.engineInstanceId());
        envelope.put("surveyId", query.engineSid());
        envelope.put("generation", query.generation());
        envelope.put("responseId", query.responseId());
        envelope.put("field", query.fieldname());
        envelope.put("storedName", query.storedName());
        envelope.put("maxBytes", query.maxBytes());
        return json.writeValueAsBytes(envelope);
    }

    private HttpResponse<InputStream> send(byte[] body) {
        String timestamp = Long.toString(clock.instant().getEpochSecond());
        HttpRequest request = HttpRequest.newBuilder(uri)
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .header("X-Pubgw-Timestamp", timestamp)
                .header("X-Pubgw-Signature", PublishGatewaySigner.sign(secret, timestamp, body))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofInputStream());
        } catch (HttpTimeoutException e) {
            throw new ResponseAttachmentsUnavailableException("gateway timed out after " + timeout);
        } catch (IOException e) {
            throw new ResponseAttachmentsUnavailableException(
                    "gateway network error: " + e.getClass().getSimpleName());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ResponseAttachmentsUnavailableException("interrupted while fetching an attachment");
        }
    }

    private ResponseAttachmentsUnavailableException refused(AttachmentQuery query, int status) {
        log.warn("attachment fetch on {} sid {} response {} refused with http {}", query.engineInstanceId(),
                query.engineSid(), query.responseId(), status);
        return new ResponseAttachmentsUnavailableException("gateway returned http " + status);
    }
}
