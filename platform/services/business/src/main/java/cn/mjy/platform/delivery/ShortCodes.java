package cn.mjy.platform.delivery;

import java.security.SecureRandom;
import java.util.regex.Pattern;

/**
 * 短链标识。用密码学随机数从 31 个不易混淆的字符里取 13 位，约 64 位熵：
 * 短链是"拿到就能作答"的凭证，必须不可枚举——自增或时间派生的标识可以被顺着数出来，
 * 一次扫描就能拿到一个租户的全部投放链接。
 *
 * <p>字符集去掉 {@code 0/o}、{@code 1/l/i}：印在纸上或口头转述时最容易读错的几组。
 */
public final class ShortCodes {

    public static final String ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz";
    public static final int LENGTH = 13;
    public static final String CODE_REGEX = "[" + ALPHABET + "]{" + LENGTH + "}";

    private static final Pattern CODE = Pattern.compile(CODE_REGEX);
    private static final SecureRandom RANDOM = new SecureRandom();

    private ShortCodes() {
    }

    public static String random() {
        char[] code = new char[LENGTH];
        for (int i = 0; i < LENGTH; i++) {
            code[i] = ALPHABET.charAt(RANDOM.nextInt(ALPHABET.length()));
        }
        return new String(code);
    }

    /** 向下取整的熵（比特）。 */
    public static int entropyBits() {
        return (int) Math.floor(LENGTH * (Math.log(ALPHABET.length()) / Math.log(2)));
    }

    /** 形状不对的短链根本不查库：避免用随便构造的字符串在存储上做探测。 */
    public static boolean isWellFormed(String code) {
        return code != null && CODE.matcher(code).matches();
    }
}
