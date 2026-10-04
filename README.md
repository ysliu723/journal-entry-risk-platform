# Journal Entry Risk Platform

A backend system that ingests general-ledger journal entries, checks that the
population is complete, and ranks entries for audit review with explainable,
configurable risk rules.

> **Status:** early development — Phase 1, core risk engine.

## Development

Requires Python 3.12+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```
