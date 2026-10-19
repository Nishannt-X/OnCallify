import { z } from 'zod';

/** One sample from a Prometheus scrape. */
export const SampleSchema = z.object({
  name: z.string().min(1).max(200),
  labels: z.record(z.string()).default({}),
  value: z.number(),
});
export type Sample = z.infer<typeof SampleSchema>;

/**
 * Everything the collector sends is an event with a client-generated id. The id is
 * what makes ingest idempotent: a retried batch inserts nothing new.
 */
export const EventSchema = z.object({
  event_id: z.string().min(8).max(128),
  ts: z.string().datetime({ offset: true }),
  service: z.string().min(1).max(100),
  type: z.enum(['log', 'metrics', 'change']),
  body: z.record(z.unknown()),
});
export type EventIn = z.infer<typeof EventSchema>;

export interface StoredEvent extends EventIn {
  received_at: string;
  /** Arrived later than the lateness window: kept for evidence, never used for alerting. */
  late: boolean;
}

export const BatchSchema = z.object({ events: z.array(EventSchema).min(1).max(1000) });

export type AlertStatus = 'firing' | 'resolved';

export interface AlertRecord {
  fingerprint: string;
  rule: string;
  service: string;
  severity: string;
  summary: string;
  status: AlertStatus;
  starts_at: string;
  ends_at: string | null;
  value: number;
  labels: Record<string, string>;
}

export const ExperimentSchema = z.object({
  name: z.string().regex(/^[a-z0-9][a-z0-9-]{1,62}$/),
  salt: z.string().min(4).max(64),
  variants: z
    .array(z.object({ name: z.string().min(1).max(40), weight: z.number().positive() }))
    .min(2)
    .max(8),
  status: z.enum(['running', 'stopped']).default('running'),
});
export type Experiment = z.infer<typeof ExperimentSchema>;

export interface AssignmentRecord {
  experiment: string;
  unit: string;
  variant: string;
  bucket: number;
  assigned_at: string;
}

export interface ExposureRecord {
  experiment: string;
  unit: string;
  variant: string;
  exposed_at: string;
}
