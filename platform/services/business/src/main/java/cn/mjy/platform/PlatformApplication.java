package cn.mjy.platform;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/** 商业平台模块化单体入口。各业务模块按包隔离，跨模块只通过 shared 下的契约交互。 */
@SpringBootApplication
public class PlatformApplication {

    public static void main(String[] args) {
        SpringApplication.run(PlatformApplication.class, args);
    }
}
