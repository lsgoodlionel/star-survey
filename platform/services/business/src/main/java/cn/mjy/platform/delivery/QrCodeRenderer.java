package cn.mjy.platform.delivery;

import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import javax.imageio.ImageIO;

/**
 * 把 {@link QrCode} 画成 PNG 或 SVG。两种输出的模块完全一致，只是载体不同：
 * PNG 走 JDK 自带的 ImageIO，SVG 是纯文本拼接。
 *
 * <p>SVG 只输出矩形与固定的属性，不含脚本、外部引用或调用方文本，因此内联到页面里不会引入脚本注入面。
 */
public final class QrCodeRenderer {

    /** 规范要求四个模块宽的静区，扫码器据此找边界。 */
    public static final int DEFAULT_QUIET_ZONE = 4;
    public static final int MIN_MODULE_SIZE = 1;
    public static final int MAX_MODULE_SIZE = 40;

    private QrCodeRenderer() {
    }

    public static byte[] png(QrCode qr, int moduleSize, int quietZone) {
        int scale = requireModuleSize(moduleSize);
        int quiet = requireQuietZone(quietZone);
        int pixels = (qr.size() + quiet * 2) * scale;
        BufferedImage image = new BufferedImage(pixels, pixels, BufferedImage.TYPE_BYTE_BINARY);
        Graphics2D graphics = image.createGraphics();
        try {
            graphics.setColor(Color.WHITE);
            graphics.fillRect(0, 0, pixels, pixels);
            graphics.setColor(Color.BLACK);
            for (int y = 0; y < qr.size(); y++) {
                for (int x = 0; x < qr.size(); x++) {
                    if (qr.dark(x, y)) {
                        graphics.fillRect((quiet + x) * scale, (quiet + y) * scale, scale, scale);
                    }
                }
            }
        } finally {
            graphics.dispose();
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try {
            if (!ImageIO.write(image, "png", out)) {
                throw new IllegalStateException("no PNG writer available in this JDK");
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return out.toByteArray();
    }

    public static String svg(QrCode qr, int moduleSize, int quietZone) {
        int scale = requireModuleSize(moduleSize);
        int quiet = requireQuietZone(quietZone);
        int span = qr.size() + quiet * 2;
        StringBuilder path = new StringBuilder();
        for (int y = 0; y < qr.size(); y++) {
            for (int x = 0; x < qr.size(); x++) {
                if (qr.dark(x, y)) {
                    path.append(path.isEmpty() ? "" : " ")
                            .append('M').append(x + quiet).append(',').append(y + quiet).append("h1v1h-1z");
                }
            }
        }
        return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
                + "<svg xmlns=\"http://www.w3.org/2000/svg\" version=\"1.1\""
                + " width=\"" + span * scale + "\" height=\"" + span * scale + "\""
                + " viewBox=\"0 0 " + span + " " + span + "\" shape-rendering=\"crispEdges\">\n"
                + "<rect width=\"" + span + "\" height=\"" + span + "\" fill=\"#ffffff\"/>\n"
                + "<path d=\"" + path + "\" fill=\"#000000\"/>\n"
                + "</svg>\n";
    }

    private static int requireModuleSize(int moduleSize) {
        if (moduleSize < MIN_MODULE_SIZE || moduleSize > MAX_MODULE_SIZE) {
            throw new IllegalArgumentException("module size must be between " + MIN_MODULE_SIZE
                    + " and " + MAX_MODULE_SIZE);
        }
        return moduleSize;
    }

    private static int requireQuietZone(int quietZone) {
        if (quietZone < 0 || quietZone > 16) {
            throw new IllegalArgumentException("quiet zone must be between 0 and 16 modules");
        }
        return quietZone;
    }
}
