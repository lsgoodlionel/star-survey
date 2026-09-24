package cn.mjy.platform.asset;

/** 上传不合规（空文件、超限、类型不在白名单、内容与声明不符、名称为空），对外 400。 */
public class InvalidAssetException extends RuntimeException {

    public InvalidAssetException(String message) {
        super(message);
    }
}
