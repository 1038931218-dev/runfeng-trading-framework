#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选股结果评估：把历史 pool-<date>.json 的候选，用次日/次两日真实走势打分（2026-11-11）。

口径（买入当天或第二天上涨 = 成功）：
  - 基准价 = 选股日收盘价（pool picks.price）
  - T+1：次日最高/收盘；T+2：第二日收盘
  - win_t1 = 次日最高 ≥ 基准×1.01；win_close_t1 = 次日收盘 > 基准
  - win_t2 = 第二日收盘 > 基准
输出 data/pool-eval.json（逐条）+ reports/pool-eval.md（汇总）。
用法：python3 tools/pool_eval.py
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import market  # noqa: E402


def main():
    files = [f for f in sorted(glob.glob(os.path.join(ROOT, "data", "pool-*.json"))) if "pool-review" not in f]
    rows = []
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        date = d.get("date")
        for pk in d.get("picks") or []:
            try:
                bars = market.kline(pk["symbol"], 20)
            except Exception:
                continue
            idx = next((i for i, b in enumerate(bars) if b["date"] > date), None)
            if idx is None:
                rows.append({**{k: pk.get(k) for k in ("symbol", "name", "price", "vol20", "mom20", "vr", "ma20_slope", "score")},
                             "date": date, "status": "待验证"})
                continue
            base = pk["price"]
            t1 = bars[idx]
            t2 = bars[idx + 1] if idx + 1 < len(bars) else None
            rows.append({
                "date": date, "symbol": pk["symbol"], "name": pk.get("name"), "base": base,
                "vol20": pk.get("vol20"), "mom20": pk.get("mom20"), "vr": pk.get("vr"),
                "ma20_slope": pk.get("ma20_slope"), "score": pk.get("score"),
                "t1_date": t1["date"], "t1_high_pct": round((t1["high"] / base - 1) * 100, 2),
                "t1_close_pct": round((t1["close"] / base - 1) * 100, 2),
                "t2_close_pct": round((t2["close"] / base - 1) * 100, 2) if t2 else None,
                "win_t1": t1["high"] >= base * 1.01,
                "win_close_t1": t1["close"] > base,
                "win_t2": (t2["close"] > base) if t2 else None,
            })
    # 多策略选股评估（picks-*.json 带 strategy）
    strat_rows = []
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "picks-*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        date = d.get("date")
        for pk in d.get("picks") or []:
            try:
                bars = market.kline(pk["symbol"], 20)
            except Exception:
                continue
            idx = next((i for i, b in enumerate(bars) if b["date"] > date), None)
            if idx is None:
                strat_rows.append({"date": date, "strategy": pk.get("strategy"), "name": pk.get("name"),
                                   "symbol": pk["symbol"], "status": "待验证"})
                continue
            base = pk["price"]; t1 = bars[idx]
            strat_rows.append({"date": date, "strategy": pk.get("strategy"), "name": pk.get("name"),
                               "symbol": pk["symbol"], "base": base,
                               "t1_high_pct": round((t1["high"] / base - 1) * 100, 2),
                               "t1_close_pct": round((t1["close"] / base - 1) * 100, 2),
                               "win_t1": t1["high"] >= base * 1.01,
                               "win_close": t1["close"] > base})

    done = [r for r in rows if r.get("t1_date")]
    summary = {
        "n": len(done),
        "win_t1": sum(1 for r in done if r["win_t1"]),
        "win_close_t1": sum(1 for r in done if r["win_close_t1"]),
        "win_t2": sum(1 for r in done if r.get("win_t2")),
        "avg_t1_high_pct": round(sum(r["t1_high_pct"] for r in done) / len(done), 2) if done else None,
        "avg_t1_close_pct": round(sum(r["t1_close_pct"] for r in done) / len(done), 2) if done else None,
        "avg_t2_close_pct": round(sum(r["t2_close_pct"] for r in done if r.get("t2_close_pct") is not None) /
                                max(1, sum(1 for r in done if r.get("t2_close_pct") is not None)), 2) if done else None,
    }
    by_strategy = {}
    for r in strat_rows:
        if r.get("win_t1") is None:
            continue
        k = r.get("strategy") or "未标注"
        d = by_strategy.setdefault(k, {"n": 0, "win_t1": 0, "win_close": 0, "sum_high": 0.0})
        d["n"] += 1
        d["win_t1"] += 1 if r["win_t1"] else 0
        d["win_close"] += 1 if r["win_close"] else 0
        d["sum_high"] += r["t1_high_pct"]
    for k, d in by_strategy.items():
        d["win_rate"] = round(d["win_t1"] / d["n"] * 100, 1)
        d["avg_t1_high_pct"] = round(d["sum_high"] / d["n"], 2)
        d.pop("sum_high", None)
    out = {"generated": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"), "summary": summary, "rows": rows, "strategy_rows": strat_rows, "by_strategy": by_strategy}
    json.dump(out, open(os.path.join(ROOT, "data", "pool-eval.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    lines = ["# 选股结果评估", "", f"样本 {summary['n']} 条（成功定义：次日最高 ≥+1%）", ""]
    if summary["n"]:
        lines += [f"- 次日盘中冲高 ≥+1%：**{summary['win_t1']}/{summary['n']}**（{summary['win_t1']/summary['n']*100:.0f}%）",
                  f"- 次日收盘上涨：**{summary['win_close_t1']}/{summary['n']}**（{summary['win_close_t1']/summary['n']*100:.0f}%）",
                  f"- 第二日收盘上涨：**{summary['win_t2']}/{summary['n']}**",
                  f"- 平均次日最高 {summary['avg_t1_high_pct']}%｜平均次日收盘 {summary['avg_t1_close_pct']}%｜平均 T+2 {summary['avg_t2_close_pct']}%"]
    lines += ["", "| 选股日 | 名称 | 基准 | T+1最高 | T+1收盘 | T+2收盘 | 成功 |", "|---|---|---:|---:|---:|---:|:--:|"]
    for r in rows:
        if r.get("t1_date"):
            lines.append(f"| {r['date']} | {r['name']} | {r['base']} | {r['t1_high_pct']}% | {r['t1_close_pct']}% | {r.get('t2_close_pct','—')}% | {'✅' if r['win_t1'] else '❌'} |")
    lines += ["", "## 分策略表现（次日最高 ≥+1% 为成功）", "", "| 策略 | 样本 | 成功 | 成功率 | 平均次日最高 |", "|---|---:|---:|---:|---:|"]
    for k, d in sorted(by_strategy.items(), key=lambda kv: -kv[1]["win_rate"]):
        lines.append(f"| {k} | {d['n']} | {d['win_t1']} | {d['win_rate']}% | {d['avg_t1_high_pct']}% |")
    if not by_strategy:
        lines.append("| （暂无已实现样本） | 0 | 0 | — | — |")
    open(os.path.join(ROOT, "reports", "pool-eval.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"POOL_EVAL n={summary['n']} win_t1={summary['win_t1']} win_close={summary['win_close_t1']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
