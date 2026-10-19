import { readFileSync } from 'node:fs';
import { RuleEngine, type Rule, type Transition } from './alerts';
import { createApp } from './app';
import { MemoryStore, MongoStore, type Store } from './store';

function log(msg: string, fields: Record<string, unknown> = {}): void {
  process.stdout.write(JSON.stringify({ ts: new Date().toISOString(), service: 'gateway', msg, ...fields }) + '\n');
}

/** Posts alert transitions to the agent, retrying with backoff; gives up after 5 tries. */
function agentNotifier(agentUrl: string, token: string): (t: Transition) => Promise<void> {
  return async (t) => {
    let delay = 250;
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      try {
        const res = await fetch(`${agentUrl}/alerts`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-ingest-token': token },
          body: JSON.stringify(t.alert),
          signal: AbortSignal.timeout(5000),
        });
        if (res.ok) {
          log('alert delivered', { fingerprint: t.alert.fingerprint, status: t.alert.status, rule: t.alert.rule });
          return;
        }
        log('alert rejected by agent', { status: res.status, attempt });
      } catch (err) {
        log('alert delivery failed', { attempt, error: String(err) });
      }
      await new Promise((r) => setTimeout(r, delay));
      delay *= 2;
    }
    throw new Error(`gave up delivering ${t.alert.fingerprint}`);
  };
}

async function main(): Promise<void> {
  const port = Number(process.env.GATEWAY_PORT ?? 7070);
  const rulesPath = process.env.GATEWAY_RULES ?? `${__dirname}/../rules.json`;
  const rules = JSON.parse(readFileSync(rulesPath, 'utf8')) as Rule[];
  const ingestToken = process.env.ONCALLIFY_INGEST_TOKEN ?? '';
  if (!ingestToken) throw new Error('ONCALLIFY_INGEST_TOKEN is required');
  const agentUrl = process.env.AGENT_URL ?? 'http://127.0.0.1:7080';

  let store: Store;
  if (process.env.MONGO_URL) {
    store = await MongoStore.connect(process.env.MONGO_URL, process.env.MONGO_DB ?? 'oncallify');
    log('using MongoDB', { db: process.env.MONGO_DB ?? 'oncallify' });
  } else {
    store = new MemoryStore();
    log('MONGO_URL not set: using in-memory store (data is lost on restart)');
  }

  const app = createApp({
    store,
    engine: new RuleEngine(rules),
    notify: agentNotifier(agentUrl, ingestToken),
    ingestToken,
    log,
  });
  const server = app.listen(port, '127.0.0.1', () => log('gateway listening', { port, rules: rules.length }));
  const shutdown = (): void => {
    server.close(() => {
      store.close().finally(() => process.exit(0));
    });
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

main().catch((err) => {
  log('fatal', { error: String(err) });
  process.exit(1);
});
