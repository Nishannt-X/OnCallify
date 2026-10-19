package dev.oncallify.ledgerline.fx;

import java.math.BigDecimal;

/** Source of exchange rates. Same-currency pairs never reach an implementation. */
public interface FxClient {

    /** Result of a lookup: the rate and whether it came from the fallback cache. */
    record Quote(BigDecimal rate, boolean fromFallback) { }

    Quote rate(String base, String quote);
}
