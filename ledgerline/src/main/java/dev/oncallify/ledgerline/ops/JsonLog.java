package dev.oncallify.ledgerline.ops;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Structured logs, one JSON object per line. These lines are the evidence the
 * on-call agent reads, so every error carries a stable {@code error_kind}.
 */
public final class JsonLog implements AutoCloseable {
    private static final ObjectMapper JSON = new ObjectMapper();
    private final BufferedWriter out;
    private final String service;

    public JsonLog(Path dir, String service) throws IOException {
        Files.createDirectories(dir);
        this.out = Files.newBufferedWriter(dir.resolve(service + ".jsonl"), StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        this.service = service;
    }

    public void info(String msg, Map<String, ?> fields) {
        write("INFO", msg, fields);
    }

    public void warn(String msg, Map<String, ?> fields) {
        write("WARN", msg, fields);
    }

    public void error(String msg, Map<String, ?> fields) {
        write("ERROR", msg, fields);
    }

    private void write(String level, String msg, Map<String, ?> fields) {
        Map<String, Object> line = new LinkedHashMap<>();
        line.put("ts", Instant.now().toString());
        line.put("level", level);
        line.put("service", service);
        line.put("msg", msg);
        if (fields != null) {
            line.putAll(fields);
        }
        try {
            String text = JSON.writeValueAsString(line);
            synchronized (out) {
                out.write(text);
                out.newLine();
                out.flush();
            }
        } catch (IOException e) {
            // logging must never take the request down with it
            System.err.println("jsonlog write failed: " + e.getMessage());
        }
    }

    @Override
    public void close() throws IOException {
        synchronized (out) {
            out.close();
        }
    }
}
