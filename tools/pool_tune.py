#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选股参数调优：用 pool-eval 的已实现样本做小网格搜索（2026-11-11）。

样本足够（≥20 条已实现 T+1）才调参；不足则维持默认并在报告中说明。
输出 data/pool-config.json（权重/阈值）与 reports/pool-tune.md。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "data", "pool-config.json")
EVAL = os.path.join(ROOT, "data", "pool-eval.json")
MIN_N = 20


def score(r, w, mom_max):
    slope = r.get("ma20_slope") or 0
    fit = 1 - abs((r.get("price") or 0) - (r.get("ma20") or 0)) / (r.get("ma20") or 1) if r.get("ma20") else 0
    vr = min(r.get("vr") or 0, 2)
    vol = min((r.get("vol20") or 0) / 60, 1.5)
    return w[0] * slope * 10 + w[1] * fit + w[2] * vr + w[3] * vol


def main():
    cfg = json.load(open(CFG, encoding="utf-8"))
    ev = json.load(open(EVAL, encoding="utf-8")) if os.path.exists(EVAL) else {"rows": []}
    rows = [r for r in ev.get("rows") or [] if r.get("t1_date") and r.get("win_t1") is not None]
    report = ["# 选股参数调优", "", f"已实现样本 **{len(rows)}** 条（阈值 {MIN_N}）", ""]
    if len(rows) < MIN_N:
        report += [f"- 样本不足，**维持当前参数**：{json.dumps(cfg.get('weights'), ensure_ascii=False)}",
                   "- 待样本≥20 后再做网格搜索；避免用 1–2 天数据过拟合"]
        print(f"POOL_TUNE n={len(rows)} < {MIN_N}，维持默认")
    else:
        best = None
        for ws in (0.20, 0.30, 0.40):
            for wv in (0.20, 0.30, 0.40):
                for wf in (0.15, 0.25):
                    wv2 = max(0.05, 1 - ws - wv - wf)
                    w = (ws, wf, wv, wv2)
                    wins = 0
                    for r in rows:
                        s = score(r, w, cfg.get("mom20_max", 0.15))
                        # 简化：高分（前 60% 分位）样本视为会被选中
                        if s >= 0.55 and r["win_t1"]:
                            wins += 1
                    rate = wins / len(rows)
                    if best is None or rate > best[0]:
                        best = (rate, w)
        rate, w = best
        cfg["weights"] = {"ma20_slope": round(w[0], 2), "ma_fit": round(w[1], 2),
                          "vol_ratio": round(w[2], 2), "volatility": round(w[3], 2)}
        cfg["updated"] = __import__("datetime").date.today().isoformat()
        json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        report += [f"- 最优权重：{json.dumps(cfg['weights'], ensure_ascii=False)}", f"- 样本命中率（简化口径）：{rate*100:.1f}%"]
        print(f"POOL_TUNE updated weights {cfg['weights']} rate={rate:.2f}")
    open(os.path.join(ROOT, "reports", "pool-tune.md"), "w", encoding="utf-8").write("\n".join(report) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
