package dev.oncallify.ledgerline.web;

import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import java.io.IOException;

/** Prometheus scrape endpoint. */
public final class MetricsServlet extends HttpServlet {
    @Override
    protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws IOException {
        AppContext app = AppContext.of(getServletContext());
        app.config().get(); // lets a scrape pick up config changes even when traffic is idle
        resp.setContentType("text/plain; version=0.0.4");
        resp.setCharacterEncoding("UTF-8");
        resp.getWriter().write(app.metrics().render());
    }
}
