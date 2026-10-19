package dev.oncallify.ledgerline;

import dev.oncallify.ledgerline.config.AppConfig;
import dev.oncallify.ledgerline.config.ConfigSource;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ConfigSourceTest {

    private static String props(int poolMax, String fxUrl) {
        return "db.url=jdbc:postgresql://localhost/ledgerline\n"
                + "db.user=ledgerline\n"
                + "db.password.file=secret.txt\n"
                + "pool.max=" + poolMax + "\n"
                + "fx.url=" + fxUrl + "\n"
                + "faults.enabled=true\n";
    }

    /** Writes and bumps mtime forward so filesystems with coarse timestamps still see a change. */
    private static void write(Path p, String text, int bumpSeconds) throws IOException {
        Files.writeString(p, text);
        Files.setLastModifiedTime(p, FileTime.from(Instant.now().plusSeconds(bumpSeconds)));
    }

    @Test
    void loadsAndResolvesPasswordRelativeToConfigFile(@TempDir Path dir) throws IOException {
        Path cfg = dir.resolve("ledgerline.properties");
        write(cfg, props(10, "http://fx/rate"), 0);
        write(dir.resolve("secret.txt"), "s3cret\n", 0);
        AppConfig c = new ConfigSource(cfg, 0).get();
        assertEquals("s3cret", c.dbPassword());
        assertEquals(10, c.poolMax());
        assertTrue(c.faultsEnabled());
        assertFalse(c.fxFallbackEnabled());
    }

    @Test
    void changeProducesNewVersionAndNotifiesListeners(@TempDir Path dir) throws IOException {
        Path cfg = dir.resolve("ledgerline.properties");
        write(cfg, props(10, "http://fx/rate"), 0);
        write(dir.resolve("secret.txt"), "a", 0);
        ConfigSource src = new ConfigSource(cfg, 0);
        List<AppConfig> seen = new ArrayList<>();
        src.addListener(seen::add);

        write(cfg, props(2, "http://fx/rate"), 5);
        assertTrue(src.refreshIfChanged());
        assertEquals(2, src.get().poolMax());
        assertEquals(2, src.get().version());
        assertEquals(1, seen.size());
        assertFalse(src.refreshIfChanged(), "no change, no reload");
    }

    @Test
    void passwordFileChangeAloneTriggersReload(@TempDir Path dir) throws IOException {
        Path cfg = dir.resolve("ledgerline.properties");
        write(cfg, props(10, "http://fx/rate"), 0);
        Path secret = dir.resolve("secret.txt");
        write(secret, "old", 0);
        ConfigSource src = new ConfigSource(cfg, 0);
        write(secret, "new", 5);
        assertTrue(src.refreshIfChanged());
        assertEquals("new", src.get().dbPassword());
    }

    @Test
    void brokenFileKeepsLastGoodConfigAndRecordsTheError(@TempDir Path dir) throws IOException {
        Path cfg = dir.resolve("ledgerline.properties");
        write(cfg, props(10, "http://fx/rate"), 0);
        write(dir.resolve("secret.txt"), "a", 0);
        ConfigSource src = new ConfigSource(cfg, 0);
        write(cfg, props(10, "http://fx/rate").replace("pool.max=10", "pool.max=lots"), 5);
        assertFalse(src.refreshIfChanged());
        assertEquals(10, src.get().poolMax());
        assertNotNull(src.lastError());

        write(cfg, props(12, "http://fx/rate"), 10);
        assertTrue(src.refreshIfChanged());
        assertNull(src.lastError());
        assertEquals(12, src.get().poolMax());
    }

    @Test
    void outOfRangePoolSizeIsRejectedAtStartup(@TempDir Path dir) throws IOException {
        Path cfg = dir.resolve("ledgerline.properties");
        write(cfg, props(0, "http://fx/rate"), 0);
        write(dir.resolve("secret.txt"), "a", 0);
        assertThrows(IllegalArgumentException.class, () -> new ConfigSource(cfg, 0));
    }
}
