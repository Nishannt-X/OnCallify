package dev.oncallify.ledgerline;

import dev.oncallify.ledgerline.metrics.MetricsRegistry;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MetricsRegistryTest {

    @Test
    void countersRenderWithSortedLabels() {
        MetricsRegistry m = new MetricsRegistry();
        m.inc("reqs_total", "status", "500", "route", "POST /transfers");
        m.inc("reqs_total", "route", "POST /transfers", "status", "500");
        String out = m.render();
        assertTrue(out.contains("# TYPE reqs_total counter"), out);
        assertTrue(out.contains("reqs_total{route=\"POST /transfers\",status=\"500\"} 2"), out);
        assertEquals(2, m.counterValue("reqs_total", "route", "POST /transfers", "status", "500"));
    }

    @Test
    void histogramBucketsAreCumulative() {
        MetricsRegistry m = new MetricsRegistry();
        var h = m.histogram("lat_seconds", "route", "r");
        h.observe(0.004);
        h.observe(0.04);
        h.observe(9.0);
        String out = m.render();
        assertTrue(out.contains("lat_seconds_bucket{route=\"r\",le=\"0.005\"} 1"), out);
        assertTrue(out.contains("lat_seconds_bucket{route=\"r\",le=\"0.05\"} 2"), out);
        assertTrue(out.contains("lat_seconds_bucket{route=\"r\",le=\"5\"} 2"), out);
        assertTrue(out.contains("lat_seconds_bucket{route=\"r\",le=\"+Inf\"} 3"), out);
        assertTrue(out.contains("lat_seconds_count{route=\"r\"} 3"), out);
    }

    @Test
    void gaugeFailureRendersNaNInsteadOfBreakingTheScrape() {
        MetricsRegistry m = new MetricsRegistry();
        m.gauge("broken", () -> {
            throw new IllegalStateException("x");
        });
        m.gauge("fine", () -> 3);
        String out = m.render();
        assertTrue(out.contains("broken NaN"), out);
        assertTrue(out.contains("fine 3"), out);
    }

    @Test
    void labelValuesAreEscaped() {
        MetricsRegistry m = new MetricsRegistry();
        m.inc("c", "k", "a\"b");
        assertTrue(m.render().contains("c{k=\"a\\\"b\"} 1"));
    }

    @Test
    void oddLabelListIsRejected() {
        MetricsRegistry m = new MetricsRegistry();
        assertThrows(IllegalArgumentException.class, () -> m.inc("c", "only-key"));
    }
}
