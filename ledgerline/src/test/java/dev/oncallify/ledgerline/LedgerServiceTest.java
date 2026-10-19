package dev.oncallify.ledgerline;

import dev.oncallify.ledgerline.domain.Account;
import dev.oncallify.ledgerline.domain.LedgerException;
import dev.oncallify.ledgerline.domain.TransferRequest;
import dev.oncallify.ledgerline.fx.FxClient;
import dev.oncallify.ledgerline.fx.FxException;
import dev.oncallify.ledgerline.repo.InMemoryTxRunner;
import dev.oncallify.ledgerline.service.LedgerService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class LedgerServiceTest {
    private InMemoryTxRunner store;
    private AtomicInteger fxCalls;
    private FxClient fx;
    private LedgerService service;

    @BeforeEach
    void setUp() {
        store = new InMemoryTxRunner();
        store.put(new Account("a", "alice", "USD", new BigDecimal("100.00")));
        store.put(new Account("b", "bob", "USD", new BigDecimal("5.00")));
        store.put(new Account("c", "chitra", "INR", new BigDecimal("0.00")));
        fxCalls = new AtomicInteger();
        fx = (base, quote) -> {
            fxCalls.incrementAndGet();
            return new FxClient.Quote(new BigDecimal("83.20"), false);
        };
        service = new LedgerService(store, fx, Clock.fixed(Instant.parse("2026-10-20T00:00:00Z"), ZoneOffset.UTC));
    }

    private BigDecimal balance(String id) {
        return store.snapshot(id).orElseThrow().balance();
    }

    @Test
    void sameCurrencyTransferMovesMoneyWithoutCallingFx() {
        var out = service.transfer(new TransferRequest("a", "b", new BigDecimal("40.00")), "k1");
        assertFalse(out.replayed());
        assertEquals(new BigDecimal("60.00"), balance("a"));
        assertEquals(new BigDecimal("45.00"), balance("b"));
        assertEquals(0, fxCalls.get());
    }

    @Test
    void crossCurrencyTransferConvertsAtQuotedRate() {
        var out = service.transfer(new TransferRequest("a", "c", new BigDecimal("10.00")), "k2");
        assertEquals(new BigDecimal("832.00"), out.transfer().credited());
        assertEquals(new BigDecimal("832.00"), balance("c"));
        assertEquals(1, fxCalls.get());
    }

    @Test
    void insufficientFundsRollsBackEverything() {
        LedgerException e = assertThrows(LedgerException.class,
                () -> service.transfer(new TransferRequest("b", "a", new BigDecimal("6.00")), "k3"));
        assertEquals(LedgerException.Code.INSUFFICIENT_FUNDS, e.code());
        assertEquals(new BigDecimal("5.00"), balance("b"));
        assertEquals(new BigDecimal("100.00"), balance("a"));
        assertEquals(0, store.transferCount());
    }

    @Test
    void sameKeySameRequestReplaysInsteadOfChargingTwice() {
        var req = new TransferRequest("a", "b", new BigDecimal("1.00"));
        var first = service.transfer(req, "dup");
        var second = service.transfer(req, "dup");
        assertTrue(second.replayed());
        assertEquals(first.transfer().id(), second.transfer().id());
        assertEquals(new BigDecimal("99.00"), balance("a"));
        assertEquals(1, store.transferCount());
    }

    @Test
    void sameKeyDifferentRequestIsAConflict() {
        service.transfer(new TransferRequest("a", "b", new BigDecimal("1.00")), "dup2");
        LedgerException e = assertThrows(LedgerException.class,
                () -> service.transfer(new TransferRequest("a", "b", new BigDecimal("2.00")), "dup2"));
        assertEquals(LedgerException.Code.CONFLICT, e.code());
    }

    @Test
    void missingIdempotencyKeyIsRejectedBeforeAnyWork() {
        assertThrows(LedgerException.class,
                () -> service.transfer(new TransferRequest("a", "b", BigDecimal.ONE), " "));
        assertEquals(0, store.transferCount());
    }

    @Test
    void fxFailureFailsTheTransferAndLeavesBalancesAlone() {
        LedgerService broken = new LedgerService(store, (b, q) -> {
            throw new FxException(FxException.Kind.TIMEOUT, "slow", null);
        }, Clock.systemUTC());
        assertThrows(FxException.class,
                () -> broken.transfer(new TransferRequest("a", "c", new BigDecimal("10.00")), "k4"));
        assertEquals(new BigDecimal("100.00"), balance("a"));
        assertEquals(new BigDecimal("0.00"), balance("c"));
    }

    @Test
    void unknownAccountIsNotFound() {
        LedgerException e = assertThrows(LedgerException.class,
                () -> service.transfer(new TransferRequest("a", "zzz", BigDecimal.ONE), "k5"));
        assertEquals(LedgerException.Code.NOT_FOUND, e.code());
    }

    @Test
    void requestValidationCatchesBadInput() {
        assertThrows(LedgerException.class, () -> new TransferRequest("a", "a", BigDecimal.ONE).validate());
        assertThrows(LedgerException.class, () -> new TransferRequest("a", "b", new BigDecimal("-1")).validate());
        assertThrows(LedgerException.class, () -> new TransferRequest("a", "b", new BigDecimal("1.001")).validate());
        assertThrows(LedgerException.class, () -> new TransferRequest(null, "b", BigDecimal.ONE).validate());
    }
}
