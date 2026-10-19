package dev.oncallify.ledgerline.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletResponse;

import java.io.IOException;
import java.util.Map;

/** Small JSON helpers shared by the servlets. Dates are rendered as ISO strings by callers. */
final class Http {
    static final ObjectMapper JSON = new ObjectMapper();

    private Http() { }

    static void json(HttpServletResponse resp, int status, Object body) throws IOException {
        resp.setStatus(status);
        resp.setContentType("application/json");
        resp.setCharacterEncoding("UTF-8");
        JSON.writeValue(resp.getWriter(), body);
    }

    static void error(HttpServletResponse resp, int status, String kind, String message) throws IOException {
        json(resp, status, Map.of("error", kind, "message", message == null ? "" : message));
    }
}
