package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import org.junit.jupiter.api.Test;

/** 游标是不透明的字符串：能原样往返，任何篡改或残缺都以 400 拒绝而不是被猜着用。 */
class ResponseCursorTest {

    @Test
    void aCursorRoundTrips() {
        ResponseCursor cursor = new ResponseCursor(3, "0b0d3f2e-aaaa-4000-8000-000000000001", 42);

        assertThat(ResponseCursor.decode(cursor.encode())).isEqualTo(cursor);
    }

    @Test
    void theEncodingIsUrlSafe() {
        String encoded = new ResponseCursor(1, "gen/with+odd=chars", 7).encode();

        assertThat(encoded).matches("[A-Za-z0-9_-]+");
    }

    @Test
    void malformedCursorsAreRejected() {
        for (String bad : new String[] {"", "!!!", enc("v2|1|1|g"), enc("v1|x|1|g"), enc("v1|1|1|"),
                enc("v1|0|1|g"), enc("v1|1|-1|g"), enc("v1|1|1"), enc("v1|1|1|" + "g".repeat(65))}) {
            assertThatThrownBy(() -> ResponseCursor.decode(bad))
                    .as(bad).isInstanceOf(InvalidResponseQueryException.class);
        }
    }

    private static String enc(String raw) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw.getBytes(StandardCharsets.UTF_8));
    }
}
