package cn.mjy.platform.survey;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/**
 * IP / CIDR 的规范写法，与网关 {@code str(ipaddress.ip_network(value, strict=False))} 逐字一致：
 * 主机位清零、总是带前缀长度、IPv6 小写去前导零并按 RFC 5952 压缩最长的一段全零。
 * 只为重算 {@code policyDigest} 而存在（{@link AccessPolicyDigest}）。
 *
 * <p>只接受字面量，绝不做域名解析；看不懂的写法一律抛异常，由调用方按"算不出摘要"处理（失败即关闭）。
 */
final class CanonicalIpNetwork {

    private static final Pattern IPV4 = Pattern.compile("\\A(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\z");
    private static final Pattern IPV6_CHARS = Pattern.compile("\\A[0-9A-Fa-f:.]+\\z");
    private static final int BITS_PER_BYTE = 8;
    private static final int HEXTETS = 8;

    private CanonicalIpNetwork() {
    }

    static final class MalformedNetworkException extends RuntimeException {

        MalformedNetworkException(String message) {
            super(message);
        }
    }

    static String canonical(String value) {
        String text = value == null ? "" : value.trim();
        int slash = text.lastIndexOf('/');
        byte[] address = literal(slash < 0 ? text : text.substring(0, slash));
        int maxPrefix = address.length * BITS_PER_BYTE;
        int prefix = slash < 0 ? maxPrefix : prefix(text.substring(slash + 1), maxPrefix);
        return format(masked(address, prefix)) + "/" + prefix;
    }

    private static byte[] literal(String host) {
        if (host.isEmpty()) {
            throw new MalformedNetworkException("empty address");
        }
        if (host.indexOf(':') < 0) {
            return ipv4(host);
        }
        if (!IPV6_CHARS.matcher(host).matches()) {
            throw new MalformedNetworkException("not an IPv6 literal: " + host);
        }
        return ipv6(host);
    }

    /**
     * 自己解析 IPv6 字面量，不走 {@code InetAddress}：后者会把 {@code ::ffff:1.2.3.4} 还原成 4 字节的
     * IPv4 地址，而 Python 的 ipaddress 始终当 16 字节处理，规范写法也因此不同。
     */
    private static byte[] ipv6(String host) {
        int gap = host.indexOf("::");
        String head = gap < 0 ? host : host.substring(0, gap);
        String tail = gap < 0 ? null : host.substring(gap + 2);
        if (tail != null && tail.contains("::")) {
            throw new MalformedNetworkException("more than one '::': " + host);
        }
        List<Integer> left = hextets(head, tail == null);
        List<Integer> right = tail == null ? List.of() : hextets(tail, true);
        int total = left.size() + right.size();
        if (tail == null ? total != HEXTETS : total >= HEXTETS) {
            throw new MalformedNetworkException("wrong number of groups: " + host);
        }
        byte[] address = new byte[16];
        int index = 0;
        for (int hextet : left) {
            index = write(address, index, hextet);
        }
        index += 2 * (HEXTETS - total);
        for (int hextet : right) {
            index = write(address, index, hextet);
        }
        return address;
    }

    /** 一段用 {@code :} 分隔的分组；最后一组可以是点分十进制的 IPv4（占两个分组）。 */
    private static List<Integer> hextets(String part, boolean ipv4Allowed) {
        if (part.isEmpty()) {
            return List.of();
        }
        String[] tokens = part.split(":", -1);
        List<Integer> hextets = new ArrayList<>();
        for (int i = 0; i < tokens.length; i++) {
            String token = tokens[i];
            if (token.indexOf('.') >= 0) {
                if (!ipv4Allowed || i != tokens.length - 1) {
                    throw new MalformedNetworkException("misplaced embedded IPv4: " + part);
                }
                byte[] embedded = ipv4(token);
                hextets.add(((embedded[0] & 0xFF) << 8) | (embedded[1] & 0xFF));
                hextets.add(((embedded[2] & 0xFF) << 8) | (embedded[3] & 0xFF));
                continue;
            }
            if (token.isEmpty() || token.length() > 4) {
                throw new MalformedNetworkException("not a hex group: " + part);
            }
            hextets.add(Integer.parseInt(token, 16));
        }
        return hextets;
    }

    private static int write(byte[] address, int index, int hextet) {
        address[index] = (byte) (hextet >> 8);
        address[index + 1] = (byte) hextet;
        return index + 2;
    }

    /** 与 Python 的 ipaddress 一样严格：恰好四段十进制、0–255、除 {@code 0} 外不许前导零。 */
    private static byte[] ipv4(String host) {
        var match = IPV4.matcher(host);
        if (!match.matches()) {
            throw new MalformedNetworkException("not an IPv4 literal: " + host);
        }
        byte[] address = new byte[4];
        for (int i = 0; i < 4; i++) {
            String part = match.group(i + 1);
            if (part.length() > 1 && part.charAt(0) == '0') {
                throw new MalformedNetworkException("octet has a leading zero: " + host);
            }
            int octet = Integer.parseInt(part);
            if (octet > 255) {
                throw new MalformedNetworkException("octet out of range: " + host);
            }
            address[i] = (byte) octet;
        }
        return address;
    }

    private static int prefix(String text, int maxPrefix) {
        if (text.isEmpty() || !text.chars().allMatch(Character::isDigit)) {
            throw new MalformedNetworkException("prefix length is not a number: " + text);
        }
        int prefix = Integer.parseInt(text);
        if (prefix > maxPrefix) {
            throw new MalformedNetworkException("prefix length out of range: " + text);
        }
        return prefix;
    }

    private static byte[] masked(byte[] address, int prefix) {
        byte[] result = address.clone();
        for (int bit = prefix; bit < address.length * BITS_PER_BYTE; bit++) {
            result[bit / BITS_PER_BYTE] &= (byte) ~(1 << (7 - bit % BITS_PER_BYTE));
        }
        return result;
    }

    private static String format(byte[] address) {
        if (address.length == 4) {
            StringBuilder text = new StringBuilder();
            for (byte octet : address) {
                text.append(text.isEmpty() ? "" : ".").append(octet & 0xFF);
            }
            return text.toString();
        }
        return compress(groups(address));
    }

    private static String[] groups(byte[] address) {
        String[] hextets = new String[HEXTETS];
        for (int i = 0; i < HEXTETS; i++) {
            hextets[i] = Integer.toHexString(((address[2 * i] & 0xFF) << 8) | (address[2 * i + 1] & 0xFF));
        }
        return hextets;
    }

    /** 最长的一段（至少两个）全零换成 {@code ::}；长度相同时取最靠前的那段，与 Python 一致。 */
    private static String compress(String[] hextets) {
        int bestStart = -1;
        int bestLength = 0;
        int runStart = -1;
        int runLength = 0;
        for (int i = 0; i < HEXTETS; i++) {
            if (!"0".equals(hextets[i])) {
                runStart = -1;
                runLength = 0;
                continue;
            }
            runStart = runStart < 0 ? i : runStart;
            runLength++;
            if (runLength > bestLength) {
                bestLength = runLength;
                bestStart = runStart;
            }
        }
        if (bestLength <= 1) {
            return String.join(":", hextets);
        }
        return join(hextets, 0, bestStart) + "::" + join(hextets, bestStart + bestLength, HEXTETS);
    }

    private static String join(String[] hextets, int from, int to) {
        StringBuilder text = new StringBuilder();
        for (int i = from; i < to; i++) {
            text.append(text.isEmpty() ? "" : ":").append(hextets[i]);
        }
        return text.toString();
    }
}
