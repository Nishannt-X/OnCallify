# OnCallify experiment report

Corpus: 56 incidents captured from the live stack (7 fault families x 8 variations). Baseline: `rules-v1`. Repeats for nondeterministic variants: 3.

## Variants

| variant | scenarios | runs | cause correct | action correct | unsafe | missed | p50 latency ms |
|---|---|---|---|---|---|---|---|
| rules-v1 | 56 | 56 | 67.9% | 69.6% | 16.1% | 14.3% | 0.0 |
| rules-v2 | 56 | 56 | 100.0% | 73.2% | 0.0% | 26.8% | 0.01 |
| ml-logreg | 56 | 56 | 100.0% | 73.2% | 0.0% | 26.8% | 0.04 |

## rules-v2 vs rules-v1 (paired, 56 scenarios)

| metric | baseline | candidate | diff | 95% CI | p | p (Holm) | test |
|---|---|---|---|---|---|---|---|
| cause_correct | 67.9% | 100.0% | +32.1% | [+19.6%, +44.6%] | 0.0000 | 0.0000 | exact McNemar |
| action_correct | 69.6% | 73.2% | +3.6% | [+0.0%, +8.9%] | 0.5000 | 0.5000 | exact McNemar |
| unsafe | 16.1% | 0.0% | -16.1% | [-26.8%, -7.1%] | 0.0039 | 0.0117 | exact McNemar |
| missed | 14.3% | 26.8% | +12.5% | [+3.6%, +23.2%] | 0.0391 | 0.0782 | exact McNemar |

_With 56 paired scenarios and 32% discordant pairs, a difference in cause accuracy smaller than about 21% would usually go undetected (80% power, alpha 0.05)._

## ml-logreg vs rules-v1 (paired, 56 scenarios)

| metric | baseline | candidate | diff | 95% CI | p | p (Holm) | test |
|---|---|---|---|---|---|---|---|
| cause_correct | 67.9% | 100.0% | +32.1% | [+19.6%, +44.6%] | 0.0000 | 0.0000 | exact McNemar |
| action_correct | 69.6% | 73.2% | +3.6% | [+0.0%, +8.9%] | 0.5000 | 0.5000 | exact McNemar |
| unsafe | 16.1% | 0.0% | -16.1% | [-26.8%, -7.1%] | 0.0039 | 0.0117 | exact McNemar |
| missed | 14.3% | 26.8% | +12.5% | [+3.6%, +23.2%] | 0.0391 | 0.0782 | exact McNemar |

_With 56 paired scenarios and 32% discordant pairs, a difference in cause accuracy smaller than about 21% would usually go undetected (80% power, alpha 0.05)._

### Where rules-v1 goes wrong (true cause -> predicted)

- pool_undersized: connection_leak x8
- memory_leak: fx_degraded x1, transient x1
- transient: bad_config x8

### Where rules-v2 goes wrong (true cause -> predicted)


### Where ml-logreg goes wrong (true cause -> predicted)

