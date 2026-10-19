import type { Collection, Db } from 'mongodb';
import { MongoClient, MongoServerError } from 'mongodb';
import type {
  AlertRecord,
  AssignmentRecord,
  Experiment,
  ExposureRecord,
  StoredEvent,
} from './types';

export interface EventQuery {
  service?: string;
  type?: string;
  since?: string;
  until?: string;
  limit: number;
}

export interface InsertResult {
  inserted: number;
  duplicates: number;
  /** ids that were new; only these may feed the alert engine */
  insertedIds: string[];
}

/**
 * Storage the gateway needs. Two implementations: MongoStore for real use and
 * MemoryStore for tests and the zero-dependency local mode. Both must behave the same
 * on the two properties that matter: event ids are unique, and an assignment or an
 * exposure for (experiment, unit) is recorded at most once.
 */
export interface Store {
  insertEvents(events: StoredEvent[]): Promise<InsertResult>;
  queryEvents(q: EventQuery): Promise<StoredEvent[]>;

  upsertAlert(alert: AlertRecord): Promise<void>;
  getAlert(fingerprint: string): Promise<AlertRecord | null>;
  listAlerts(status?: string): Promise<AlertRecord[]>;

  putExperiment(e: Experiment): Promise<void>;
  getExperiment(name: string): Promise<Experiment | null>;
  listExperiments(): Promise<Experiment[]>;

  /** Returns the stored record: the new one, or the one that already existed. */
  recordAssignment(a: AssignmentRecord): Promise<AssignmentRecord>;
  recordExposure(e: ExposureRecord): Promise<boolean>;
  countByVariant(experiment: string, kind: 'assignments' | 'exposures'): Promise<Record<string, number>>;

  close(): Promise<void>;
}

export class MemoryStore implements Store {
  private events = new Map<string, StoredEvent>();
  private alerts = new Map<string, AlertRecord>();
  private experiments = new Map<string, Experiment>();
  private assignments = new Map<string, AssignmentRecord>();
  private exposures = new Map<string, ExposureRecord>();

  async insertEvents(events: StoredEvent[]): Promise<InsertResult> {
    const insertedIds: string[] = [];
    for (const e of events) {
      if (this.events.has(e.event_id)) continue;
      this.events.set(e.event_id, e);
      insertedIds.push(e.event_id);
    }
    return { inserted: insertedIds.length, duplicates: events.length - insertedIds.length, insertedIds };
  }

  async queryEvents(q: EventQuery): Promise<StoredEvent[]> {
    const out = [...this.events.values()].filter(
      (e) =>
        (!q.service || e.service === q.service) &&
        (!q.type || e.type === q.type) &&
        (!q.since || e.ts >= q.since) &&
        (!q.until || e.ts <= q.until),
    );
    out.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
    return out.slice(-q.limit);
  }

  async upsertAlert(alert: AlertRecord): Promise<void> {
    this.alerts.set(alert.fingerprint, { ...alert });
  }

  async getAlert(fingerprint: string): Promise<AlertRecord | null> {
    return this.alerts.get(fingerprint) ?? null;
  }

  async listAlerts(status?: string): Promise<AlertRecord[]> {
    return [...this.alerts.values()]
      .filter((a) => !status || a.status === status)
      .sort((a, b) => (a.starts_at < b.starts_at ? 1 : -1));
  }

  async putExperiment(e: Experiment): Promise<void> {
    this.experiments.set(e.name, e);
  }

  async getExperiment(name: string): Promise<Experiment | null> {
    return this.experiments.get(name) ?? null;
  }

  async listExperiments(): Promise<Experiment[]> {
    return [...this.experiments.values()];
  }

  async recordAssignment(a: AssignmentRecord): Promise<AssignmentRecord> {
    const key = `${a.experiment}\u0000${a.unit}`;
    const existing = this.assignments.get(key);
    if (existing) return existing;
    this.assignments.set(key, a);
    return a;
  }

  async recordExposure(e: ExposureRecord): Promise<boolean> {
    const key = `${e.experiment}\u0000${e.unit}`;
    if (this.exposures.has(key)) return false;
    this.exposures.set(key, e);
    return true;
  }

  async countByVariant(experiment: string, kind: 'assignments' | 'exposures'): Promise<Record<string, number>> {
    const source = kind === 'assignments' ? this.assignments : this.exposures;
    const counts: Record<string, number> = {};
    for (const r of source.values()) {
      if (r.experiment !== experiment) continue;
      counts[r.variant] = (counts[r.variant] ?? 0) + 1;
    }
    return counts;
  }

  async close(): Promise<void> {
    /* nothing to release */
  }
}

const DUPLICATE_KEY = 11000;

/**
 * MongoDB implementation. Uniqueness is enforced by indexes, not by read-then-write,
 * so two gateway replicas racing on the same event id still insert it once.
 */
export class MongoStore implements Store {
  private constructor(
    private readonly client: MongoClient,
    private readonly db: Db,
  ) {}

  static async connect(url: string, dbName = 'oncallify'): Promise<MongoStore> {
    const client = new MongoClient(url, { serverSelectionTimeoutMS: 5000 });
    await client.connect();
    const store = new MongoStore(client, client.db(dbName));
    await store.ensureIndexes();
    return store;
  }

