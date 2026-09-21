<?php

/**
 * 通过 HTTPS 把事件投递到平台的 /internal/engine-events。
 *
 * 每个引擎实例有自己的密钥（运营从平台签发，平台由主密钥按实例标识派生），
 * 请求头 X-Mjy-Engine-Instance 声明本实例，X-Mjy-Signature 是用本实例密钥对
 * "时间戳.请求体" 做的 HMAC-SHA256（含时间戳，便于平台侧拒绝重放）。平台只按
 * 请求头里的实例派生密钥验签，并要求批次内每个事件都属于该实例。生产环境还应
 * 启用双向 TLS，由部署层配置客户端证书。任何非 2xx、超时或连接错误都抛异常，
 * 事件保持未投递状态等待下轮重试。
 */
class MjyHttpEventTransport implements MjyEventTransport
{
    private const TIMEOUT_SECONDS = 15;
    private const SIGNATURE_ALGORITHM = 'sha256';
    /** 实例标识要放进请求头：只允许 1–64 个可见 ASCII 字符（也杜绝换行注入额外的头）。 */
    private const HEADER_SAFE_INSTANCE = '/^[\x21-\x7E]{1,64}$/D';

    /** @var string */
    private $endpoint;

    /** @var string */
    private $engineInstanceId;

    /** @var string */
    private $secret;

    /** @var string|null */
    private $clientCertificatePath;

    /**
     * @param string $engineInstanceId 本实例标识，与事件日志里写入的 engine_instance_id 相同
     * @param string $secret           平台为本实例签发的密钥（MJY_PLATFORM_EVENTS_SECRET）
     */
    public function __construct(
        string $endpoint,
        string $engineInstanceId,
        string $secret,
        ?string $clientCertificatePath = null
    ) {
        if (preg_match(self::HEADER_SAFE_INSTANCE, $engineInstanceId) !== 1) {
            throw new InvalidArgumentException('Engine instance id must be 1-64 visible ASCII characters');
        }
        $this->endpoint = $endpoint;
        $this->engineInstanceId = $engineInstanceId;
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
                'X-Mjy-Engine-Instance: ' . $this->engineInstanceId,
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
