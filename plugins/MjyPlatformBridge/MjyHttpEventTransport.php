<?php

/**
 * 通过 HTTPS 把事件投递到平台的 /internal/engine-events。
 *
 * 用共享密钥做 HMAC-SHA256 签名（含时间戳，便于平台侧拒绝重放）；生产环境还应
 * 启用双向 TLS，由部署层配置客户端证书。任何非 2xx、超时或连接错误都抛异常，
 * 事件保持未投递状态等待下轮重试。
 */
class MjyHttpEventTransport implements MjyEventTransport
{
    private const TIMEOUT_SECONDS = 15;
    private const SIGNATURE_ALGORITHM = 'sha256';

    /** @var string */
    private $endpoint;

    /** @var string */
    private $secret;

    /** @var string|null */
    private $clientCertificatePath;

    public function __construct(string $endpoint, string $secret, ?string $clientCertificatePath = null)
    {
        $this->endpoint = $endpoint;
        $this->secret = $secret;
        $this->clientCertificatePath = $clientCertificatePath;
    }

    public function send(array $envelopes): void
    {
        $body = json_encode(['events' => $envelopes], JSON_UNESCAPED_UNICODE);
        if ($body === false) {
            throw new RuntimeException('Could not encode event envelopes: ' . json_last_error_msg());
        }
        $timestamp = (string) time();
        $curl = curl_init($this->endpoint);
        curl_setopt_array($curl, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $body,
            CURLOPT_TIMEOUT => self::TIMEOUT_SECONDS,
            CURLOPT_HTTPHEADER => [
                'Content-Type: application/json',
                'X-Mjy-Timestamp: ' . $timestamp,
                'X-Mjy-Signature: ' . $this->sign($timestamp, $body),
            ],
        ]);
        if ($this->clientCertificatePath !== null) {
            curl_setopt($curl, CURLOPT_SSLCERT, $this->clientCertificatePath);
        }
        $response = curl_exec($curl);
        $status = (int) curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
        $error = curl_error($curl);
        curl_close($curl);

        if ($response === false) {
            throw new RuntimeException("Event delivery failed: $error");
        }
        if ($status < 200 || $status >= 300) {
            throw new RuntimeException("Event delivery rejected with HTTP $status: " . substr((string) $response, 0, 200));
        }
    }

    private function sign(string $timestamp, string $body): string
    {
        return hash_hmac(self::SIGNATURE_ALGORITHM, $timestamp . '.' . $body, $this->secret);
    }
}
