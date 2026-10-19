package dev.oncallify.ledgerline.web;

import dev.oncallify.ledgerline.ops.FaultInjector;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Stands in for an external FX provider so the demo needs no third-party API. It has
 * realistic latency (20-40 ms) and can be degraded (slow) or broken (503) through the
 * fault switches. It shares the JVM with the app only for convenience.
 */
public final class FxStubServlet extends HttpServlet {

    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        AppContext app = AppContext.of(getServletContext());
        FaultInjector faults = app.faults();
        sleep(ThreadLocalRandom.current().nextLong(20, 41));
        if (faults.isOn(FaultInjector.Fault.FX_SLOW)) {
            sleep(faults.fxDelayMs());
        }
        if (faults.isOn(FaultInjector.Fault.FX_ERRORS)) {
            Http.error(resp, 503, "unavailable", "provider maintenance");
            return;
        }
        String key = req.getParameter("base") + "/" + req.getParameter("quote");
        BigDecimal base = LedgerlineListener.SEED_RATES.get(key);
        if (base == null) {
            Http.error(resp, 404, "unknown_pair", key);
            return;
        }
        double jitter = 1 + ThreadLocalRandom.current().nextDouble(-0.002, 0.002);
        BigDecimal rate = base.multiply(BigDecimal.valueOf(jitter)).setScale(6, RoundingMode.HALF_EVEN);
        Http.json(resp, 200, Map.of("pair", key, "rate", rate.toPlainString()));
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
