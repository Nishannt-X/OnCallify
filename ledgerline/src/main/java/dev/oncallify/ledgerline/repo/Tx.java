package dev.oncallify.ledgerline.repo;

import dev.oncallify.ledgerline.domain.Account;
import dev.oncallify.ledgerline.domain.Transfer;

import java.util.List;
import java.util.Optional;

/** Everything one unit of work may read or write. Implementations decide what "atomic" means. */
public interface Tx {

    Optional<Account> findAccount(String id);

    /**
     * Locks both accounts for update and returns them in the order asked.
     * Locks are always taken in ascending id order so two opposite transfers cannot deadlock.
     */
    List<Account> lockAccounts(String firstId, String secondId);

    void saveBalance(Account account);

    Optional<Transfer> findTransferByIdempotencyKey(String key);

    void insertTransfer(Transfer transfer);
}
