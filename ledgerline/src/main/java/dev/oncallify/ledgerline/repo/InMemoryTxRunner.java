package dev.oncallify.ledgerline.repo;

import dev.oncallify.ledgerline.domain.Account;
import dev.oncallify.ledgerline.domain.LedgerException;
import dev.oncallify.ledgerline.domain.Transfer;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.Function;

/**
 * Single-lock, copy-on-write store used by unit tests. A transaction works on a
 * snapshot of balances and publishes it only if the work returns normally, so a
 * thrown exception really does roll back.
 */
public final class InMemoryTxRunner implements TxRunner {
    private final ReentrantLock lock = new ReentrantLock();
    private final Map<String, Account> accounts = new HashMap<>();
    private final Map<String, Transfer> transfersByKey = new HashMap<>();

    public void put(Account account) {
        lock.lock();
        try {
            accounts.put(account.id(), copy(account));
        } finally {
            lock.unlock();
        }
    }

    public Optional<Account> snapshot(String id) {
        lock.lock();
        try {
            return Optional.ofNullable(accounts.get(id)).map(InMemoryTxRunner::copy);
        } finally {
            lock.unlock();
        }
    }

    public int transferCount() {
        lock.lock();
        try {
            return transfersByKey.size();
        } finally {
            lock.unlock();
        }
    }

    @Override
    public <T> T inTransaction(Function<Tx, T> work) {
        lock.lock();
        try {
            Map<String, Account> working = new HashMap<>();
            accounts.forEach((k, v) -> working.put(k, copy(v)));
            List<Transfer> inserted = new ArrayList<>();
            T result = work.apply(new Tx() {
                @Override
                public Optional<Account> findAccount(String id) {
                    return Optional.ofNullable(working.get(id));
                }

                @Override
                public List<Account> lockAccounts(String firstId, String secondId) {
                    Account a = working.get(firstId);
                    Account b = working.get(secondId);
                    if (a == null || b == null) {
                        throw new LedgerException(LedgerException.Code.NOT_FOUND, "account not found");
                    }
                    return List.of(a, b);
                }

                @Override
                public void saveBalance(Account account) {
                    working.put(account.id(), account);
                }

                @Override
                public Optional<Transfer> findTransferByIdempotencyKey(String key) {
                    return Optional.ofNullable(transfersByKey.get(key));
                }

                @Override
                public void insertTransfer(Transfer transfer) {
                    if (transfersByKey.containsKey(transfer.idempotencyKey())) {
                        throw new LedgerException(LedgerException.Code.CONFLICT, "duplicate idempotency key");
                    }
                    inserted.add(transfer);
                }
            });
            accounts.clear();
            accounts.putAll(working);
            inserted.forEach(t -> transfersByKey.put(t.idempotencyKey(), t));
            return result;
        } finally {
            lock.unlock();
        }
    }

    private static Account copy(Account a) {
        return new Account(a.id(), a.owner(), a.currency(), new BigDecimal(a.balance().toPlainString()));
    }
}
