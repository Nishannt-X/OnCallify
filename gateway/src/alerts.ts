import { createHash } from 'node:crypto';
import type { AlertRecord, Sample } from './types';

/**
 * Rule kinds, kept deliberately few:
 *  - counter_increase: sum of increases of a counter (optionally label-filtered) over
 *    `window_seconds` is above `threshold`. Handles counter resets (app restart).
 *  - gauge_ratio: numerator gauge / denominator gauge is at or above `threshold`.
 *  - gauge_threshold: gauge is at or above `threshold`.
 * A condition must hold for `for_seconds` before the alert fires (pending state), and
 * must be false for `resolve_seconds` before it resolves, so one noisy scrape neither
 * pages nor un-pages anyone.
 */
export interface Rule {
  name: string;
  service: string;
  kind: 'counter_increase' | 'gauge_ratio' | 'gauge_threshold';
  metric: string;
  match?: Record<string, string>;
  /** label value prefixes to exclude, e.g. kind: ["business"] */
  exclude?: Record<string, string[]>;
  denominator?: string;
  threshold: number;
  window_seconds?: number;
  for_seconds: number;
  resolve_seconds?: number;
  severity: 'page' | 'ticket';
  summary: string;
}

type Phase = 'inactive' | 'pending' | 'firing';

interface RuleState {
  phase: Phase;
  conditionSince: number | null;
  clearSince: number | null;
  fingerprint: string | null;
  startsAt: string | null;
  lastValue: number;
}

interface Point {
  t: number;
  increase: number;
}

export interface Transition {
  alert: AlertRecord;
  change: 'firing' | 'resolved';
}

const seriesKey = (s: Sample): string =>
  s.name +
  '{' +
  Object.keys(s.labels)
    .sort()
    .map((k) => `${k}=${s.labels[k]}`)
    .join(',') +
  '}';

function matches(s: Sample, rule: Rule, metric: string): boolean {
  if (s.name !== metric) return false;
  for (const [k, v] of Object.entries(rule.match ?? {})) {
    if (s.labels[k] !== v) return false;
  }
  for (const [k, prefixes] of Object.entries(rule.exclude ?? {})) {
    const v = s.labels[k] ?? '';
    if (prefixes.some((p) => v.startsWith(p))) return false;
  }
  return true;
}

export function fingerprintOf(rule: string, service: string, startsAt: string): string {
  return createHash('sha256').update(`${rule}|${service}|${startsAt}`).digest('hex').slice(0, 16);
}

export class RuleEngine {
  private readonly state = new Map<string, RuleState>();
  /** last seen value of each counter series, per service */
  private readonly lastCounter = new Map<string, number>();
  /** recent counter increases per rule, for windowed sums */
  private readonly increases = new Map<string, Point[]>();

  constructor(private readonly rules: Rule[]) {
    for (const r of rules) {
      this.state.set(r.name, {
        phase: 'inactive',
        conditionSince: null,
        clearSince: null,
        fingerprint: null,
        startsAt: null,
        lastValue: 0,
      });
    }
  }

  listRules(): Rule[] {
    return this.rules.map((r) => ({ ...r }));
  }

  /** Feed one scrape. Returns the alerts that changed state because of it. */
  observe(service: string, tsIso: string, samples: Sample[]): Transition[] {
    const t = Date.parse(tsIso);
    const counterDeltas = this.counterDeltas(service, samples);
    const out: Transition[] = [];
    for (const rule of this.rules) {
      if (rule.service !== service) continue;
      const value = this.evaluate(rule, t, samples, counterDeltas);
      const transition = this.step(rule, t, tsIso, value);
      if (transition) out.push(transition);
    }
    return out;
  }

  private counterDeltas(service: string, samples: Sample[]): Map<string, number> {
    const deltas = new Map<string, number>();
    for (const s of samples) {
      const key = `${service}|${seriesKey(s)}`;
      const prev = this.lastCounter.get(key);
      this.lastCounter.set(key, s.value);
      if (prev === undefined) continue; // first sight of a series: no rate yet
      // a counter that went down was reset (process restart): count from zero
      deltas.set(seriesKey(s), s.value >= prev ? s.value - prev : s.value);
    }
    return deltas;
  }

  private evaluate(rule: Rule, t: number, samples: Sample[], deltas: Map<string, number>): number {
    if (rule.kind === 'counter_increase') {
      let inc = 0;
      for (const s of samples) {
        if (matches(s, rule, rule.metric)) inc += deltas.get(seriesKey(s)) ?? 0;
      }
      const window = (rule.window_seconds ?? 30) * 1000;
      const points = (this.increases.get(rule.name) ?? []).filter((p) => p.t > t - window);
      points.push({ t, increase: inc });
      this.increases.set(rule.name, points);
      return points.reduce((a, p) => a + p.increase, 0);
    }
    const num = samples.filter((s) => matches(s, rule, rule.metric)).reduce((a, s) => a + s.value, 0);
    if (rule.kind === 'gauge_threshold') return num;
    const den = samples
      .filter((s) => s.name === rule.denominator)
      .reduce((a, s) => a + s.value, 0);
    return den > 0 ? num / den : 0;
  }

  private step(rule: Rule, t: number, tsIso: string, value: number): Transition | null {
    const st = this.state.get(rule.name)!;
    st.lastValue = value;
    const condition = Number.isFinite(value) && value >= rule.threshold && (rule.kind !== 'counter_increase' || value > 0);

    if (condition) {
      st.clearSince = null;
      if (st.phase === 'inactive') {
        st.phase = 'pending';
        st.conditionSince = t;
      }
      if (st.phase === 'pending' && t - (st.conditionSince ?? t) >= rule.for_seconds * 1000) {
        st.phase = 'firing';
        st.startsAt = tsIso;
        st.fingerprint = fingerprintOf(rule.name, rule.service, tsIso);
        return { change: 'firing', alert: this.record(rule, st, 'firing', null) };
      }
      return null;
    }

    if (st.phase === 'pending') {
      st.phase = 'inactive';
      st.conditionSince = null;
      return null;
    }
    if (st.phase === 'firing') {
      if (st.clearSince === null) st.clearSince = t;
      if (t - st.clearSince >= (rule.resolve_seconds ?? 10) * 1000) {
        const rec = this.record(rule, st, 'resolved', tsIso);
        st.phase = 'inactive';
        st.conditionSince = null;
        st.clearSince = null;
        st.fingerprint = null;
        st.startsAt = null;
        return { change: 'resolved', alert: rec };
      }
    }
    return null;
  }

  private record(rule: Rule, st: RuleState, status: 'firing' | 'resolved', endsAt: string | null): AlertRecord {
    return {
      fingerprint: st.fingerprint!,
      rule: rule.name,
      service: rule.service,
      severity: rule.severity,
      summary: rule.summary,
      status,
      starts_at: st.startsAt!,
      ends_at: endsAt,
      value: st.lastValue,
      labels: { ...(rule.match ?? {}) },
    };
  }

  /** Current phase per rule, for /v1/rules. */
  phases(): Record<string, { phase: Phase; value: number }> {
    const out: Record<string, { phase: Phase; value: number }> = {};
    for (const [name, st] of this.state) out[name] = { phase: st.phase, value: st.lastValue };
    return out;
  }
}
