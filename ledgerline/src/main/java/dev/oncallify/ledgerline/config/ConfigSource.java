package dev.oncallify.ledgerline.config;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;
import java.util.function.Supplier;

/**
 * Reloading config. {@link #get()} is called on every request; at most once per
 * {@code checkIntervalMs} it compares file modification times and, if anything moved,
 * parses a new snapshot and tells listeners.
 *
 * <p>A file that fails to parse is rejected and the previous good snapshot stays live,
 * with the error kept in {@link #lastError()} so it shows up in /api/health. It is
 * re-checked on the next interval, so a file caught mid-write is picked up once complete.
 * That is the difference between "a bad deploy breaks one feature" and "a typo takes
 * the service down".
 */
public final class ConfigSource implements Supplier<AppConfig> {
    private final Path configFile;
    private final long checkIntervalMs;
    private final List<Consumer<AppConfig>> listeners = new CopyOnWriteArrayList<>();

    private volatile AppConfig current;
    private volatile String lastError;
    private volatile long lastCheck;
    private volatile Stamp stamp;

    private record Stamp(FileTime config, FileTime password, FileTime token) { }

    public ConfigSource(Path configFile, long checkIntervalMs) {
        this.configFile = configFile;
        this.checkIntervalMs = checkIntervalMs;
        Loaded first = load(1);
        this.current = first.config;
        this.stamp = first.stamp;
        this.lastCheck = System.currentTimeMillis();
    }

    public void addListener(Consumer<AppConfig> listener) {
        listeners.add(listener);
    }

    public String lastError() {
        return lastError;
    }

    public Path configFile() {
        return configFile;
    }

    @Override
    public AppConfig get() {
        long now = System.currentTimeMillis();
        if (now - lastCheck >= checkIntervalMs) {
            synchronized (this) {
                if (now - lastCheck >= checkIntervalMs) {
                    lastCheck = now;
                    refreshIfChanged();
                }
            }
        }
        return current;
    }

    /** Forces a check now (used by tests and by the admin endpoint). */
    public synchronized boolean refreshIfChanged() {
        Stamp now;
        try {
            now = stampOf(readRaw());
        } catch (IOException e) {
            lastError = "cannot read config: " + e.getMessage();
            return false;
        }
        if (now.equals(stamp)) {
            return false;
        }
        try {
            Loaded next = load(current.version() + 1);
            current = next.config;
            stamp = next.stamp;
            lastError = null;
            for (Consumer<AppConfig> l : listeners) {
                l.accept(current);
            }
            return true;
        } catch (RuntimeException e) {
            // Keep the old stamp so the next check retries: the file may have been caught
            // half-written. Checks are throttled to checkIntervalMs, so this is cheap.
            lastError = e.getMessage();
            return false;
        }
    }

    private record Loaded(AppConfig config, Stamp stamp) { }

    private Loaded load(long version) {
        try {
            Properties p = readRaw();
            String password = readSecret(p, "db.password.file");
            String token = readSecretOptional(p, "admin.token.file");
            return new Loaded(AppConfig.from(version, p, password, token), stampOf(p));
        } catch (IOException e) {
            throw new IllegalStateException("cannot load config " + configFile + ": " + e.getMessage(), e);
        }
    }

    private Properties readRaw() throws IOException {
        Properties p = new Properties();
        try (Reader r = Files.newBufferedReader(configFile, StandardCharsets.UTF_8)) {
            p.load(r);
        }
        return p;
    }

    private Stamp stampOf(Properties p) throws IOException {
        return new Stamp(Files.getLastModifiedTime(configFile), mtime(p.getProperty("db.password.file")),
                mtime(p.getProperty("admin.token.file")));
    }

    private Path resolve(String raw) {
        Path path = Path.of(raw.trim());
        return path.isAbsolute() ? path : configFile.toAbsolutePath().getParent().resolve(path);
    }

    private FileTime mtime(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            return Files.getLastModifiedTime(resolve(raw));
        } catch (IOException e) {
            return null;
        }
    }

    private String readSecret(Properties p, String key) throws IOException {
        String raw = p.getProperty(key);
        if (raw == null || raw.isBlank()) {
            throw new IllegalArgumentException("missing config key: " + key);
        }
        return Files.readString(resolve(raw), StandardCharsets.UTF_8).trim();
    }

    private String readSecretOptional(Properties p, String key) throws IOException {
        String raw = p.getProperty(key);
        if (raw == null || raw.isBlank()) {
            return null;
        }
        return Files.readString(resolve(raw), StandardCharsets.UTF_8).trim();
    }
}
