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
