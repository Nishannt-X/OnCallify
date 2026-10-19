package dev.oncallify.ledgerline.ops;

import java.sql.Connection;
import java.util.ArrayList;
import java.util.Collections;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * In-process fault switches for the failures that live inside the code. Config and
 * credential failures are NOT here on purpose: they are caused from outside (a deploy,
 * a rotated secret), exactly as in production.
 *
 * <p>All state is in memory, so a Tomcat context reload clears it. That is also true of
 * the real bugs these imitate, which is why "restart" is a real (temporary) fix for a leak.
 */
public final class FaultInjector {

    public enum Fault {
        /** Transactions commit but never return their connection to the pool. */
        CONNECTION_LEAK,
        /** Every transfer request pins 64 KB that is never released: a gradual leak, like real ones. */
        MEMORY_LEAK,
        /** The FX stub answers slowly (provider degradation). */
        FX_SLOW,
        /** The FX stub returns 503 (provider outage or a short blip). */
        FX_ERRORS;

        public static Fault parse(String raw) {
            return Fault.valueOf(raw.trim().toUpperCase().replace('-', '_'));
        }
    }

    static final int RETAINED_PER_REQUEST = 64 * 1024;

    private final Map<Fault, Boolean> state = Collections.synchronizedMap(new EnumMap<>(Fault.class));
    private final List<Connection> leaked = Collections.synchronizedList(new ArrayList<>());
    private final List<byte[]> retained = Collections.synchronizedList(new ArrayList<>());
    private final AtomicLong fxDelayMs = new AtomicLong(3_000);

    public boolean isOn(Fault fault) {
        return Boolean.TRUE.equals(state.get(fault));
    }

    public void set(Fault fault, boolean on) {
        state.put(fault, on);
    }

    public Map<String, Boolean> snapshot() {
        Map<String, Boolean> out = new java.util.TreeMap<>();
        for (Fault f : Fault.values()) {
            out.put(f.name().toLowerCase(), isOn(f));
        }
        return out;
    }

    public void leak(Connection conn) {
        leaked.add(conn);
    }

    public int leakedConnections() {
        return leaked.size();
    }

    /** Called per transfer; keeps a buffer alive forever while MEMORY_LEAK is on. */
    public void maybeRetain() {
        if (isOn(Fault.MEMORY_LEAK)) {
            retained.add(new byte[RETAINED_PER_REQUEST]);
        }
    }

    public long retainedBytes() {
        return (long) retained.size() * RETAINED_PER_REQUEST;
    }

    public long fxDelayMs() {
        return fxDelayMs.get();
    }

    public void setFxDelayMs(long ms) {
        fxDelayMs.set(ms);
    }

    /** Releases everything this instance is holding (context shutdown). */
    public void releaseAll() {
        synchronized (leaked) {
            for (Connection c : leaked) {
                try {
                    c.close();
                } catch (Exception ignored) {
                    // shutting down
                }
            }
            leaked.clear();
        }
        retained.clear();
        state.clear();
    }
}
