package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Locale;
import java.util.Optional;
import java.util.regex.Pattern;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

/**
 * 从运行环境读取密钥：变量名 {@code PLATFORM_ORG_SECRET_<租户标识去横线大写>_<密钥名>}。
 * 例：租户 3f2a0c1e-… 的密钥名 WECOM → {@code PLATFORM_ORG_SECRET_3F2A0C1E…_WECOM}。
 * 配置 PLATFORM_ORG_SECRET_DIR 时优先且仅从该只读目录读取同名文件；缺失或无效时失败关闭。
 * 未设置目录时继续从环境读取，换密钥库时可实现本接口的另一个 bean。
 */
@Component
class EnvironmentSecretResolver implements SecretResolver {

    static final String PREFIX = "PLATFORM_ORG_SECRET_";
    private static final Pattern REF = Pattern.compile("[A-Z][A-Z0-9_]{0,63}");

    private final Environment environment;

    EnvironmentSecretResolver(Environment environment) {
        this.environment = environment;
    }

    @Override
    public Optional<String> resolve(TenantId tenant, String secretRef) {
        if (secretRef == null || !REF.matcher(secretRef).matches()) {
            return Optional.empty();
        }
        String name = variableName(tenant, secretRef);
        String directory = environment.getProperty("PLATFORM_ORG_SECRET_DIR");
        if (directory != null) {
            return readFileSecret(directory, name);
        }
        String value = environment.getProperty(name);
        return value == null || value.isBlank() ? Optional.empty() : Optional.of(value);
    }

    private static Optional<String> readFileSecret(String directory, String name) {
        if (directory.isBlank()) return Optional.empty();
        try {
            Path root = Path.of(directory);
            if (!root.isAbsolute() || Files.isSymbolicLink(root)) return Optional.empty();
            Path file = root.resolve(name);
            if (!Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS)) return Optional.empty();
            try (InputStream input = Files.newInputStream(file, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
                byte[] bytes = input.readNBytes(4097);
                if (bytes.length > 4096) return Optional.empty();
                String value = StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString();
                if (value.endsWith("\r\n")) value = value.substring(0, value.length() - 2);
                else if (value.endsWith("\n")) value = value.substring(0, value.length() - 1);
                if (value.isBlank() || value.indexOf('\n') >= 0 || value.indexOf('\r') >= 0 || value.indexOf('\0') >= 0) {
                    return Optional.empty();
                }
                return Optional.of(value);
            }
        } catch (IOException | InvalidPathException | SecurityException e) {
            return Optional.empty();
        }
    }

    static String variableName(TenantId tenant, String secretRef) {
        return PREFIX + tenant.value().toString().replace("-", "").toUpperCase(Locale.ROOT) + "_" + secretRef;
    }
}
