package dev.oncallify.ledgerline.domain;

import java.math.BigDecimal;

/** What a client asks for. Validated by {@link #validate()} before any I/O. */
public record TransferRequest(String fromAccount, String toAccount, BigDecimal amount) {

    public void validate() {
        if (fromAccount == null || fromAccount.isBlank() || toAccount == null || toAccount.isBlank()) {
            throw new LedgerException(LedgerException.Code.VALIDATION, "from and to are required");
        }
        if (fromAccount.equals(toAccount)) {
            throw new LedgerException(LedgerException.Code.VALIDATION, "cannot transfer to the same account");
        }
        if (amount == null || amount.signum() <= 0) {
            throw new LedgerException(LedgerException.Code.VALIDATION, "amount must be positive");
        }
        if (amount.scale() > 2) {
            throw new LedgerException(LedgerException.Code.VALIDATION, "amount has more than 2 decimal places");
        }
    }
}
