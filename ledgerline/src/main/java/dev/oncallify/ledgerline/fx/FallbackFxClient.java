package dev.oncallify.ledgerline.fx;

import java.math.BigDecimal;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.BooleanSupplier;

/**
 * Decorator: remembers the last good rate per pair, and when the provider fails AND
 * the {@code fx.fallback.enabled} flag is on, serves that cached rate instead of
 * failing the transfer. With the flag off, provider failures propagate.
 *
 * <p>A cached rate is a business decision (stale prices), which is why it sits behind a
 * flag that a human or an approved runbook turns on, never silently.
 */
public final class FallbackFxClient implements FxClient {
    private final FxClient delegate;
    private final BooleanSupplier fallbackEnabled;
    private final Map<String, BigDecimal> lastGood = new ConcurrentHashMap<>();
    private final Map<String, BigDecimal> seed;

    public FallbackFxClient(FxClient delegate, BooleanSupplier fallbackEnabled, Map<String, BigDecimal> seed) {
        this.delegate = delegate;
        this.fallbackEnabled = fallbackEnabled;
        this.seed = Map.copyOf(seed);
    }

    @Override
    public Quote rate(String base, String quote) {
        String key = base + "/" + quote;
        try {
            Quote q = delegate.rate(base, quote);
            lastGood.put(key, q.rate());
            return q;
        } catch (FxException e) {
            if (!fallbackEnabled.getAsBoolean()) {
                throw e;
            }
            BigDecimal cached = lastGood.getOrDefault(key, seed.get(key));
            if (cached == null) {
                throw e;
            }
            return new Quote(cached, true);
        }
    }
}
