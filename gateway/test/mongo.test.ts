import { MongoStore } from '../src/store';
import type { StoredEvent } from '../src/types';

/**
 * Runs against a real MongoDB when MONGO_URL is set (CI starts a mongo:7 service).
 * Skipped otherwise, so the unit suite needs no database.
 */
const url = process.env.MONGO_URL;
const maybe = url ? describe : describe.skip;

maybe('MongoStore (integration)', () => {
  let store: MongoStore;
  const db = `oncallify_test_${Date.now()}`;

  beforeAll(async () => {
    store = await MongoStore.connect(url!, db);
  });

  afterAll(async () => {
    await store.close();
  });

  const ev = (id: string, ts: string): StoredEvent => ({
    event_id: id,
    ts,
    service: 'ledgerline',
    type: 'log',
    body: { msg: 'x' },
    received_at: ts,
    late: false,
  });

  test('duplicate event ids are skipped, others in the same batch are kept', async () => {
    const first = await store.insertEvents([ev('m-1', '2026-10-20T00:00:01Z'), ev('m-2', '2026-10-20T00:00:02Z')]);
    expect(first).toMatchObject({ inserted: 2, duplicates: 0 });
    const second = await store.insertEvents([ev('m-2', '2026-10-20T00:00:02Z'), ev('m-3', '2026-10-20T00:00:03Z')]);
    expect(second).toEqual({ inserted: 1, duplicates: 1, insertedIds: ['m-3'] });
    const rows = await store.queryEvents({ service: 'ledgerline', limit: 10 });
    expect(rows.map((r) => r.event_id)).toEqual(['m-1', 'm-2', 'm-3']);
  });

  test('assignment and exposure are unique per (experiment, unit)', async () => {
    const a = { experiment: 'e', unit: 'u', variant: 'A', bucket: 1, assigned_at: 'now' };
    expect(await store.recordAssignment(a)).toMatchObject({ variant: 'A' });
    expect(await store.recordAssignment({ ...a, variant: 'B' })).toMatchObject({ variant: 'A' });
    expect(await store.recordExposure({ experiment: 'e', unit: 'u', variant: 'A', exposed_at: 'now' })).toBe(true);
    expect(await store.recordExposure({ experiment: 'e', unit: 'u', variant: 'A', exposed_at: 'now' })).toBe(false);
    expect(await store.countByVariant('e', 'assignments')).toEqual({ A: 1 });
  });
});
