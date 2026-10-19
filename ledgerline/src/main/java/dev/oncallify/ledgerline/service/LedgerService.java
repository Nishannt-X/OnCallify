package dev.oncallify.ledgerline.service;

import dev.oncallify.ledgerline.domain.Account;
import dev.oncallify.ledgerline.domain.LedgerException;
import dev.oncallify.ledgerline.domain.Transfer;
import dev.oncallify.ledgerline.domain.TransferRequest;
import dev.oncallify.ledgerline.fx.FxClient;
import dev.oncallify.ledgerline.repo.TxRunner;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Clock;
import java.util.List;
import java.util.UUID;

/**
 * Transfer rules: idempotent by key, both accounts locked, currency converted, no
 * negative balances.
 *
 * <p>Known design choice: the FX lookup happens INSIDE the transaction, while the two
 * account rows are locked and a pooled connection is held. That keeps the rate and the
 * balances consistent, but it means FX latency is paid while holding a connection,
 * which is exactly why pool size and FX health are coupled in this service. It is the
 * kind of trade-off real services carry, and it is what makes the incidents realistic.
 */
public final class LedgerService {
    private final TxRunner tx;
    private final FxClient fx;
    private final Clock clock;

    public LedgerService(TxRunner tx, FxClient fx, Clock clock) {
        this.tx = tx;
        this.fx = fx;
        this.clock = clock;
    }

    public Account account(String id) {
        return tx.inTransaction(t -> t.findAccount(id)
                .orElseThrow(() -> new LedgerException(LedgerException.Code.NOT_FOUND, "account " + id + " not found")));
    }

    /** Result plus whether this call replayed an earlier one with the same key. */
    public record Outcome(Transfer transfer, boolean replayed) { }

    public Outcome transfer(TransferRequest request, String idempotencyKey) {
        if (idempotencyKey == null || idempotencyKey.isBlank() || idempotencyKey.length() > 80) {
            throw new LedgerException(LedgerException.Code.VALIDATION, "Idempotency-Key header is required (max 80 chars)");
        }
        request.validate();
        return tx.inTransaction(t -> {
            var existing = t.findTransferByIdempotencyKey(idempotencyKey);
            if (existing.isPresent()) {
                Transfer prior = existing.get();
                if (!prior.fromAccount().equals(request.fromAccount())
                        || !prior.toAccount().equals(request.toAccount())
                        || prior.amount().compareTo(request.amount()) != 0) {
                    throw new LedgerException(LedgerException.Code.CONFLICT,
                            "Idempotency-Key reused with a different request");
                }
                return new Outcome(prior, true);
            }
            List<Account> pair = t.lockAccounts(request.fromAccount(), request.toAccount());
            Account from = pair.get(0);
            Account to = pair.get(1);

            BigDecimal rate = BigDecimal.ONE;
            boolean fallback = false;
            if (!from.currency().equals(to.currency())) {
                FxClient.Quote q = fx.rate(from.currency(), to.currency());
                rate = q.rate();
                fallback = q.fromFallback();
            }
            BigDecimal credited = request.amount().multiply(rate).setScale(2, RoundingMode.HALF_EVEN);
            from.debit(request.amount());
            to.credit(credited);
            t.saveBalance(from);
            t.saveBalance(to);
            Transfer done = new Transfer(UUID.randomUUID().toString(), idempotencyKey, from.id(), to.id(),
                    request.amount(), rate, credited, fallback, clock.instant());
            t.insertTransfer(done);
            return new Outcome(done, false);
        });
    }
}
