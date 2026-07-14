# Lake rerun analysis (piotrg)

This folder contains the reproducible data extraction and charts for the two final
14-task Lake reruns (Level 09 was intentionally excluded):

- GPT-5.6 Sol, high reasoning, Codex harness
- Claude Fable 5, high reasoning, Claude Code harness

The raw Harbor jobs are not committed. Regenerate the processed CSV by passing their
paths explicitly:

```bash
python process_raw_data.py \
  --gpt-job /path/to/gpt56-sol-high-codex-lake-no-hard-timeout-rerun \
  --fable-job /path/to/claude-fable-5-high-claude-code-lake-no-time-prompt-rerun \
  --output processed_data.csv
python generate_charts.py processed_data.csv --output-dir .
```

Both scripts use only the Python standard library.

## Files

- `processed_data.csv` — one row per model/task, including status, wall-clock time,
  cache-aware token classes, API-response count, and cost.
- `process_raw_data.py` — parser for the two different Harbor trajectory formats.
- `generate_charts.py` — deterministic SVG chart generator.
- `cutoff-vs-solved.svg` — cutoff on the x-axis and solved tasks on the y-axis.
- `solved-vs-cutoff.svg` — the same curves with the axes swapped.

## Metric handling

For Codex, cumulative `token_count` events in rollout JSONL are de-duplicated and
converted to per-call deltas. Cached input is treated as a subset of input. Cost is
then computed per API call so that long-context pricing can be applied correctly.

For Claude Code, the final `result` event's `usage` and `total_cost_usd` are
authoritative. Harbor trajectory steps repeat the same response metrics on multiple
derived thinking/tool/content steps, so those tuples are de-duplicated before counting
API responses. The stopped Sunken Temple run has no final Claude result; its tokens and
cost are a lower bound computed from completed response groups in `trajectory.json`.

The embedded pricing snapshot is the OpenRouter pricing observed on 2026-07-14:

- GPT-5.6 Sol: $5/M uncached input, $0.50/M cache read, $30/M output. The doubled
  long-context input/cache rates and $45/M output rate begin at a 272k-token prompt;
  no call in this rerun crossed that threshold.
- Claude Fable 5: $10/M uncached input, $1/M cache read, $12.50/M cache write,
  $50/M output.

Each chart is an empirical per-task cutoff curve. A failure or stopped task does not
advance the solved count, and a model's line stops at its last observed success instead
of being extended to the other model's maximum or to infinity.

Across the 13 successful tasks, Fable reached 13 successes at a 15:46 wall-clock
cutoff versus 69:40 for GPT-5.6 Sol (4.42× faster). GPT-5.6 Sol cost 27.7% less in
aggregate ($29.97 versus $41.47), while Fable used 36.6% fewer total tokens
(10.11M versus 15.95M).
