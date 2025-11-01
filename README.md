## Environment Setup
- Python 3.13+

## Running Unit Tests
```bash
python3 -m unittest discover -s tests -p 'test*.py'
```

## Admin Utility
Bootstraps a broker with evenly partitioned subscribers and a background publisher that emits random bytes.

```bash
python3 admin.py <subscribers> [--publish-interval SECONDS] [--duration SECONDS] [--seed INT]
```

Arguments:
- `subscribers` (1–256): number of subscribers; the payload space 0–255 is split evenly among them.
- `--publish-interval` (default `1.0`): seconds between random publishes.
- `--duration`: optional runtime in seconds before the utility exits; omit to run until Ctrl+C.
- `--seed`: optional random seed for deterministic payload generation.

Example:
```bash
python3 admin.py 4 --publish-interval 0.05 --duration 2 --seed 123
```
This prints each subscriber’s payload range and running count totals while the publisher loop runs.
