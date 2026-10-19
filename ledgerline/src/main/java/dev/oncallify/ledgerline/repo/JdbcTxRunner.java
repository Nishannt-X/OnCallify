package dev.oncallify.ledgerline.repo;

import dev.oncallify.ledgerline.domain.Account;
import dev.oncallify.ledgerline.domain.LedgerException;
import dev.oncallify.ledgerline.domain.Transfer;
import dev.oncallify.ledgerline.ops.FaultInjector;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.function.Function;
import java.util.function.Supplier;

/**
 * PostgreSQL implementation. The data source is looked up on every call because the
 * pool is rebuilt when its config changes (pool size, credentials).
 *
 * <p>When the {@code connection_leak} fault is on, a successful transaction "forgets"
 * to close its connection. That is the real bug shape: the pool fills up, then every
 * request waits {@code maxWait} and fails with "Timeout: Pool empty".
 */
public final class JdbcTxRunner implements TxRunner {
    private final Supplier<DataSource> dataSource;
    private final FaultInjector faults;

    public JdbcTxRunner(Supplier<DataSource> dataSource, FaultInjector faults) {
        this.dataSource = dataSource;
        this.faults = faults;
    }

    @Override
    public <T> T inTransaction(Function<Tx, T> work) {
        Connection conn;
        try {
            conn = dataSource.get().getConnection();
        } catch (SQLException e) {
            throw new DataAccessException("could not get a connection", e);
        }
        boolean leak = false;
        try {
            conn.setAutoCommit(false);
            T result = work.apply(new JdbcTx(conn));
            conn.commit();
            leak = faults.isOn(FaultInjector.Fault.CONNECTION_LEAK);
            if (leak) {
                faults.leak(conn);
            }
            return result;
        } catch (SQLException e) {
            rollbackQuietly(conn);
            throw new DataAccessException("transaction failed", e);
        } catch (RuntimeException e) {
            rollbackQuietly(conn);
            throw e;
        } finally {
            if (!leak) {
                closeQuietly(conn);
            }
        }
    }

    private static void rollbackQuietly(Connection conn) {
        try {
            conn.rollback();
        } catch (SQLException ignored) {
            // the original failure is the one worth reporting
        }
    }

    private static void closeQuietly(Connection conn) {
        try {
            conn.close();
        } catch (SQLException ignored) {
            // returning a broken connection to the pool is not actionable here
        }
    }

    /** Thin wrapper so callers see one unchecked exception type for database failures. */
    public static final class DataAccessException extends RuntimeException {
        public DataAccessException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    private record JdbcTx(Connection conn) implements Tx {

        @Override
        public Optional<Account> findAccount(String id) {
            try (PreparedStatement ps = conn.prepareStatement(
                    "SELECT id, owner, currency, balance FROM accounts WHERE id = ?")) {
                ps.setString(1, id);
                try (ResultSet rs = ps.executeQuery()) {
                    return rs.next() ? Optional.of(map(rs)) : Optional.empty();
                }
            } catch (SQLException e) {
                throw new DataAccessException("findAccount failed", e);
            }
        }

        @Override
        public List<Account> lockAccounts(String firstId, String secondId) {
            List<Account> locked = new ArrayList<>(2);
            try (PreparedStatement ps = conn.prepareStatement(
                    "SELECT id, owner, currency, balance FROM accounts WHERE id IN (?, ?) ORDER BY id FOR UPDATE")) {
                ps.setString(1, firstId);
                ps.setString(2, secondId);
                try (ResultSet rs = ps.executeQuery()) {
                    while (rs.next()) {
                        locked.add(map(rs));
                    }
                }
            } catch (SQLException e) {
                throw new DataAccessException("lockAccounts failed", e);
            }
            Account first = locked.stream().filter(a -> a.id().equals(firstId)).findFirst()
                    .orElseThrow(() -> new LedgerException(LedgerException.Code.NOT_FOUND, "account " + firstId + " not found"));
            Account second = locked.stream().filter(a -> a.id().equals(secondId)).findFirst()
                    .orElseThrow(() -> new LedgerException(LedgerException.Code.NOT_FOUND, "account " + secondId + " not found"));
            return List.of(first, second);
        }

        @Override
        public void saveBalance(Account account) {
            try (PreparedStatement ps = conn.prepareStatement("UPDATE accounts SET balance = ? WHERE id = ?")) {
                ps.setBigDecimal(1, account.balance());
                ps.setString(2, account.id());
                ps.executeUpdate();
            } catch (SQLException e) {
                throw new DataAccessException("saveBalance failed", e);
            }
        }

        @Override
        public Optional<Transfer> findTransferByIdempotencyKey(String key) {
            try (PreparedStatement ps = conn.prepareStatement(
                    "SELECT id, idempotency_key, from_account, to_account, amount, fx_rate, credited, fx_fallback, created_at "
                            + "FROM transfers WHERE idempotency_key = ?")) {
                ps.setString(1, key);
                try (ResultSet rs = ps.executeQuery()) {
                    if (!rs.next()) {
                        return Optional.empty();
                    }
                    return Optional.of(new Transfer(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4),
                            rs.getBigDecimal(5), rs.getBigDecimal(6), rs.getBigDecimal(7), rs.getBoolean(8),
                            rs.getTimestamp(9).toInstant()));
                }
            } catch (SQLException e) {
                throw new DataAccessException("findTransfer failed", e);
            }
        }

        @Override
        public void insertTransfer(Transfer t) {
            try (PreparedStatement ps = conn.prepareStatement(
                    "INSERT INTO transfers (id, idempotency_key, from_account, to_account, amount, fx_rate, credited, fx_fallback, created_at) "
                            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")) {
                ps.setString(1, t.id());
                ps.setString(2, t.idempotencyKey());
                ps.setString(3, t.fromAccount());
                ps.setString(4, t.toAccount());
                ps.setBigDecimal(5, t.amount());
                ps.setBigDecimal(6, t.fxRate());
                ps.setBigDecimal(7, t.credited());
                ps.setBoolean(8, t.fxFallbackUsed());
                ps.setTimestamp(9, Timestamp.from(t.createdAt()));
                ps.executeUpdate();
            } catch (SQLException e) {
                if ("23505".equals(e.getSQLState())) {
                    throw new LedgerException(LedgerException.Code.CONFLICT, "duplicate idempotency key");
                }
                throw new DataAccessException("insertTransfer failed", e);
            }
        }

        private static Account map(ResultSet rs) throws SQLException {
            return new Account(rs.getString(1), rs.getString(2), rs.getString(3), rs.getBigDecimal(4));
        }
    }
}
