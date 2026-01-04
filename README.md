# Quant Backtest Harness vΩ (Public Demo)

![CI](https://github.com/TheProblemShredder/Quant_Backtest_Harness_vOmega/actions/workflows/ci.yml/badge.svg)


A tiny, reproducible backtest scaffold demonstrating verification-first discipline:

- preregistered parameters + thresholds
- baseline vs ablation delta gate
- negative control gate
- deterministic IDs (AEQ/CID)
- append-only audit ledger (outputs_*/ledger.ndjson)
- artifact manifest with sha256 hashes
- CI runs and asserts artifacts exist

Stdlib only.

Run:
  python3 run.py --out outputs_unblind --seed 123
  python3 run.py --out outputs_blind  --seed 123 --blind --reveal
