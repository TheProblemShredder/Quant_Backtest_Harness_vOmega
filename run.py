#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, random, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

def canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def write_text(path: Path, s: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s, encoding="utf-8")

def write_json(path: Path, obj: Any) -> None:
    write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")

def file_sha256(path: Path) -> str:
    return sha256_hex(path.read_bytes())

def append_ledger(out_dir: Path, entry: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    entry = dict(entry)
    entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with (out_dir / "ledger.ndjson").open("a", encoding="utf-8") as f:
        f.write(canon(entry) + "\n")

@dataclass(frozen=True)
class Prereg:
    n_days: int = 252
    fee_bps: float = 1.0          # per trade, bps
    slippage_bps: float = 2.0     # per trade, bps
    entry_z: float = 1.0          # signal threshold
    baseline_sharpe_min: float = 0.50
    delta_min: float = 0.30       # baseline - ablation sharpe
    neg_sharpe_max: float = 0.25  # negative control (random) must stay low

def gen_prices(rng: random.Random, n: int) -> List[float]:
    # lognormal-ish random walk with mild drift
    p = 100.0
    out = [p]
    for _ in range(n-1):
        r = rng.gauss(0.0004, 0.01)
        p *= (1.0 + r)
        out.append(p)
    return out

def returns(prices: List[float]) -> List[float]:
    return [(prices[i]/prices[i-1]-1.0) for i in range(1, len(prices))]

def sharpe(rets: List[float]) -> float:
    if not rets:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((x-mu)**2 for x in rets) / max(1, len(rets)-1)
    sd = var**0.5
    if sd == 0:
        return 0.0
    # annualize with sqrt(252)
    return (mu / sd) * (252**0.5)

def backtest(pr: Prereg, rng: random.Random, mode: str) -> Dict[str, Any]:
    prices = gen_prices(rng, pr.n_days)
    rets = returns(prices)

    # Simple "signal": yesterday return z-scored over short window (approx)
    # baseline: trade with signal; ablation: trade always (no signal); neg: random positions
    pos = 0  # -1,0,+1
    pnl: List[float] = []
    win = 20
    for i in range(len(rets)):
        r = rets[i]
        # rolling stats
        start = max(0, i-win)
        window = rets[start:i+1]
        mu = sum(window)/len(window)
        var = sum((x-mu)**2 for x in window)/max(1, len(window)-1) if len(window) > 1 else 0.0
        sd = var**0.5 if var > 0 else 1e-9
        z = (r - mu) / sd

        new_pos = pos
        if mode == "baseline":
            if z > pr.entry_z:
                new_pos = 1
            elif z < -pr.entry_z:
                new_pos = -1
            else:
                new_pos = 0
        elif mode == "ablation":
            # ablated: ignore signal, always long
            new_pos = 1
        elif mode == "negative_control":
            new_pos = 0  # flat: deterministic negative control

        traded = (new_pos != pos)
        pos = new_pos

        # trading costs on trade days
        cost = 0.0
        if traded:
            bps = (pr.fee_bps + pr.slippage_bps)
            cost = bps * 1e-4  # bps -> fraction

        pnl.append(pos * r - cost)

    eq = 1.0
    for x in pnl:
        eq *= (1.0 + x)
    return {"final_equity": eq, "sharpe": sharpe(pnl), "n_trades_approx": sum(1 for i in range(1,len(pnl)) if pnl[i]!=pnl[i-1])}

def prereg(seed: int) -> Dict[str, Any]:
    pr = Prereg()
    obj = {
        "seed": seed,
        "params": pr.__dict__,
        "conditions": ["baseline", "ablation", "negative_control"],
        "metric": "sharpe",
        "notes": "Tiny deterministic backtest harness. Stdlib only.",
    }
    aeq = sha256_hex(canon(obj).encode("utf-8"))[:12]
    cid = sha256_hex(f"{aeq}:{seed}".encode("utf-8"))[:12]
    obj["AEQ"] = aeq
    obj["CID"] = cid
    return obj

def run_once(out_dir: Path, seed: int, blind: bool, reveal: bool) -> int:
    rng = random.Random(seed)
    pr_blob = prereg(seed)
    pr = Prereg()

    out_dir.mkdir(parents=True, exist_ok=True)
    prereg_path = out_dir / "prereg.json"
    write_json(prereg_path, pr_blob)
    append_ledger(out_dir, {"event":"prereg_written","file":"prereg.json","sha256":file_sha256(prereg_path),"AEQ":pr_blob["AEQ"],"CID":pr_blob["CID"]})

    labels = {"baseline":"baseline","ablation":"ablation","negative_control":"negative_control"}
    blind_map_path = out_dir / "blind_map.json"
    if blind:
        conds = pr_blob["conditions"][:]
        rng.shuffle(conds)
        labels = {cond: f"C{idx+1}" for idx, cond in enumerate(conds)}
        write_json(blind_map_path, {"seed": seed, "map_real_to_blind": labels})
        append_ledger(out_dir, {"event":"blind_map_written","file":"blind_map.json","sha256":file_sha256(blind_map_path)})

    # Backtests
    base = backtest(pr, random.Random(seed + 1), "baseline")
    ablt = backtest(pr, random.Random(seed + 2), "ablation")
    neg  = backtest(pr, random.Random(seed + 3), "negative_control")

    base_s = base["sharpe"]
    ablt_s = ablt["sharpe"]
    neg_s  = neg["sharpe"]
    delta = base_s - ablt_s

    gates = {
        "baseline_sharpe_min": {"value": base_s, "min": pr.baseline_sharpe_min, "pass": base_s >= pr.baseline_sharpe_min},
        "delta_min": {"value": delta, "min": pr.delta_min, "pass": delta >= pr.delta_min},
        "neg_sharpe_max": {"value": neg_s, "max": pr.neg_sharpe_max, "pass": neg_s <= pr.neg_sharpe_max},
    }
    overall = all(v["pass"] for v in gates.values())

    results = {
        "AEQ": pr_blob["AEQ"],
        "CID": pr_blob["CID"],
        "seed": seed,
        "blind": blind,
        "metrics": {
            labels["baseline"]: base,
            labels["ablation"]: ablt,
            labels["negative_control"]: neg,
            "delta_sharpe(baseline-ablation)": delta,
        },
        "gates": gates,
        "overall_pass": overall,
    }

    results_path = out_dir / "results.json"
    write_json(results_path, results)
    append_ledger(out_dir, {"event":"results_written","file":"results.json","sha256":file_sha256(results_path),"overall_pass":overall})

    manifest = {
        "AEQ": pr_blob["AEQ"],
        "CID": pr_blob["CID"],
        "files": {
            "prereg.json": file_sha256(prereg_path),
            "results.json": file_sha256(results_path),
            "ledger.ndjson": file_sha256(out_dir / "ledger.ndjson"),
            **({"blind_map.json": file_sha256(blind_map_path)} if blind else {}),
        },
    }
    write_json(out_dir / "artifacts_manifest.json", manifest)

    if reveal and blind and blind_map_path.exists():
        print("BLIND MAP (reveal):")
        print(blind_map_path.read_text(encoding="utf-8"))

    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0 if overall else 2

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="outputs_unblind")
    ap.add_argument("--seed", type=int, default=123)
    ap.add_argument("--blind", action="store_true")
    ap.add_argument("--reveal", action="store_true")
    a = ap.parse_args()
    return run_once(Path(a.out), a.seed, a.blind, a.reveal)

if __name__ == "__main__":
    raise SystemExit(main())
