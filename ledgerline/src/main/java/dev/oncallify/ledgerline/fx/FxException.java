package dev.oncallify.ledgerline.fx;

/** The FX provider could not answer. {@link Kind} drives the log's error_kind. */
public class FxException extends RuntimeException {
    public enum Kind { TIMEOUT, UNAVAILABLE, BAD_RESPONSE }

    private final Kind kind;

    public FxException(Kind kind, String message, Throwable cause) {
        super(message, cause);
        this.kind = kind;
    }

    public Kind kind() { return kind; }
}
