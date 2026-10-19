package dev.oncallify.ledgerline.web;

import dev.oncallify.ledgerline.config.ConfigSource;
import dev.oncallify.ledgerline.metrics.MetricsRegistry;
import dev.oncallify.ledgerline.ops.FaultInjector;
import dev.oncallify.ledgerline.ops.JsonLog;
import dev.oncallify.ledgerline.ops.PoolManager;
import dev.oncallify.ledgerline.service.LedgerService;
import jakarta.servlet.ServletContext;

/** Everything the servlets share, built once per context start by {@link LedgerlineListener}. */
public record AppContext(
        ConfigSource config,
        PoolManager pool,
        FaultInjector faults,
        MetricsRegistry metrics,
        JsonLog log,
        LedgerService ledger,
        String startedAt) {

    static final String ATTR = AppContext.class.getName();

    static AppContext of(ServletContext sc) {
        return (AppContext) sc.getAttribute(ATTR);
    }
}
