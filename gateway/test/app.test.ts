import request from 'supertest';
import { RuleEngine, type Rule, type Transition } from '../src/alerts';
import { createApp } from '../src/app';
import { MemoryStore } from '../src/store';

const TOKEN = 'test-token-123';
const rule: Rule = {
  name: 'Errors',
  service: 'ledgerline',
  kind: 'counter_increase',
  metric: 'errors_total',
  threshold: 3,
  window_seconds: 30,
  for_seconds: 0,
  resolve_seconds: 0,
  severity: 'page',
  summary: 'errors',
};

function setup(nowIso = '2026-10-20T00:01:00.000Z') {
  const store = new MemoryStore();
  const sent: Transition[] = [];
  const app = createApp({
    store,
    engine: new RuleEngine([rule]),
    notify: async (t) => {
      sent.push(t);
    },
    ingestToken: TOKEN,
    latenessSeconds: 120,
    now: () => new Date(nowIso),
  });
  return { app, store, sent };
}

const metricsEvent = (id: string, ts: string, value: number) => ({
  event_id: id,
  ts,
  service: 'ledgerline',
  type: 'metrics',
  body: { samples: [{ name: 'errors_total', labels: { kind: 'pool_exhausted' }, value }] },
});

describe('ingest', () => {
  test('rejects missing or wrong token', async () => {
    const { app } = setup();
    await request(app).post('/v1/events').send({ events: [] }).expect(401);
    await request(app).post('/v1/events').set('x-ingest-token', 'nope').send({ events: [] }).expect(401);
  });

  test('rejects malformed events with 400', async () => {
    const { app } = setup();
    const res = await request(app)
      .post('/v1/events')
      .set('x-ingest-token', TOKEN)
      .send({ events: [{ event_id: 'x', ts: 'yesterday', service: 's', type: 'log', body: {} }] })
      .expect(400);
    expect(res.body.error).toBe('validation');
  });

  test('is idempotent: a retried batch inserts nothing and does not re-alert', async () => {
    const { app, sent } = setup();
    const batch = {
      events: [
        metricsEvent('evt-00000001', '2026-10-20T00:00:50.000Z', 0),
        metricsEvent('evt-00000002', '2026-10-20T00:00:55.000Z', 10),
      ],
    };
    const first = await request(app).post('/v1/events').set('x-ingest-token', TOKEN).send(batch).expect(202);
    expect(first.body).toMatchObject({ inserted: 2, duplicates: 0, alert_transitions: 1 });
    const retry = await request(app).post('/v1/events').set('x-ingest-token', TOKEN).send(batch).expect(202);
    expect(retry.body).toMatchObject({ inserted: 0, duplicates: 2, alert_transitions: 0 });
    expect(sent).toHaveLength(1);
    expect(sent[0]!.change).toBe('firing');
  });

  test('late events are stored but never alerted on', async () => {
    const { app, sent } = setup('2026-10-20T01:00:00.000Z');
    const res = await request(app)
      .post('/v1/events')
      .set('x-ingest-token', TOKEN)
      .send({
        events: [
          metricsEvent('late-0000001', '2026-10-20T00:00:00.000Z', 0),
          metricsEvent('late-0000002', '2026-10-20T00:00:05.000Z', 50),
        ],
      })
      .expect(202);
    expect(res.body).toMatchObject({ inserted: 2, late: 2, alert_transitions: 0 });
    expect(sent).toHaveLength(0);
    const q = await request(app).get('/v1/events?service=ledgerline').expect(200);
    expect(q.body.events).toHaveLength(2);
    expect(q.body.events[0].late).toBe(true);
  });

  test('alert state is queryable by fingerprint', async () => {
    const { app, sent } = setup();
    await request(app)
      .post('/v1/events')
      .set('x-ingest-token', TOKEN)
      .send({
        events: [
          metricsEvent('evt-a0000001', '2026-10-20T00:00:50.000Z', 0),
          metricsEvent('evt-a0000002', '2026-10-20T00:00:55.000Z', 9),
        ],
      })
      .expect(202);
    const fp = sent[0]!.alert.fingerprint;
    const got = await request(app).get(`/v1/alerts/${fp}`).expect(200);
    expect(got.body).toMatchObject({ rule: 'Errors', status: 'firing' });
    await request(app).get('/v1/alerts/doesnotexist').expect(404);
  });
});

describe('experiments', () => {
  const body = {
    name: 'diagnoser-v2',
    salt: 'salt-2026',
    variants: [
      { name: 'rules-v1', weight: 1 },
      { name: 'rules-v2', weight: 1 },
    ],
  };

  test('assignment is sticky and recorded once per unit', async () => {
    const { app } = setup();
    await request(app).post('/v1/experiments').set('x-ingest-token', TOKEN).send(body).expect(201);
    const a = await request(app).get('/v1/assign?experiment=diagnoser-v2&unit=inc-1').expect(200);
    const b = await request(app).get('/v1/assign?experiment=diagnoser-v2&unit=inc-1').expect(200);
    expect(a.body.variant).toBe(b.body.variant);
    const counts = await request(app).get('/v1/experiments/diagnoser-v2/counts').expect(200);
    const total = Object.values(counts.body.assignments as Record<string, number>).reduce((x, y) => x + y, 0);
    expect(total).toBe(1);
  });

  test('changing the salt of a running experiment is refused', async () => {
    const { app } = setup();
    await request(app).post('/v1/experiments').set('x-ingest-token', TOKEN).send(body).expect(201);
    const res = await request(app)
      .post('/v1/experiments')
      .set('x-ingest-token', TOKEN)
      .send({ ...body, salt: 'other-salt' })
      .expect(409);
    expect(res.body.message).toMatch(/running experiment/);
    // stopping it first is allowed
    await request(app)
      .post('/v1/experiments')
      .set('x-ingest-token', TOKEN)
      .send({ ...body, salt: 'other-salt', status: 'stopped' })
      .expect(200);
  });

  test('exposure must match the assigned arm and is recorded once', async () => {
    const { app } = setup();
    await request(app).post('/v1/experiments').set('x-ingest-token', TOKEN).send(body).expect(201);
    const a = await request(app).get('/v1/assign?experiment=diagnoser-v2&unit=inc-7').expect(200);
    const other = a.body.variant === 'rules-v1' ? 'rules-v2' : 'rules-v1';
    await request(app)
      .post('/v1/exposures')
      .set('x-ingest-token', TOKEN)
      .send({ experiment: 'diagnoser-v2', unit: 'inc-7', variant: other })
      .expect(409);
    await request(app)
      .post('/v1/exposures')
      .set('x-ingest-token', TOKEN)
      .send({ experiment: 'diagnoser-v2', unit: 'inc-7', variant: a.body.variant })
      .expect(201);
    const again = await request(app)
      .post('/v1/exposures')
      .set('x-ingest-token', TOKEN)
      .send({ experiment: 'diagnoser-v2', unit: 'inc-7', variant: a.body.variant })
      .expect(200);
    expect(again.body.recorded).toBe(false);
  });

  test('stopped experiments serve control and record nothing', async () => {
    const { app } = setup();
    await request(app)
      .post('/v1/experiments')
      .set('x-ingest-token', TOKEN)
      .send({ ...body, status: 'stopped' })
      .expect(201);
    const a = await request(app).get('/v1/assign?experiment=diagnoser-v2&unit=x').expect(200);
    expect(a.body).toMatchObject({ variant: 'rules-v1', recorded: false });
  });

  test('unknown experiment is 404', async () => {
    const { app } = setup();
    await request(app).get('/v1/assign?experiment=nope&unit=x').expect(404);
  });
});
