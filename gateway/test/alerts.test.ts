import { fingerprintOf, RuleEngine, type Rule } from '../src/alerts';
import type { Sample } from '../src/types';

const errorsRule: Rule = {
  name: 'Errors',
  service: 'svc',
  kind: 'counter_increase',
  metric: 'errors_total',
  exclude: { kind: ['business'] },
  threshold: 5,
  window_seconds: 20,
  for_seconds: 6,
  resolve_seconds: 10,
  severity: 'page',
  summary: 'errors',
};

const heapRule: Rule = {
  name: 'Heap',
  service: 'svc',
  kind: 'gauge_ratio',
  metric: 'heap_used',
  denominator: 'heap_max',
  threshold: 0.85,
  for_seconds: 4,
  resolve_seconds: 4,
  severity: 'page',
  summary: 'heap',
};

const at = (s: number): string => new Date(Date.UTC(2026, 9, 20, 0, 0, s)).toISOString();
const errs = (infra: number, business = 0): Sample[] => [
  { name: 'errors_total', labels: { kind: 'pool_exhausted' }, value: infra },
  { name: 'errors_total', labels: { kind: 'business' }, value: business },
];

describe('RuleEngine', () => {
  test('counter rule goes pending, then fires only after for_seconds', () => {
    const e = new RuleEngine([errorsRule]);
    expect(e.observe('svc', at(0), errs(0))).toEqual([]); // first sight: no rate
    expect(e.observe('svc', at(2), errs(10))).toEqual([]); // condition true -> pending
    expect(e.phases().Errors!.phase).toBe('pending');
    expect(e.observe('svc', at(4), errs(20))).toEqual([]);
    const t = e.observe('svc', at(8), errs(30));
    expect(t).toHaveLength(1);
    expect(t[0]!.change).toBe('firing');
    expect(t[0]!.alert.fingerprint).toBe(fingerprintOf('Errors', 'svc', at(8)));
  });

  test('business errors do not count', () => {
    const e = new RuleEngine([errorsRule]);
    e.observe('svc', at(0), errs(0, 0));
    for (let s = 2; s <= 20; s += 2) expect(e.observe('svc', at(s), errs(0, s * 100))).toEqual([]);
    expect(e.phases().Errors!.phase).toBe('inactive');
  });

  test('a short blip that clears before for_seconds never fires', () => {
    const e = new RuleEngine([{ ...errorsRule, window_seconds: 2 }]);
    e.observe('svc', at(0), errs(0));
    e.observe('svc', at(2), errs(10)); // pending
    expect(e.observe('svc', at(5), errs(10))).toEqual([]); // window empty -> back to inactive
    expect(e.phases().Errors!.phase).toBe('inactive');
  });

  test('counter reset after a restart is treated as an increase from zero', () => {
    const e = new RuleEngine([{ ...errorsRule, threshold: 3, for_seconds: 0 }]);
    e.observe('svc', at(0), errs(100));
    const t = e.observe('svc', at(2), errs(4)); // restarted process, counter 100 -> 4
    expect(t[0]?.change).toBe('firing');
    expect(t[0]?.alert.value).toBe(4);
  });

  test('firing alert resolves only after resolve_seconds of clear', () => {
    const e = new RuleEngine([heapRule]);
    const heap = (used: number): Sample[] => [
      { name: 'heap_used', labels: {}, value: used },
      { name: 'heap_max', labels: {}, value: 100 },
    ];
    e.observe('svc', at(0), heap(90));
    const fired = e.observe('svc', at(5), heap(95));
    expect(fired[0]?.change).toBe('firing');
    expect(e.observe('svc', at(6), heap(40))).toEqual([]); // clear starts
    expect(e.observe('svc', at(8), heap(95))).toEqual([]); // flaps back: still the same alert
    expect(e.observe('svc', at(9), heap(40))).toEqual([]);
    const resolved = e.observe('svc', at(14), heap(40));
    expect(resolved[0]?.change).toBe('resolved');
    expect(resolved[0]?.alert.fingerprint).toBe(fired[0]?.alert.fingerprint);
    expect(resolved[0]?.alert.ends_at).toBe(at(14));
  });

  test('rules for other services are ignored', () => {
    const e = new RuleEngine([{ ...heapRule, for_seconds: 0 }]);
    expect(
      e.observe('other', at(0), [
        { name: 'heap_used', labels: {}, value: 99 },
        { name: 'heap_max', labels: {}, value: 100 },
      ]),
    ).toEqual([]);
  });
});
