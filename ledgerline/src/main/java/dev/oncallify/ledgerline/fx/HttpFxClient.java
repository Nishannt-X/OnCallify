package dev.oncallify.ledgerline.fx;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.oncallify.ledgerline.config.AppConfig;

import java.io.IOException;
import java.math.BigDecimal;
import java.net.ConnectException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpConnectTimeoutException;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.function.Supplier;

/**
 * Calls the FX provider over HTTP. URL and timeout are read from the live config on
 * every call, so a bad config deploy (wrong URL) breaks this immediately and a
 * rollback fixes it immediately, with no restart.
 */
public final class HttpFxClient implements FxClient {
    private static final ObjectMapper JSON = new ObjectMapper();
    private final Supplier<AppConfig> config;
    private final HttpClient http;

    public HttpFxClient(Supplier<AppConfig> config) {
        this.config = config;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofMillis(500))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    @Override
    public Quote rate(String base, String quote) {
        AppConfig cfg = config.get();
        URI uri = URI.create(cfg.fxUrl() + "?base=" + enc(base) + "&quote=" + enc(quote));
        HttpRequest req = HttpRequest.newBuilder(uri)
                .timeout(Duration.ofMillis(cfg.fxTimeoutMs()))
                .GET()
                .build();
        HttpResponse<String> resp;
        try {
            resp = http.send(req, HttpResponse.BodyHandlers.ofString());
        } catch (HttpConnectTimeoutException e) {
            throw new FxException(FxException.Kind.UNAVAILABLE, "fx connect timeout to " + uri.getAuthority(), e);
        } catch (HttpTimeoutException e) {
            throw new FxException(FxException.Kind.TIMEOUT,
                    "fx request timed out after " + cfg.fxTimeoutMs() + " ms", e);
        } catch (ConnectException e) {
            throw new FxException(FxException.Kind.UNAVAILABLE, "fx connection refused at " + uri.getAuthority(), e);
        } catch (IOException e) {
            throw new FxException(FxException.Kind.UNAVAILABLE, "fx I/O error: " + e.getMessage(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new FxException(FxException.Kind.UNAVAILABLE, "interrupted", e);
        }
        if (resp.statusCode() >= 500) {
            throw new FxException(FxException.Kind.UNAVAILABLE, "fx provider returned " + resp.statusCode(), null);
        }
        if (resp.statusCode() != 200) {
            throw new FxException(FxException.Kind.BAD_RESPONSE, "fx provider returned " + resp.statusCode(), null);
        }
        try {
            JsonNode body = JSON.readTree(resp.body());
            return new Quote(new BigDecimal(body.get("rate").asText()), false);
        } catch (IOException | RuntimeException e) {
            throw new FxException(FxException.Kind.BAD_RESPONSE, "unparseable fx response", e);
        }
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }
}