  private get eventsCol(): Collection<StoredEvent> {
    return this.db.collection<StoredEvent>('events');
  }
  private get alertsCol(): Collection<AlertRecord> {
    return this.db.collection<AlertRecord>('alerts');
  }
  private get experimentsCol(): Collection<Experiment> {
    return this.db.collection<Experiment>('experiments');
  }
  private get assignmentsCol(): Collection<AssignmentRecord> {
    return this.db.collection<AssignmentRecord>('assignments');
  }
  private get exposuresCol(): Collection<ExposureRecord> {
    return this.db.collection<ExposureRecord>('exposures');
  }

  async ensureIndexes(): Promise<void> {
    await this.eventsCol.createIndex({ event_id: 1 }, { unique: true });
    await this.eventsCol.createIndex({ service: 1, type: 1, ts: 1 });
    await this.alertsCol.createIndex({ fingerprint: 1 }, { unique: true });
    await this.experimentsCol.createIndex({ name: 1 }, { unique: true });
    await this.assignmentsCol.createIndex({ experiment: 1, unit: 1 }, { unique: true });
    await this.exposuresCol.createIndex({ experiment: 1, unit: 1 }, { unique: true });
  }

  async insertEvents(events: StoredEvent[]): Promise<InsertResult> {
    const all = events.map((e) => e.event_id);
    try {
      await this.eventsCol.insertMany(events.map((e) => ({ ...e })), { ordered: false });
      return { inserted: all.length, duplicates: 0, insertedIds: all };
    } catch (err: unknown) {
      // unordered insert: everything except the failed indexes was written
      const raw = (err as { writeErrors?: unknown }).writeErrors;
      const writeErrors = (Array.isArray(raw) ? raw : raw ? [raw] : []) as { code: number; index: number }[];
      if (writeErrors.length > 0 && writeErrors.every((w) => w.code === DUPLICATE_KEY)) {
        const failed = new Set(writeErrors.map((w) => w.index));
        const insertedIds = all.filter((_, i) => !failed.has(i));
        return { inserted: insertedIds.length, duplicates: failed.size, insertedIds };
      }
      throw err;
    }
  }

  async queryEvents(q: EventQuery): Promise<StoredEvent[]> {
    const filter: Record<string, unknown> = {};
    if (q.service) filter.service = q.service;
    if (q.type) filter.type = q.type;
    if (q.since || q.until) {
      const range: Record<string, string> = {};
      if (q.since) range.$gte = q.since;
      if (q.until) range.$lte = q.until;
      filter.ts = range;
    }
    const rows = await this.eventsCol
      .find(filter, { projection: { _id: 0 } })
      .sort({ ts: -1 })
      .limit(q.limit)
      .toArray();
    return rows.reverse();
  }

  async upsertAlert(alert: AlertRecord): Promise<void> {
    await this.alertsCol.replaceOne({ fingerprint: alert.fingerprint }, alert, { upsert: true });
  }

  async getAlert(fingerprint: string): Promise<AlertRecord | null> {
    return this.alertsCol.findOne({ fingerprint }, { projection: { _id: 0 } });
  }

  async listAlerts(status?: string): Promise<AlertRecord[]> {
    return this.alertsCol
      .find(status ? { status: status as AlertRecord['status'] } : {}, { projection: { _id: 0 } })
      .sort({ starts_at: -1 })
      .limit(500)
      .toArray();
  }

  async putExperiment(e: Experiment): Promise<void> {
    await this.experimentsCol.replaceOne({ name: e.name }, e, { upsert: true });
  }

  async getExperiment(name: string): Promise<Experiment | null> {
    return this.experimentsCol.findOne({ name }, { projection: { _id: 0 } });
  }

  async listExperiments(): Promise<Experiment[]> {
    return this.experimentsCol.find({}, { projection: { _id: 0 } }).toArray();
  }

  async recordAssignment(a: AssignmentRecord): Promise<AssignmentRecord> {
    try {
      await this.assignmentsCol.insertOne({ ...a });
      return a;
    } catch (err) {
      if (err instanceof MongoServerError && err.code === DUPLICATE_KEY) {
        const existing = await this.assignmentsCol.findOne(
          { experiment: a.experiment, unit: a.unit },
          { projection: { _id: 0 } },
        );
        if (existing) return existing;
      }
      throw err;
    }
  }

  async recordExposure(e: ExposureRecord): Promise<boolean> {
    try {
      await this.exposuresCol.insertOne({ ...e });
      return true;
    } catch (err) {
      if (err instanceof MongoServerError && err.code === DUPLICATE_KEY) return false;
      throw err;
    }
  }

  async countByVariant(experiment: string, kind: 'assignments' | 'exposures'): Promise<Record<string, number>> {
    const col = kind === 'assignments' ? this.assignmentsCol : this.exposuresCol;
    const rows = await col
      .aggregate<{ _id: string; n: number }>([
        { $match: { experiment } },
        { $group: { _id: '$variant', n: { $sum: 1 } } },
      ])
      .toArray();
    return Object.fromEntries(rows.map((r) => [r._id, r.n]));
  }

  async close(): Promise<void> {
    await this.client.close();
  }
}
