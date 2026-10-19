package dev.oncallify.ledgerline.repo;

import java.util.function.Function;

/** Runs work inside one transaction: commit on return, roll back on any exception. */
public interface TxRunner {
    <T> T inTransaction(Function<Tx, T> work);
}
