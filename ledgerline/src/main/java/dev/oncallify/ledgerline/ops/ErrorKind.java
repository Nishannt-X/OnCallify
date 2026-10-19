package dev.oncallify.ledgerline.ops;

import dev.oncallify.ledgerline.domain.LedgerException;
import dev.oncallify.ledgerline.fx.FxException;

import java.sql.SQLException;

/**
 * Maps a failure to a stable, low-cardinality label. Business errors are separated
 * from infrastructure errors so a wave of "insufficient funds" never pages anyone.
 */
public enum ErrorKind {
    POOL_EXHAUSTED(true),
    DB_AUTH(true),
    DB_UNAVAILABLE(true),
    FX_TIMEOUT(true),
    FX_UNAVAILABLE(true),
    FX_BAD_RESPONSE(true),
    OUT_OF_MEMORY(true),
    INTERNAL(true),
    BUSINESS(false);

    private final boolean infrastructure;

    ErrorKind(boolean infrastructure) {
        this.infrastructure = infrastructure;
    }

    public boolean infrastructure() {
        return infrastructure;
    }

    public String label() {
        return name().toLowerCase();
    }

    public static ErrorKind classify(Throwable t) {
        for (Throwable c = t; c != null; c = c.getCause()) {
            if (c instanceof LedgerException) {
                return BUSINESS;
            }
            if (c instanceof OutOfMemoryError) {
                return OUT_OF_MEMORY;
            }
            if (c instanceof FxException fx) {
                return switch (fx.kind()) {
                    case TIMEOUT -> FX_TIMEOUT;
                    case UNAVAILABLE -> FX_UNAVAILABLE;
                    case BAD_RESPONSE -> FX_BAD_RESPONSE;
                };
            }
            String msg = String.valueOf(c.getMessage());
            if (c.getClass().getName().endsWith("PoolExhaustedException") || msg.contains("Pool empty")) {
                return POOL_EXHAUSTED;
            }
            if (c instanceof SQLException sql) {
                String state = sql.getSQLState();
                if ("28P01".equals(state) || "28000".equals(state) || msg.contains("password authentication failed")) {
                    return DB_AUTH;
                }
                if (state != null && state.startsWith("08")) {
                    return DB_UNAVAILABLE;
                }
            }
        }
        return INTERNAL;
    }
}
