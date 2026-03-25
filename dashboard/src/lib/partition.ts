/**
 * Partition the [0, 255] payload space into n non-overlapping ranges.
 * Mirrors aether.core.payload_range.partition_payload_space.
 */
export function partitionPayloadSpace(n: number): { low: number; high: number }[] {
  const ranges: { low: number; high: number }[] = [];
  for (let i = 0; i < n; i++) {
    const low = Math.floor((i * 256) / n);
    const high = Math.floor(((i + 1) * 256) / n) - 1;
    ranges.push({ low, high });
  }
  return ranges;
}
