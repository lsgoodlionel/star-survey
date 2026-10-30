package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import org.springframework.stereotype.Component;

/** SMTP 授权码的唯一加解密点；AES-GCM 的认证失败一律按不可用处理，不降级为明文。 */
@Component
final class EmailCredentialCipher {

    private static final String TRANSFORMATION = "AES/GCM/NoPadding";
    private static final int NONCE_BYTES = 12;
    private static final int TAG_BITS = 128;
    private final DeliveryKeys keys;
    private final SecureRandom random = new SecureRandom();

    EmailCredentialCipher(DeliveryKeys keys) {
        this.keys = keys;
    }

    Envelope seal(TenantId tenant, String credential) {
        if (credential == null || credential.isBlank()) {
            throw DeliveryExceptions.invalid("email credential is required");
        }
        byte[] nonce = new byte[NONCE_BYTES];
        random.nextBytes(nonce);
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, keys.emailCredentialKey(tenant), new GCMParameterSpec(TAG_BITS, nonce));
            return new Envelope(cipher.doFinal(credential.getBytes(StandardCharsets.UTF_8)), nonce);
        } catch (GeneralSecurityException exception) {
            throw new DeliveryUnavailableException("email credential encryption is unavailable");
        }
    }

    String open(TenantId tenant, byte[] ciphertext, byte[] nonce) {
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.DECRYPT_MODE, keys.emailCredentialKey(tenant), new GCMParameterSpec(TAG_BITS, nonce));
            return new String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8);
        } catch (GeneralSecurityException exception) {
            throw new DeliveryUnavailableException("stored email credential cannot be decrypted");
        }
    }

    record Envelope(byte[] ciphertext, byte[] nonce) { }
}
