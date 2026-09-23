package cn.mjy.platform.contacts;

/** 目标不存在，或不在调用者的数据范围 / 租户内——两者对外无法区分，一律 404。 */
public class ContactNotFoundException extends RuntimeException {

    public ContactNotFoundException(String message) {
        super(message);
    }
}
