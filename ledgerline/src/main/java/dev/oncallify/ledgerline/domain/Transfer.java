package dev.oncallify.ledgerline.domain;

import java.math.BigDecimal;
import java.time.Instant;

/**
 * A completed transfer. {@code amount} is in the source currency; {@code credited}
 * is what landed in the destination account after conversion at {@code fxRate}.
 */
public record Transfer(
        String id,
        String idempotencyKey,
        String fromAccount,
        String toAccount,
        BigDecimal amount,
        BigDecimal fxRate,
        BigDecimal credited,
        boolean fxFallbackUsed,
        Instant createdAt) {
}
