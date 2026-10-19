import express, { type NextFunction, type Request, type Response } from 'express';
import { timingSafeEqual } from 'node:crypto';
import { z } from 'zod';
import { RuleEngine, type Transition } from './alerts';
import { assign } from './assign';
import type { Store } from './store';
import { BatchSchema, ExperimentSchema, SampleSchema, type StoredEvent } from './types';

export interface AppDeps {
  store: Store;
  engine: RuleEngine;
  /** called for every alert state change; failures are logged, never thrown at the client */
  notify: (t: Transition) => Promise<void>;
  ingestToken: string;
  /** events older than this (relative to receive time) are stored but not alerted on */
  latenessSeconds?: number;
  now?: () => Date;
  log?: (msg: string, fields?: Record<string, unknown>) => void;
}

function tokenOk(expected: string, given: string | undefined): boolean {
  if (!expected || !given) return false;
  const a = Buffer.from(expected);
  const b = Buffer.from(given);
  return a.length === b.length && timingSafeEqual(a, b);
}

const asyncRoute =
  (fn: (req: Request, res: Response) => Promise<unknown>) =>
  (req: Request, res: Response, next: NextFunction): void => {
    fn(req, res).catch(next);
  };

export function createApp(deps: AppDeps): express.Express {
  const now = deps.now ?? (() => new Date());
  const log = deps.log ?? (() => undefined);
  const lateness = (deps.latenessSeconds ?? 120) * 1000;
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '2mb' }));

  const requireIngest = (req: Request, res: Response, next: NextFunction): void => {
    if (!tokenOk(deps.ingestToken, req.header('x-ingest-token'))) {
      res.status(401).json({ error: 'unauthorized' });
      return;
    }
    next();
  };

  app.get('/health', (_req, res) => {
    res.json({ status: 'ok', rules: deps.engine.listRules().length });
  });

  // ---- ingest ------------------------------------------------------------------------
  app.post(
    '/v1/events',
    requireIngest,
    asyncRoute(async (req, res) => {
      const parsed = BatchSchema.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: 'validation', issues: parsed.error.issues.slice(0, 5) });
        return;
      }
      const receivedAt = now();
      const stored: StoredEvent[] = parsed.data.events.map((e) => ({
        ...e,
        received_at: receivedAt.toISOString(),
        late: receivedAt.getTime() - Date.parse(e.ts) > lateness,
      }));
      const result = await deps.store.insertEvents(stored);

      // Alert only on fresh metrics. Duplicates are skipped by id at the store, so a
      // retried batch can not double-count; we evaluate only events that were new.
      const newIds = new Set(result.insertedIds);
      const fresh = stored
        .filter((e) => e.type === 'metrics' && !e.late && newIds.has(e.event_id))
        .sort((a, b) => (a.ts < b.ts ? -1 : 1));
      let transitions = 0;
      for (const e of fresh) {
        const samples = z.array(SampleSchema).safeParse((e.body as { samples?: unknown }).samples);
        if (!samples.success) continue;
        for (const t of deps.engine.observe(e.service, e.ts, samples.data)) {
          transitions += 1;
          await deps.store.upsertAlert(t.alert);
          deps.notify(t).catch((err: unknown) =>
            log('notify failed', { fingerprint: t.alert.fingerprint, error: String(err) }),
          );
        }
      }
      res.status(202).json({ inserted: result.inserted, duplicates: result.duplicates, late: stored.filter((e) => e.late).length, alert_transitions: transitions });
    }),
  );

  app.get(
    '/v1/events',
    asyncRoute(async (req, res) => {
      const q = z
        .object({
          service: z.string().optional(),
          type: z.enum(['log', 'metrics', 'change']).optional(),
          since: z.string().datetime({ offset: true }).optional(),
          until: z.string().datetime({ offset: true }).optional(),
          limit: z.coerce.number().int().min(1).max(5000).default(500),
        })
        .safeParse(req.query);
      if (!q.success) {
        res.status(400).json({ error: 'validation', issues: q.error.issues.slice(0, 5) });
        return;
      }
      res.json({ events: await deps.store.queryEvents(q.data) });
    }),
  );

  // ---- alerts ------------------------------------------------------------------------
  app.get('/v1/rules', (_req, res) => {
    res.json({ rules: deps.engine.listRules(), phases: deps.engine.phases() });
  });

  app.get(
    '/v1/alerts',
    asyncRoute(async (req, res) => {
      const status = typeof req.query.status === 'string' ? req.query.status : undefined;
      res.json({ alerts: await deps.store.listAlerts(status) });
    }),
  );

  app.get(
    '/v1/alerts/:fingerprint',
    asyncRoute(async (req, res) => {
      const a = await deps.store.getAlert(String(req.params.fingerprint));
      if (!a) {
        res.status(404).json({ error: 'not_found' });
        return;
      }
      res.json(a);
    }),
  );

  // ---- experiments -------------------------------------------------------------------
  app.post(
    '/v1/experiments',
    requireIngest,
    asyncRoute(async (req, res) => {
      const parsed = ExperimentSchema.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: 'validation', issues: parsed.error.issues.slice(0, 5) });
        return;
      }
      const existing = await deps.store.getExperiment(parsed.data.name);
      if (existing && existing.status === 'running') {
        const sameShape =
          existing.salt === parsed.data.salt &&
          JSON.stringify(existing.variants) === JSON.stringify(parsed.data.variants);
        if (!sameShape && parsed.data.status === 'running') {
          // reshuffling units mid-flight would mix pre- and post-change populations
          res.status(409).json({ error: 'conflict', message: 'cannot change salt or variants of a running experiment' });
          return;
        }
      }
      await deps.store.putExperiment(parsed.data);
      res.status(existing ? 200 : 201).json(parsed.data);
    }),
  );

  app.get(
    '/v1/experiments',
    asyncRoute(async (_req, res) => {
      res.json({ experiments: await deps.store.listExperiments() });
    }),
  );

  app.get(
    '/v1/assign',
    asyncRoute(async (req, res) => {
      const q = z
        .object({ experiment: z.string().min(1), unit: z.string().min(1).max(200) })
        .safeParse(req.query);
      if (!q.success) {
        res.status(400).json({ error: 'validation', message: 'experiment and unit are required' });
        return;
      }
      const exp = await deps.store.getExperiment(q.data.experiment);
      if (!exp) {
        res.status(404).json({ error: 'not_found', message: 'unknown experiment' });
        return;
      }
      if (exp.status !== 'running') {
        // stopped experiments serve the first (control) variant and record nothing
        res.json({ experiment: exp.name, unit: q.data.unit, variant: exp.variants[0]!.name, recorded: false });
        return;
      }
      const { variant, bucket } = assign(exp, q.data.unit);
      const rec = await deps.store.recordAssignment({
        experiment: exp.name,
        unit: q.data.unit,
        variant,
        bucket,
        assigned_at: now().toISOString(),
      });
      res.json({ experiment: exp.name, unit: q.data.unit, variant: rec.variant, bucket: rec.bucket, recorded: true });
    }),
  );

  app.post(
    '/v1/exposures',
    requireIngest,
    asyncRoute(async (req, res) => {
      const parsed = z
        .object({ experiment: z.string().min(1), unit: z.string().min(1), variant: z.string().min(1) })
        .safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: 'validation' });
        return;
      }
      const exp = await deps.store.getExperiment(parsed.data.experiment);
      if (!exp) {
        res.status(404).json({ error: 'not_found' });
        return;
      }
      const expected = assign(exp, parsed.data.unit).variant;
      if (expected !== parsed.data.variant) {
        // an exposure for an arm the unit was never assigned to is a logging bug
        res.status(409).json({ error: 'conflict', message: `unit is assigned to ${expected}` });
        return;
      }
      const created = await deps.store.recordExposure({ ...parsed.data, exposed_at: now().toISOString() });
      res.status(created ? 201 : 200).json({ recorded: created });
    }),
  );

  app.get(
    '/v1/experiments/:name/counts',
    asyncRoute(async (req, res) => {
      const name = String(req.params.name);
      const exp = await deps.store.getExperiment(name);
      if (!exp) {
        res.status(404).json({ error: 'not_found' });
        return;
      }
      res.json({
        experiment: name,
        expected_weights: Object.fromEntries(exp.variants.map((v) => [v.name, v.weight])),
        assignments: await deps.store.countByVariant(name, 'assignments'),
        exposures: await deps.store.countByVariant(name, 'exposures'),
      });
    }),
  );

  app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
    log('unhandled error', { error: String(err) });
    res.status(500).json({ error: 'internal' });
  });

  return app;
}
