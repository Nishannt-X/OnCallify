import { assign, bucketOf, BUCKETS, variantFor } from '../src/assign';
import type { Experiment } from '../src/types';

const exp = (over: Partial<Experiment> = {}): Experiment => ({
  name: 'diagnoser-v2',
  salt: 'salt-a',
  status: 'running',
  variants: [
    { name: 'control', weight: 1 },
    { name: 'treatment', weight: 1 },
  ],
  ...over,
});

describe('assignment', () => {
  test('is deterministic for the same unit', () => {
    const e = exp();
    expect(assign(e, 'inc-42')).toEqual(assign(e, 'inc-42'));
    expect(bucketOf('s', 'e', 'u')).toBe(bucketOf('s', 'e', 'u'));
  });

  test('buckets stay inside [0, BUCKETS)', () => {
    for (let i = 0; i < 2000; i += 1) {
      const b = bucketOf('s', 'e', `u${i}`);
      expect(b).toBeGreaterThanOrEqual(0);
      expect(b).toBeLessThan(BUCKETS);
    }
  });

  test('a 50/50 split is balanced within 2 percentage points over 20k units', () => {
    const e = exp();
    let treatment = 0;
    const n = 20_000;
    for (let i = 0; i < n; i += 1) if (assign(e, `unit-${i}`).variant === 'treatment') treatment += 1;
    expect(Math.abs(treatment / n - 0.5)).toBeLessThan(0.02);
  });

  test('weights are respected (90/10)', () => {
    const e = exp({ variants: [{ name: 'control', weight: 9 }, { name: 'treatment', weight: 1 }] });
    let treatment = 0;
    const n = 20_000;
    for (let i = 0; i < n; i += 1) if (assign(e, `unit-${i}`).variant === 'treatment') treatment += 1;
    expect(Math.abs(treatment / n - 0.1)).toBeLessThan(0.015);
  });

  test('different salts give independent assignments', () => {
    const a = exp({ salt: 'one' });
    const b = exp({ salt: 'two' });
    let agree = 0;
    const n = 10_000;
    for (let i = 0; i < n; i += 1) if (assign(a, `u${i}`).variant === assign(b, `u${i}`).variant) agree += 1;
    // independent 50/50 assignments agree about half the time
    expect(Math.abs(agree / n - 0.5)).toBeLessThan(0.03);
  });

  test('the last variant owns the top bucket even with float rounding', () => {
    const e = exp({ variants: [{ name: 'a', weight: 1 }, { name: 'b', weight: 1 }, { name: 'c', weight: 1 }] });
    expect(variantFor(e, BUCKETS - 1)).toBe('c');
    expect(variantFor(e, 0)).toBe('a');
  });
});
