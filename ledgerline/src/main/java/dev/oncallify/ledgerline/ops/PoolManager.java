package dev.oncallify.ledgerline.ops;

import dev.oncallify.ledgerline.config.AppConfig;
import org.apache.tomcat.jdbc.pool.DataSource;
import org.apache.tomcat.jdbc.pool.PoolProperties;

import java.util.function.Supplier;

/**
 * Owns the Tomcat JDBC connection pool and rebuilds it when the pool-relevant part of
 * the config changes (size, wait, max age, URL, user, password). The old pool is
 * closed; connections borrowed from it finish their work and are discarded.
 *
 * <p>{@code maxAge} is set so connections are recycled regularly. That is what makes a
 * rotated database password surface within seconds rather than days: new connections
 * need the new password.
 */
public final class PoolManager implements Supplier<javax.sql.DataSource>, AutoCloseable {
    private volatile DataSource pool;
    private volatile AppConfig.PoolKey key;
    private volatile long rebuilds;

    public PoolManager(AppConfig initial) {
        this.pool = build(initial);
        this.key = initial.poolKey();
    }

    /** Config listener: rebuild only if something the pool depends on changed. */
    public synchronized void onConfig(AppConfig cfg) {
        if (cfg.poolKey().equals(key)) {
            return;
        }
        DataSource old = pool;
        pool = build(cfg);
        key = cfg.poolKey();
        rebuilds++;
        old.close(false);
    }

    @Override
    public javax.sql.DataSource get() {
        return pool;
    }

    public int active() { return pool.getActive(); }
    public int idle() { return pool.getIdle(); }
    public int waiting() { return pool.getWaitCount(); }
    public int max() { return pool.getMaxActive(); }
    public long rebuilds() { return rebuilds; }

    private static DataSource build(AppConfig cfg) {
        PoolProperties p = new PoolProperties();
        p.setUrl(cfg.dbUrl());
        p.setDriverClassName("org.postgresql.Driver");
        p.setUsername(cfg.dbUser());
        p.setPassword(cfg.dbPassword());
        p.setMaxActive(cfg.poolMax());
        p.setMaxIdle(cfg.poolMax());
        p.setMinIdle(Math.min(2, cfg.poolMax()));
        p.setInitialSize(0);
        p.setMaxWait(cfg.poolMaxWaitMs());
        p.setMaxAge(cfg.poolMaxAgeMs());
        p.setTestOnBorrow(true);
        p.setValidationQuery("SELECT 1");
        p.setValidationInterval(1_000);
        p.setFairQueue(true);
        p.setJmxEnabled(false);
        DataSource ds = new DataSource();
        ds.setPoolProperties(p);
        return ds;
    }

    @Override
    public void close() {
        pool.close(true);
    }
}
