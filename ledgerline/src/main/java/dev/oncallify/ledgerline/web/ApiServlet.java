package dev.oncallify.ledgerline.web;

import com.fasterxml.jackson.databind.JsonNode;
import dev.oncallify.ledgerline.domain.Account;
import dev.oncallify.ledgerline.domain.LedgerException;
import dev.oncallify.ledgerline.domain.Transfer;
import dev.oncallify.ledgerline.domain.TransferRequest;
import dev.oncallify.ledgerline.ops.ErrorKind;
import dev.oncallify.ledgerline.service.LedgerService;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import java.io.IOException;
import java.math.BigDecimal;
import java.sql.Connection;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * The public API. Every request is timed and counted; every infrastructure failure is
 * logged with an {@code error_kind}, the request id, and the exception chain summary.
 */
public final class ApiServlet extends HttpServlet {

    @Override
    protected void service(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        AppContext app = AppContext.of(getServletContext());
        String path = req.getPathInfo() == null ? "/" : req.getPathInfo();
        String route = route(req.getMethod(), path);
        String requestId = UUID.randomUUID().toString().substring(0, 8);
        resp.setHeader("X-Request-Id", requestId);
        long start = System.nanoTime();
        int status = 500;
        try {
            status = switch (route) {
                case "GET /health" -> health(app, resp);
                case "GET /accounts/{id}" -> account(app, path.substring("/accounts/".length()), resp);
                case "POST /transfers" -> transfer(app, req, resp);
                default -> {
                    Http.error(resp, 404, "not_found", "no route " + req.getMethod() + " " + path);
                    yield 404;
                }
            };
        } catch (LedgerException e) {
            status = e.httpStatus();
            Http.error(resp, status, e.code().name().toLowerCase(), e.getMessage());
        } catch (Throwable t) {
            ErrorKind kind = ErrorKind.classify(t);
            status = kind == ErrorKind.FX_TIMEOUT ? 504 : (kind == ErrorKind.POOL_EXHAUSTED ? 503 : 500);
            app.metrics().inc("ledgerline_errors_total", "kind", kind.label());
            app.log().error("request failed", errorFields(route, requestId, kind, t));
            if (!resp.isCommitted()) {
                Http.error(resp, status, kind.label(), summary(t));
            }
            if (t instanceof Error err && !(t instanceof OutOfMemoryError)) {
                throw err;
            }
        } finally {
            double seconds = (System.nanoTime() - start) / 1e9;
            app.metrics().inc("ledgerline_http_requests_total", "route", route, "status", Integer.toString(status));
            app.metrics().histogram("ledgerline_http_request_duration_seconds", "route", route).observe(seconds);
        }
    }

    static String route(String method, String path) {
        if (path.equals("/health")) {
            return method + " /health";
        }
        if (path.startsWith("/accounts/") && path.length() > "/accounts/".length()) {
            return method + " /accounts/{id}";
        }
        if (path.equals("/transfers")) {
            return method + " /transfers";
        }
        return method + " other";
    }

    private int health(AppContext app, HttpServletResponse resp) throws IOException {
        Map<String, Object> body = new LinkedHashMap<>();
        boolean dbOk;
        try (Connection c = app.pool().get().getConnection()) {
            dbOk = c.isValid(1);
        } catch (Exception e) {
            dbOk = false;
            body.put("db_error", summary(e));
        }
        body.put("status", dbOk ? "UP" : "DEGRADED");
        body.put("db", dbOk ? "ok" : "fail");
        body.put("config_version", app.config().get().version());
        body.put("config_error", app.config().lastError());
        body.put("pool", Map.of("active", app.pool().active(), "idle", app.pool().idle(), "max", app.pool().max()));
        body.put("started_at", app.startedAt());
        int status = dbOk ? 200 : 503;
        Http.json(resp, status, body);
        return status;
    }

    private int account(AppContext app, String id, HttpServletResponse resp) throws IOException {
        Account a = app.ledger().account(id);
        Http.json(resp, 200, Map.of("id", a.id(), "owner", a.owner(), "currency", a.currency(),
                "balance", a.balance().toPlainString()));
        return 200;
    }

    private int transfer(AppContext app, HttpServletRequest req, HttpServletResponse resp) throws IOException {
        JsonNode body;
        try {
            body = Http.JSON.readTree(req.getReader());
        } catch (IOException e) {
            throw new LedgerException(LedgerException.Code.VALIDATION, "body must be JSON");
        }
        if (body == null || !body.hasNonNull("amount")) {
            throw new LedgerException(LedgerException.Code.VALIDATION, "from, to and amount are required");
        }
        BigDecimal amount;
        try {
            amount = new BigDecimal(body.get("amount").asText());
        } catch (NumberFormatException e) {
            throw new LedgerException(LedgerException.Code.VALIDATION, "amount must be a number");
        }
        TransferRequest request = new TransferRequest(text(body, "from"), text(body, "to"), amount);
        app.faults().maybeRetain();
        LedgerService.Outcome outcome = app.ledger().transfer(request, req.getHeader("Idempotency-Key"));
        Transfer t = outcome.transfer();
        if (t.fxFallbackUsed()) {
            app.metrics().inc("ledgerline_fx_fallback_total");
        }
        int status = outcome.replayed() ? 200 : 201;
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", t.id());
        out.put("from", t.fromAccount());
        out.put("to", t.toAccount());
        out.put("amount", t.amount().toPlainString());
        out.put("fx_rate", t.fxRate().toPlainString());
        out.put("credited", t.credited().toPlainString());
        out.put("fx_fallback", t.fxFallbackUsed());
        out.put("created_at", t.createdAt().toString());
        out.put("replayed", outcome.replayed());
        Http.json(resp, status, out);
        return status;
    }

    private static String text(JsonNode n, String field) {
        JsonNode v = n.get(field);
        return v == null || v.isNull() ? null : v.asText();
    }

    static Map<String, Object> errorFields(String route, String requestId, ErrorKind kind, Throwable t) {
        Map<String, Object> f = new LinkedHashMap<>();
        f.put("route", route);
        f.put("request_id", requestId);
        f.put("error_kind", kind.label());
        f.put("exception", t.getClass().getSimpleName());
        f.put("detail", summary(t));
        return f;
    }

    /** "Outer: msg <- Cause: msg", capped, so log lines stay readable and bounded. */
    static String summary(Throwable t) {
        StringBuilder sb = new StringBuilder();
        int depth = 0;
        for (Throwable c = t; c != null && depth < 4; c = c.getCause(), depth++) {
            if (depth > 0) {
                sb.append(" <- ");
            }
            sb.append(c.getClass().getSimpleName()).append(": ").append(c.getMessage());
        }
        return sb.length() > 400 ? sb.substring(0, 400) : sb.toString();
    }
}
