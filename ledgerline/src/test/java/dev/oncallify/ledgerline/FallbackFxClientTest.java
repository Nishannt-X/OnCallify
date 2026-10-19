package dev.oncallify.ledgerline;

import dev.oncallify.ledgerline.fx.FallbackFxClient;
import dev.oncallify.ledgerline.fx.FxClient;
import dev.oncallify.ledgerline.fx.FxException;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FallbackFxClientTest {

    /** A provider that works until it is told to fail. */
    static final class Switchable implements FxClient {
        final AtomicBoolean down = new AtomicBoolean();
        @Override
        public Quote rate(String base, String quote) {
            if (down.get()) {
                throw new FxException(FxException.Kind.TIMEOUT, "timed out", null);
            }
            return new Quote(new BigDecimal("80.00"), false);
        }
    }

    @Test
    void flagOffPropagatesProviderFailure() {
        Switchable provider = new Switchable();
        FxClient fx = new FallbackFxClient(provider, () -> false, Map.of("USD/INR", new BigDecimal("83.20")));
        provider.down.set(true);
        assertThrows(FxException.class, () -> fx.rate("USD", "INR"));
    }

    @Test
    void flagOnServesLastGoodRateAndSaysSo() {
        Switchable provider = new Switchable();
        FxClient fx = new FallbackFxClient(provider, () -> true, Map.of("USD/INR", new BigDecimal("83.20")));
        assertFalse(fx.rate("USD", "INR").fromFallback());
        provider.down.set(true);
        FxClient.Quote q = fx.rate("USD", "INR");
        assertTrue(q.fromFallback());
        assertEquals(new BigDecimal("80.00"), q.rate());
    }

    @Test
    void flagOnUsesSeedWhenNothingWasCachedYet() {
        Switchable provider = new Switchable();
        provider.down.set(true);
        FxClient fx = new FallbackFxClient(provider, () -> true, Map.of("USD/INR", new BigDecimal("83.20")));
        assertEquals(new BigDecimal("83.20"), fx.rate("USD", "INR").rate());
        assertThrows(FxException.class, () -> fx.rate("EUR", "JPY"));
    }
}
