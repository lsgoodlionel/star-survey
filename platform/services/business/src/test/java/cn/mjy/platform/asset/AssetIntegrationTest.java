package cn.mjy.platform.asset;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;
import org.springframework.boot.test.context.SpringBootTest;

/** 资产车道里需要取件票的测试标注：给出资产主密钥与平台对外地址，其余沿用共享的测试配置。 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.RUNTIME)
@SpringBootTest(properties = {
        "PLATFORM_ASSET_SECRET=" + AssetIntegrationTest.MASTER_SECRET,
        "platform.asset.public-base-url=https://survey.example"
})
public @interface AssetIntegrationTest {

    /** 至少 32 字节，只用于测试。 */
    String MASTER_SECRET = "asset-master-secret-for-tests-only-0123456789";
}
