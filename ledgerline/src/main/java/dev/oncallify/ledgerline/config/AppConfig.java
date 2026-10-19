package dev.oncallify.ledgerline.config;

import java.nio.file.Path;
import java.util.Properties;

/**
 * One immutable snapshot of {@code ledgerline.properties}. A new snapshot (with a new
 * {@code version}) is created whenever the file or the password file changes.
 */
public record AppConfig(
        long version,
        String dbUrl,
        String dbUser,
        String dbPassword,
        int poolMax,
        int poolMaxWaitMs,
        int poolMaxAgeMs,
        String fxUrl,
        int fxTimeoutMs,
        boolean fxFallbackEnabled,
        boolean faultsEnabled,
        String adminToken,
        Path logDir) {

    /** The part of the config that, when changed, requires a new connection pool. */
    public record PoolKey(String dbUrl, String dbUser, String dbPassword, int poolMax, int poolMaxWaitMs, int poolMaxAgeMs) { }

    public PoolKey poolKey() {
        return new PoolKey(dbUrl, dbUser, dbPassword, poolMax, poolMaxWaitMs, poolMaxAgeMs);
    }

    static AppConfig from(long version, Properties p, String password, String adminToken) {
        return new AppConfig(
                version,
                required(p, "db.url"),
                required(p, "db.user"),
                password,
                intProp(p, "pool.max", 10, 1, 200),
                intProp(p, "pool.max.wait.ms", 500, 10, 60_000),
                intProp(p, "pool.max.age.ms", 5_000, 0, 3_600_000),
                required(p, "fx.url"),
                intProp(p, "fx.timeout.ms", 1_000, 50, 60_000),
                Boolean.parseBoolean(p.getProperty("fx.fallback.enabled", "false").trim()),
                Boolean.parseBoolean(p.getProperty("faults.enabled", "false").trim()),
                adminToken,
                Path.of(p.getProperty("log.dir", System.getProperty("java.io.tmpdir")).trim()));
    }

    private static String required(Properties p, String key) {
        String v = p.getProperty(key);
        if (v == null || v.isBlank()) {
            throw new IllegalArgumentException("missing config key: " + key);
        }
        return v.trim();
    }

    static int intProp(Properties p, String key, int dflt, int min, int max) {
        String raw = p.getProperty(key);
        if (raw == null || raw.isBlank()) {
            return dflt;
        }
        int v;
        try {
            v = Integer.parseInt(raw.trim());
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException(key + " must be an integer, got '" + raw + "'", e);
        }
        if (v < min || v > max) {
            throw new IllegalArgumentException(key + " must be in [" + min + ", " + max + "], got " + v);
        }
        return v;
    }
}
