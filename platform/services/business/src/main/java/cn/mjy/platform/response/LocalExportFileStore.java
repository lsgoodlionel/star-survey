package cn.mjy.platform.response;

import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;

/**
 * 本地文件系统实现（开发与测试；多副本部署需要共享卷，生产应换对象存储实现）。
 * 写入先落到同目录的临时文件，提交时原子改名，覆盖同名对象。
 */
final class LocalExportFileStore implements ExportFileStore {

    private static final String TEMP_SUFFIX = ".partial";
    private static final int BUFFER_BYTES = 64 * 1024;

    private final Path root;

    LocalExportFileStore(Path root) {
        this.root = root.toAbsolutePath().normalize();
    }

    @Override
    public Upload create(String key) throws IOException {
        Path target = resolve(key);
        Files.createDirectories(target.getParent());
        Path temp = Files.createTempFile(target.getParent(), target.getFileName().toString(), TEMP_SUFFIX);
        return new LocalUpload(temp, target);
    }

    @Override
    public InputStream open(String key) throws IOException {
        return Files.newInputStream(resolve(key));
    }

    @Override
    public boolean exists(String key) {
        return Files.isRegularFile(resolve(key));
    }

    @Override
    public long size(String key) throws IOException {
        return Files.size(resolve(key));
    }

    @Override
    public void delete(String key) throws IOException {
        Files.deleteIfExists(resolve(key));
    }

    @Override
    public void deletePrefix(String prefix) throws IOException {
        Path dir = resolve(prefix);
        if (!Files.isDirectory(dir)) {
            Files.deleteIfExists(dir);
            return;
        }
        List<Path> paths;
        try (Stream<Path> walk = Files.walk(dir)) {
            paths = walk.sorted(Comparator.reverseOrder()).toList();
        }
        for (Path path : paths) {
            Files.deleteIfExists(path);
        }
    }

    private Path resolve(String key) {
        Path path = root.resolve(ExportFileStore.requireKey(key)).normalize();
        if (!path.startsWith(root)) {
            throw new IllegalArgumentException("export file key escapes the storage root");
        }
        return path;
    }

    private static final class LocalUpload implements Upload {

        private final Path temp;
        private final Path target;
        private final OutputStream out;
        private boolean finished;

        LocalUpload(Path temp, Path target) throws IOException {
            this.temp = temp;
            this.target = target;
            this.out = new BufferedOutputStream(Files.newOutputStream(temp), BUFFER_BYTES);
        }

        @Override
        public OutputStream stream() {
            return out;
        }

        @Override
        public void commit() throws IOException {
            if (finished) {
                throw new IllegalStateException("upload already finished");
            }
            finished = true;
            out.close();
            try {
                Files.move(temp, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            } catch (AtomicMoveNotSupportedException e) {
                Files.move(temp, target, StandardCopyOption.REPLACE_EXISTING);
            }
        }

        /** 未提交即放弃：删掉临时文件。 */
        @Override
        public void close() throws IOException {
            if (finished) {
                return;
            }
            finished = true;
            try {
                out.close();
            } finally {
                Files.deleteIfExists(temp);
            }
        }
    }
}
