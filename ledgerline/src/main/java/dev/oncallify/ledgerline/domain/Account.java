package dev.oncallify.ledgerline.domain;

import java.math.BigDecimal;
import java.util.Objects;

/** A ledger account. Balances are held in the account's own currency. */
public final class Account {
    private final String id;
    private final String owner;
    private final String currency;
    private BigDecimal balance;

    public Account(String id, String owner, String currency, BigDecimal balance) {
        this.id = Objects.requireNonNull(id, "id");
        this.owner = Objects.requireNonNull(owner, "owner");
        this.currency = Objects.requireNonNull(currency, "currency");
        this.balance = Objects.requireNonNull(balance, "balance");
    }

    public String id() { return id; }
    public String owner() { return owner; }
    public String currency() { return currency; }
    public BigDecimal balance() { return balance; }

    /** Removes {@code amount} or throws if the balance would go negative. */
    public void debit(BigDecimal amount) {
        requirePositive(amount);
        if (balance.compareTo(amount) < 0) {
            throw new LedgerException(LedgerException.Code.INSUFFICIENT_FUNDS,
                    "account " + id + " has insufficient funds");
        }
        balance = balance.subtract(amount);
    }

    public void credit(BigDecimal amount) {
        requirePositive(amount);
        balance = balance.add(amount);
    }

    private static void requirePositive(BigDecimal amount) {
        if (amount == null || amount.signum() <= 0) {
            throw new LedgerException(LedgerException.Code.VALIDATION, "amount must be positive");
        }
    }
}
