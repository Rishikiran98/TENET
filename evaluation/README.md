# Reproduce the bounded comparison

Requires Python 3.11+, NumPy, pytest, SQLite from Python's standard library, and
this repository. No model, API key, paid service, or GPU is used.

Run from the repository root after `pip install -e '.[dev]'`:

```bash
pytest -q
PYTHONPATH=src python evaluation/compare.py --system repaired
python evaluation/compare.py --system conventional
PYTHONPATH=src python evaluation/crash_boundary.py
```

To compare the exact original source, extract the audited commit into a separate
scratch directory, without switching or modifying your working tree:

```bash
mkdir -p /tmp/tenet-original-audit
git archive c7a798368869dbee60923b45de2c09f3ab4d45c8 | tar -x -C /tmp/tenet-original-audit
PYTHONPATH=/tmp/tenet-original-audit/src python evaluation/compare.py --system original
```

`compare.py` adapts the same proposals, grants, filesystem fixtures, clock values,
fixture policy, and filesystem outcome assertions to all three implementations.
Original and repaired TENET use their default in-memory logs for these nine
cases. The baseline uses an in-memory SQLite database. These cases do not test
durability. Each implementation runs in its own Python process.

`conventional.py` is independently implemented using ordinary task and attempt
tables plus direct checks. It imports no TENET code. It deliberately has no
hash chain, raw/context memory projections, retrieval ranking, or LLM. It is a
baseline for the action correctness claims tested here, not a full replacement
for every TENET feature. The two implementations share the documented contract
and fixture policy, but no enforcement implementation.

`crash_boundary.py` separately uses file-backed SQLite for both implementations.
Each child process exits via `os._exit(23)` either immediately before a file
write or after a completed and fsynced file write but before its outcome receipt.
The parent reopens the database, checks pending work, tries task reuse, and
checks the effect state. This models process death, not power loss or a hostile
filesystem. The original has no durable-log adapter and is excluded from this
experiment rather than counted as a failure.

Checked-in `results/` files contain the observed October 10, 2026 run. The
original commit is fixed above; repaired-source changes are in the same commit
as the results. `environment.json` records runtime versions. Timing in pytest
output is incidental, not a performance benchmark.

The nine cases were selected from known correctness findings. They are not a
random sample, a prompt-injection success-rate estimate, or a novelty test.

## Follow-up review validation (0.3.0)

The latest patch uses write schema 2 and an observation-aware Brain protocol.
The original comparison is unchanged; its adapter still supports the original
source API. Current follow-up results are recorded separately in
`results/review2-comparison.json`, `review2-crash-boundary.json`, and
`review2-pytest.txt`. The original result files remain historical evidence.

Append scaling:

```bash
PYTHONPATH=src python evaluation/sqlite_scaling.py --label after
mkdir -p /tmp/tenet-before-incremental
git archive ac05117454f287245458e02212a7af2e05c4218f | tar -x -C /tmp/tenet-before-incremental
PYTHONPATH=/tmp/tenet-before-incremental/src python evaluation/sqlite_scaling.py --label before
```

On a fresh GitHub checkout, substitute `ded3e5ed6fc190cad68bab7d804127b940536952`
for the local before commit. Their trees are identical. Run both measurements
on the same machine for a useful local comparison. The recorded experiment uses
one run at each size and makes no production throughput guarantee.
