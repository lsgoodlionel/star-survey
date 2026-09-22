package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.util.Optional;

/**
 * 按"租户 + 密钥名"解析开放平台应用密钥。数据库只存密钥名；密钥值只在运行环境或密钥库里，
 * 且解析时必须把租户纳入键，这样一个租户无论填什么名字都拿不到别的租户的密钥。
 * 实现不得记录密钥值。
 */
public interface SecretResolver {

    Optional<String> resolve(TenantId tenant, String secretRef);
}
