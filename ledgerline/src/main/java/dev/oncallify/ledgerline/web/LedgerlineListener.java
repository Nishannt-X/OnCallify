package dev.oncallify.ledgerline.web;

import dev.oncallify.ledgerline.config.AppConfig;
import dev.oncallify.ledgerline.config.ConfigSource;
import dev.oncallify.ledgerline.fx.FallbackFxClient;
import dev.oncallify.ledgerline.fx.HttpFxClient;
import dev.oncallify.ledgerline.metrics.MetricsRegistry;
import dev.oncallify.ledgerline.ops.FaultInjector;
import dev.oncallify.ledgerline.ops.JsonLog;
import dev.oncallify.ledgerline.ops.PoolManager;
import dev.oncallify.ledgerline.repo.JdbcTxRunner;
import dev.oncallify.ledgerline.service.LedgerService;
import jakarta.servlet.ServletContext;
import jakarta.servlet.ServletContextEvent;
import jakarta.servlet.ServletContextListener;

import javax.management.MBeanServer;
import javax.management.ObjectName;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Clock;
import java.time.Instant;
import java.util.Map;
import java.util.Set;

/**
 * Builds the application when Tomcat starts (or reloads) the context and tears it down
 * on stop. The config path comes from the {@code LEDGERLINE_CONFIG} environment
 * variable or system property.
 */
public final class LedgerlineListener implements ServletContextListener {

    static final Map<String, BigDecimal> SEED_RATES = Map.of(
            "USD/INR", new BigDecimal("83.20"), "INR/USD", new BigDecimal("0.0120"),
            "EUR/INR", new BigDecimal("90.10"), "INR/EUR", new BigDecimal("0.0111"),
            "USD/EUR", new BigDecimal("0.9200"), "EUR/USD", new BigDecimal("1.0870"));

    @Override
    public void contextInitialized(ServletContextEvent event) {
        ServletContext sc = event.getServletContext();
        String path = System.getProperty("LEDGERLINE_CONFIG", System.getenv("LEDGERLINE_CONFIG"));
        if (path == null || path.isBlank()) {
            throw new IllegalStateException("LEDGERLINE_CONFIG is not set");
        }
        ConfigSource config = new ConfigSource(Path.of(path), 500);
        AppConfig cfg = config.get();
        FaultInjector faults = new FaultInjector();
        PoolManager pool = new PoolManager(cfg);
        config.addListener(pool::onConfig);

        JsonLog log;
        try {
            log = new JsonLog(cfg.logDir(), "ledgerline");
        } catch (IOException e) {
            throw new IllegalStateException("cannot open log dir " + cfg.logDir(), e);
        }
        config.addListener(c -> log.info("config reloaded", Map.of(
                "config_version", c.version(), "pool_max", c.poolMax(), "fx_url", c.fxUrl(),
                "fx_fallback_enabled", c.fxFallbackEnabled())));

        MetricsRegistry metrics = new MetricsRegistry();
        registerGauges(metrics, pool, config);

        var fx = new FallbackFxClient(new HttpFxClient(config), () -> config.get().fxFallbackEnabled(), SEED_RATES);
        LedgerService ledger = new LedgerService(new JdbcTxRunner(pool, faults), fx, Clock.systemUTC());

        AppContext app = new AppContext(config, pool, faults, metrics, log, ledger, Instant.now().toString());
        sc.setAttribute(AppContext.ATTR, app);
        initSchema(app);
        log.info("ledgerline started", Map.of("config_version", cfg.version(), "pool_max", cfg.poolMax(),
                "faults_enabled", cfg.faultsEnabled()));
    }

    @Override
    public void contextDestroyed(ServletContextEvent event) {
        AppContext app = AppContext.of(event.getServletContext());
        if (app == null) {
            return;
        }
        app.log().info("ledgerline stopping", Map.of());
        app.faults().releaseAll();
        app.pool().close();
        try {
            app.log().close();
        } catch (IOException ignored) {
            // shutting down
        }
    }

    /**
     * Note what is NOT exported: the number of leaked connections. A real service cannot
     * see its own leak, so the agent has to infer it (pool busy while request threads idle).
     */
    private static void registerGauges(MetricsRegistry m, PoolManager pool, ConfigSource config) {
        m.describe("ledgerline_pool_active", "Connections currently borrowed from the pool");
        m.gauge("ledgerline_pool_active", pool::active);
        m.gauge("ledgerline_pool_idle", pool::idle);
        m.gauge("ledgerline_pool_waiting", pool::waiting);
        m.gauge("ledgerline_pool_max", pool::max);
        m.gauge("ledgerline_pool_rebuilds", pool::rebuilds);
        m.gauge("ledgerline_config_version", () -> config.get().version());
        m.gauge("ledgerline_fx_fallback_enabled", () -> config.get().fxFallbackEnabled() ? 1 : 0);
        Runtime rt = Runtime.getRuntime();
        m.describe("jvm_heap_used_bytes", "Heap in use");
        m.gauge("jvm_heap_used_bytes", () -> rt.totalMemory() - rt.freeMemory());
        m.gauge("jvm_heap_max_bytes", rt::maxMemory);
        m.gauge("tomcat_threads_busy", () -> threadPoolAttr("currentThreadsBusy"));
        m.gauge("tomcat_threads_max", () -> threadPoolAttr("maxThreads"));
    }

    /** Tomcat publishes its connector thread pool as a JMX MBean; sum over connectors. */
    static double threadPoolAttr(String attr) {
        try {
            MBeanServer mbs = ManagementFactory.getPlatformMBeanServer();
            Set<ObjectName> names = mbs.queryNames(new ObjectName("Catalina:type=ThreadPool,name=*"), null);
            double total = 0;
            for (ObjectName n : names) {
                Object v = mbs.getAttribute(n, attr);
                if (v instanceof Number num) {
                    total += num.doubleValue();
                }
            }
            return names.isEmpty() ? Double.NaN : total;
        } catch (Exception e) {
            return Double.NaN;
        }
    }

    private static void initSchema(AppContext app) {
        try (Connection c = app.pool().get().getConnection(); Statement s = c.createStatement()) {
            s.execute("CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                    + "currency CHAR(3) NOT NULL, balance NUMERIC(18,2) NOT NULL CHECK (balance >= 0))");
            s.execute("CREATE TABLE IF NOT EXISTS transfers (id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, "
                    + "from_account TEXT NOT NULL REFERENCES accounts(id), to_account TEXT NOT NULL REFERENCES accounts(id), "
                    + "amount NUMERIC(18,2) NOT NULL, fx_rate NUMERIC(18,6) NOT NULL, credited NUMERIC(18,2) NOT NULL, "
                    + "fx_fallback BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL)");
            s.execute("CREATE INDEX IF NOT EXISTS idx_transfers_from ON transfers(from_account, created_at)");
            s.execute("INSERT INTO accounts (id, owner, currency, balance) "
                    + "SELECT 'acc-' || lpad(g::text, 3, '0'), 'owner-' || g, "
                    + "(ARRAY['INR','USD','EUR'])[1 + g % 3], 1000000.00 FROM generate_series(1, 60) g "
                    + "ON CONFLICT (id) DO NOTHING");
        } catch (SQLException e) {
            // The service still starts; /api/health reports the database as down.
            app.log().error("schema init failed", Map.of("error_kind", "db_unavailable", "detail", String.valueOf(e.getMessage())));
        }
    }
}
