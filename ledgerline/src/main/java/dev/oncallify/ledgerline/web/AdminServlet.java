package dev.oncallify.ledgerline.web;

import com.fasterxml.jackson.databind.JsonNode;
import dev.oncallify.ledgerline.config.AppConfig;
import dev.oncallify.ledgerline.ops.FaultInjector;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;

/**
 * Fault switches for experiments. Disabled unless {@code faults.enabled=true}, and every
 * call needs the admin token (compared in constant time). Never enable in production.
 */
public final class AdminServlet extends HttpServlet {

    @Override
    protected void service(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        AppContext app = AppContext.of(getServletContext());
        AppConfig cfg = app.config().get();
        if (!cfg.faultsEnabled()) {
            Http.error(resp, 404, "not_found", "fault injection is disabled");
            return;
        }
        if (!tokenMatches(cfg.adminToken(), req.getHeader("X-Admin-Token"))) {
            Http.error(resp, 401, "unauthorized", "missing or wrong X-Admin-Token");
            return;
        }
        String path = req.getPathInfo() == null ? "/" : req.getPathInfo();
        if ("GET".equals(req.getMethod()) && "/faults".equals(path)) {
            Http.json(resp, 200, Map.of("faults", app.faults().snapshot(), "fx_delay_ms", app.faults().fxDelayMs(),
                    "leaked_connections", app.faults().leakedConnections(), "retained_bytes", app.faults().retainedBytes()));
            return;
        }
        if ("POST".equals(req.getMethod()) && "/faults".equals(path)) {
            JsonNode body = Http.JSON.readTree(req.getReader());
            FaultInjector.Fault fault;
            try {
                fault = FaultInjector.Fault.parse(body.get("fault").asText());
            } catch (RuntimeException e) {
                Http.error(resp, 400, "validation", "unknown fault");
                return;
            }
            boolean on = body.path("enabled").asBoolean(true);
            if (body.has("fx_delay_ms")) {
                app.faults().setFxDelayMs(Math.max(0, Math.min(30_000, body.get("fx_delay_ms").asLong())));
            }
            app.faults().set(fault, on);
            // deliberately NOT in the service log: the agent must diagnose from symptoms,
            // not read the answer out of an "injected fault X" line
            Http.json(resp, 200, Map.of("fault", fault.name().toLowerCase(), "enabled", on));
            return;
        }
        Http.error(resp, 404, "not_found", "no route");
    }

    static boolean tokenMatches(String expected, String given) {
        if (expected == null || expected.isEmpty() || given == null) {
            return false;
        }
        return MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8), given.getBytes(StandardCharsets.UTF_8));
    }
}
