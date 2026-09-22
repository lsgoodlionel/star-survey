package cn.mjy.platform.identity.org;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.parsers.ParserConfigurationException;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.w3c.dom.Document;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;
import org.xml.sax.SAXException;

/**
 * 企业微信自建应用"接收事件服务器"（通讯录变更 change_contact）。文档：
 * <ul>
 *   <li>加解密方案 https://developer.work.weixin.qq.com/document/path/90968</li>
 *   <li>成员变更事件 https://developer.work.weixin.qq.com/document/path/90970</li>
 * </ul>
 * <ul>
 *   <li>URL 校验：{@code GET ?msg_signature&timestamp&nonce&echostr}，签名覆盖 echostr，解密后原样返回明文；</li>
 *   <li>事件：{@code POST ?msg_signature&timestamp&nonce}，XML 体 {@code <Encrypt>}，签名覆盖 Encrypt；
 *       解密后 receiveid 必须等于连接的 corpid；应答 200 空包。</li>
 * </ul>
 * 离职：{@code ChangeType=delete_user}，或 {@code update_user} 且 {@code Status} 为 2（禁用）/ 5（退出企业），
 * 与拉取同步对 user/get status 的判定一致。XML 解析禁用 DTD 与外部实体。
 */
@Component
class WeComEventReceiver implements OrgEventReceiver {

    private static final Set<String> DEPARTED_STATUS = Set.of("2", "5");
    private static final Reply EMPTY = new Reply(MediaType.TEXT_PLAIN, "");

    @Override
    public OrgProvider provider() {
        return OrgProvider.WECOM;
    }

    @Override
    public Verified receive(OrgConnection connection, Secrets secrets, Request request, Duration maxSkew) {
        String signature = request.param("msg_signature");
        String timestamp = request.param("timestamp");
        String nonce = request.param("nonce");
        if (nonce == null) {
            throw OrgEventException.unauthenticated("event nonce is missing");
        }
        boolean handshake = "GET".equals(request.method());
        String encrypted = handshake ? request.param("echostr") : encryptedField(request.body());
        if (encrypted == null) {
            throw OrgEventException.unauthenticated("event payload is missing");
        }
        String expected = OrgEventCrypto.sortedSha1(secrets.token(), timestamp == null ? "" : timestamp, nonce,
                encrypted);
        if (!OrgEventCrypto.signatureMatches(expected, signature)) {
            throw OrgEventException.unauthenticated("event signature does not match");
        }
        OrgEventReceiver.requireFresh(Instant.ofEpochSecond(OrgEventReceiver.parseTimestamp(timestamp)),
                request.receivedAt(), maxSkew);
        OrgEventCrypto.Envelope envelope = OrgEventCrypto.openWxBiz(secrets.key(), encrypted);
        if (!connection.corpId().equals(envelope.receiver())) {
            throw OrgEventException.unauthenticated("event was sealed for another organisation");
        }
        if (handshake) {
            return Verified.handshake(new Reply(MediaType.TEXT_PLAIN, envelope.message()));
        }
        return parseEvent(connection, envelope.message());
    }

    private Verified parseEvent(OrgConnection connection, String message) {
        Document xml = parse(message.getBytes(StandardCharsets.UTF_8));
        String to = text(xml, "ToUserName");
        if (to != null && !connection.corpId().equals(to)) {
            throw OrgEventException.unauthenticated("event addresses another organisation");
        }
        String event = text(xml, "Event");
        String change = text(xml, "ChangeType");
        String type = OrgEventReceiver.safeType(event == null ? text(xml, "MsgType")
                : change == null ? event : event + ":" + change);
        String userId = text(xml, "UserID");
        boolean departed = "change_contact".equals(event) && userId != null && !userId.isBlank()
                && ("delete_user".equals(change)
                        || "update_user".equals(change) && DEPARTED_STATUS.contains(text(xml, "Status")));
        return new Verified(message, type, departed ? List.of(userId) : List.of(), EMPTY);
    }

    private static String encryptedField(byte[] body) {
        if (body.length == 0) {
            return null;
        }
        return text(parse(body), "Encrypt");
    }

    /** 根元素下第一个同名子元素的文本；没有则为空。 */
    private static String text(Document xml, String element) {
        NodeList nodes = xml.getDocumentElement().getElementsByTagName(element);
        for (int i = 0; i < nodes.getLength(); i++) {
            Node node = nodes.item(i);
            if (node.getParentNode() == xml.getDocumentElement()) {
                return node.getTextContent().trim();
            }
        }
        return null;
    }

    private static Document parse(byte[] body) {
        try {
            return builder().parse(new ByteArrayInputStream(body));
        } catch (SAXException | IOException e) {
            throw OrgEventException.malformed("event body is not well-formed XML");
        }
    }

    /** 禁用 DTD、外部实体与 XInclude，防 XXE 与实体膨胀。 */
    private static DocumentBuilder builder() {
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
            factory.setXIncludeAware(false);
            factory.setExpandEntityReferences(false);
            factory.setNamespaceAware(false);
            DocumentBuilder builder = factory.newDocumentBuilder();
            builder.setErrorHandler(new org.xml.sax.helpers.DefaultHandler());
            return builder;
        } catch (ParserConfigurationException e) {
            throw new IllegalStateException("secure XML parser unavailable", e);
        }
    }
}
