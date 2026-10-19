import { createHash } from 'node:crypto';
import type { Experiment } from './types';

export const BUCKETS = 10_000;

/**
 * Deterministic bucket in [0, 10000) for (salt, experiment, unit).
 *
 * The salt is per experiment so two experiments over the same units are independent
 * (a unit in variant B of one experiment is not more likely to be in B of another).
 * Changing the salt of a RUNNING experiment would reshuffle units between arms and
 * corrupt the comparison, which is why the API refuses it.
 */
export function bucketOf(salt: string, experiment: string, unit: string): number {
  const digest = createHash('sha256').update(`${salt}:${experiment}:${unit}`).digest();
  // first 4 bytes as an unsigned int: uniform enough for 10k buckets
  return digest.readUInt32BE(0) % BUCKETS;
}

/** Maps a bucket to a variant by cumulative weight. */
export function variantFor(exp: Experiment, bucket: number): string {
  const total = exp.variants.reduce((a, v) => a + v.weight, 0);
  let edge = 0;
  for (const v of exp.variants) {
    edge += (v.weight / total) * BUCKETS;
    if (bucket < edge) return v.name;
  }
  // floating point can leave the last edge at 9999.999...; the last variant owns the rest
  return exp.variants[exp.variants.length - 1]!.name;
}

export function assign(exp: Experiment, unit: string): { variant: string; bucket: number } {
  const bucket = bucketOf(exp.salt, exp.name, unit);
  return { variant: variantFor(exp, bucket), bucket };
}
