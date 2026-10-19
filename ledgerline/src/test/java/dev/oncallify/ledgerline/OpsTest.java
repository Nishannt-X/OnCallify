package dev.oncallify.ledgerline;

import dev.oncallify.ledgerline.domain.LedgerException;
import dev.oncallify.ledgerline.fx.FxException;
import dev.oncallify.ledgerline.ops.ErrorKind;
import dev.oncallify.ledgerline.ops.FaultInjector;
import org.junit.jupiter.api.Test;

import java.sql.SQLException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class OpsTest {

    @Test
    void classifiesPoolExhaustionThroughWrappers() {
        Throwable t = new RuntimeException("transaction failed",
                new SQLException("[pool] Timeout: Pool empty. Unable to fetch a connection in 0 seconds"));
        assertEquals(ErrorKind.POOL_EXHAUSTED, ErrorKind.classify(t));
    }

    @Test
    void classifiesDatabaseAuthBySqlState() {
        assertEquals(ErrorKind.DB_AUTH, ErrorKind.classify(new SQLException("nope", "28P01")));
        assertEquals(ErrorKind.DB_UNAVAILABLE, ErrorKind.classify(new SQLException("down", "08001")));
    }

    @Test
    void classifiesFxKinds() {
        assertEquals(ErrorKind.FX_TIMEOUT, ErrorKind.classify(new FxException(FxException.Kind.TIMEOUT, "t", null)));
        assertEquals(ErrorKind.FX_UNAVAILABLE, ErrorKind.classify(new FxException(FxException.Kind.UNAVAILABLE, "u", null)));
    }

    @Test
    void businessErrorsAreNotInfrastructure() {
        ErrorKind k = ErrorKind.classify(new LedgerException(LedgerException.Code.INSUFFICIENT_FUNDS, "x"));
        assertEquals(ErrorKind.BUSINESS, k);
        assertFalse(k.infrastructure());
        assertTrue(ErrorKind.classify(new IllegalStateException("?")).infrastructure());
    }

    @Test
    void faultSwitchesParseAndToggle() {
        FaultInjector f = new FaultInjector();
        assertEquals(FaultInjector.Fault.CONNECTION_LEAK, FaultInjector.Fault.parse("connection-leak"));
        assertThrows(IllegalArgumentException.class, () -> FaultInjector.Fault.parse("meteor"));
        f.set(FaultInjector.Fault.MEMORY_LEAK, true);
        f.maybeRetain();
        f.maybeRetain();
        assertEquals(2L * 64 * 1024, f.retainedBytes());
        f.releaseAll();
        assertEquals(0, f.retainedBytes());
        assertFalse(f.isOn(FaultInjector.Fault.MEMORY_LEAK));
    }
}
