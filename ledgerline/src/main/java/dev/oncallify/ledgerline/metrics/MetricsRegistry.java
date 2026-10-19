package dev.oncallify.ledgerline.metrics;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.DoubleAdder;
import java.util.concurrent.atomic.LongAdder;
import java.util.function.DoubleSupplier;

/**
 * A deliberately small metrics registry that renders the Prometheus text exposition
 * format. Counters and histogram buckets use {@link LongAdder} so the hot path never
 * takes a lock; gauges are sampled at scrape time.
 */
public final class MetricsRegistry {
    public static final double[] DEFAULT_BUCKETS = {0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5};

    private final Map<String, String> help = new ConcurrentHashMap<>();
    private final Map<String, LongAdder> counters = new ConcurrentHashMap<>();
    private final Map<String, DoubleSupplier> gauges = new ConcurrentHashMap<>();
    private final Map<String, Histogram> histograms = new ConcurrentHashMap<>();

    public void describe(String name, String text) {
        help.put(name, text);
    }

    public void inc(String name, String... labels) {
        counters.computeIfAbsent(key(name, labels), k -> new LongAdder()).increment();
    }

    public long counterValue(String name, String... labels) {
        LongAdder a = counters.get(key(name, labels));
        return a == null ? 0 : a.sum();
    }

    public void gauge(String name, DoubleSupplier supplier, String... labels) {
        gauges.put(key(name, labels), supplier);
    }

    public Histogram histogram(String name, String... labels) {
        return histograms.computeIfAbsent(key(name, labels), k -> new Histogram(DEFAULT_BUCKETS));
    }

    /** Label pairs are passed flat: {@code "route", "/api/transfers", "status", "500"}. */
    static String key(String name, String... labels) {
        if (labels.length % 2 != 0) {
            throw new IllegalArgumentException("labels must be key/value pairs");
        }
        if (labels.length == 0) {
            return name;
        }
        TreeMap<String, String> sorted = new TreeMap<>();
        for (int i = 0; i < labels.length; i += 2) {
            sorted.put(labels[i], labels[i + 1]);
        }
        StringBuilder sb = new StringBuilder(name).append('{');
        boolean first = true;
        for (Map.Entry<String, String> e : sorted.entrySet()) {
            if (!first) {
                sb.append(',');
            }
            first = false;
            sb.append(e.getKey()).append("=\"").append(escape(e.getValue())).append('"');
        }
        return sb.append('}').toString();
    }

    private static String escape(String v) {
        return v.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }

    private static String baseName(String key) {
        int i = key.indexOf('{');
        return i < 0 ? key : key.substring(0, i);
    }

    private static String labelPart(String key) {
        int i = key.indexOf('{');
        return i < 0 ? "" : key.substring(i + 1, key.length() - 1);
    }

    public String render() {
        StringBuilder out = new StringBuilder(4096);
        TreeMap<String, List<String>> lines = new TreeMap<>();
        TreeMap<String, String> types = new TreeMap<>();

        counters.forEach((k, v) -> {
            types.put(baseName(k), "counter");
            lines.computeIfAbsent(baseName(k), x -> new ArrayList<>()).add(k + " " + v.sum());
        });
        gauges.forEach((k, s) -> {
            types.put(baseName(k), "gauge");
            double value;
            try {
                value = s.getAsDouble();
            } catch (RuntimeException e) {
                value = Double.NaN;
            }
            lines.computeIfAbsent(baseName(k), x -> new ArrayList<>()).add(k + " " + fmt(value));
        });
        histograms.forEach((k, h) -> {
            String base = baseName(k);
            String labels = labelPart(k);
            types.put(base, "histogram");
            List<String> l = lines.computeIfAbsent(base, x -> new ArrayList<>());
            long[] cumulative = h.cumulativeCounts();
            double[] bounds = h.bounds();
            for (int i = 0; i < bounds.length; i++) {
                l.add(base + "_bucket{" + join(labels, "le=\"" + fmt(bounds[i]) + "\"") + "} " + cumulative[i]);
            }
            l.add(base + "_bucket{" + join(labels, "le=\"+Inf\"") + "} " + h.count());
            l.add(base + "_sum" + wrap(labels) + " " + fmt(h.sum()));
            l.add(base + "_count" + wrap(labels) + " " + h.count());
        });

        for (Map.Entry<String, List<String>> e : lines.entrySet()) {
            String name = e.getKey();
            if (help.containsKey(name)) {
                out.append("# HELP ").append(name).append(' ').append(help.get(name)).append('\n');
            }
            out.append("# TYPE ").append(name).append(' ').append(types.get(name)).append('\n');
            e.getValue().stream().sorted().forEach(line -> out.append(line).append('\n'));
        }
        return out.toString();
    }

    private static String join(String a, String b) {
        return a.isEmpty() ? b : a + "," + b;
    }

    private static String wrap(String labels) {
        return labels.isEmpty() ? "" : "{" + labels + "}";
    }

    static String fmt(double v) {
        if (Double.isNaN(v)) {
            return "NaN";
        }
        if (v == Math.rint(v) && Math.abs(v) < 1e15) {
            return Long.toString((long) v);
        }
        return Double.toString(v);
    }

    /** Fixed-bucket histogram. Buckets are upper bounds in seconds. */
    public static final class Histogram {
        private final double[] bounds;
        private final LongAdder[] buckets;
        private final LongAdder count = new LongAdder();
        private final DoubleAdder sum = new DoubleAdder();

        Histogram(double[] bounds) {
            this.bounds = Arrays.copyOf(bounds, bounds.length);
            this.buckets = new LongAdder[bounds.length];
            for (int i = 0; i < bounds.length; i++) {
                buckets[i] = new LongAdder();
            }
        }

        public void observe(double seconds) {
            count.increment();
            sum.add(seconds);
            for (int i = 0; i < bounds.length; i++) {
                if (seconds <= bounds[i]) {
                    buckets[i].increment();
                    return;
                }
            }
        }

        public long count() { return count.sum(); }
        public double sum() { return sum.sum(); }
        double[] bounds() { return Arrays.copyOf(bounds, bounds.length); }

        long[] cumulativeCounts() {
            long[] out = new long[bounds.length];
            long running = 0;
            for (int i = 0; i < bounds.length; i++) {
                running += buckets[i].sum();
                out[i] = running;
            }
            return out;
        }
    }
}
