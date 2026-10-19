package dev.oncallify.ledgerline.domain;

/** A business-rule failure. These are client errors (4xx), never pages. */
public class LedgerException extends RuntimeException {
    public enum Code { VALIDATION, NOT_FOUND, INSUFFICIENT_FUNDS, CONFLICT }

    private final Code code;

    public LedgerException(Code code, String message) {
        super(message);
        this.code = code;
    }

    public Code code() { return code; }

    public int httpStatus() {
        return switch (code) {
            case VALIDATION -> 400;
            case NOT_FOUND -> 404;
            case INSUFFICIENT_FUNDS -> 422;
            case CONFLICT -> 409;
        };
    }
}
