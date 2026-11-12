#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日要点摘要（2026-11-07，省 token 用）。

把 data/ 下的大文件（快照 1.6MB、行业、指数、组合、风险、计划）压成一份
~50 行的紧凑摘要，供 Agent 和各角色引用，避免反复读取大 JSON / 长报告。

用法:
  python3 tools/daily_brief.py                 # 输出当天（否则最近交易日）
  python3 tools/daily_brief.py --date 2026-11-06
  python3 tools/daily_brief.py --json          # 输出紧凑 JSON（供程序消费）
输出: data/brief-<date>.json + 控制台摘要
"""
import argparse
import glob
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MODELS = os.path.join(ROOT, "models")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def pct(x, nd=2):
    return None if x is None else round(x, nd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    date = a.date
    if not date:
        snaps = sorted(glob.glob(os.path.join(DATA, "snapshot-*.json")))
        if not snaps:
            print("没有快照文件", file=sys.stderr)
            return 1
        date = os.path.basename(snaps[-1]).replace("snapshot-", "").replace(".json", "")

    snap = load(os.path.join(DATA, f"snapshot-{date}.json"), {}) or {}
    rows = snap.get("rows") or []
    breadth = snap.get("breadth") or {}
    ind_file = os.path.join(DATA, f"industry-{date}.json")
    if not os.path.exists(ind_file):
        cand = sorted(glob.glob(os.path.join(DATA, "industry-*.json")))
        ind_file = cand[-1] if cand else ind_file
    ind = (load(ind_file) or {}).get("rows") or []
    sectors_source = os.path.basename(ind_file) if ind else None
    idx = load(os.path.join(DATA, "index-tech-crosscheck.json")) or {}
    pf = load(sorted(glob.glob(os.path.join(MODELS, "portfolio-*.json")))[-1], {}) if glob.glob(os.path.join(MODELS, "portfolio-*.json")) else {}
    risk = load(os.path.join(DATA, f"risk-{date}.json")) or {}
    plan = load(os.path.join(DATA, f"plan-{date}.json")) or {}
    if not plan:
        plans = sorted(glob.glob(os.path.join(DATA, "plan-*.json")))
        plan = load(plans[-1]) if plans else {}
        plan = dict(plan, _stale=True) if plan else plan
    wl = load(os.path.join(DATA, "watchlist.json")) or {}
    acct = load(os.path.join(DATA, "account-state.json")) or {}

    act = [r for r in rows if r.get("price", 0) > 0]
    pcts = [r["pct"] for r in act if isinstance(r.get("pct"), (int, float))]
    top = sorted(ind, key=lambda r: -r["pct"])[:5] if ind else []
    bot = sorted(ind, key=lambda r: r["pct"])[:5] if ind else []
    top_amt = sorted(ind, key=lambda r: -r["amount_yi"])[:5] if ind else []
    up_ind = [r for r in ind if r["pct"] > 0]

    brief = {
        "date": date,
        "breadth": {
            "total": len(rows), "active": len(act),
            "up": sum(1 for p in pcts if p > 0.01), "down": sum(1 for p in pcts if p < -0.01),
            "flat": sum(1 for p in pcts if -0.01 <= p <= 0.01),
            "median_pct": pct(statistics.median(pcts)) if pcts else None,
            "limit_up": breadth.get("limit_up"), "limit_down": breadth.get("limit_down"),
            "amount_yi": breadth.get("amount_yi"),
        },
        "indices": {k: {"name": v.get("name"), "close": v.get("close"), "ma20": v.get("ma20"),
                        "ma60": v.get("ma60"), "ma250": v.get("ma250"),
                        "low250": v.get("low250"), "high250": v.get("high250")}
                    for k, v in idx.items()},
        "sectors_source": sectors_source,
        "sectors": {
            "count": len(ind), "up": len(up_ind), "down": len(ind) - len(up_ind),
            "median_pct": pct(statistics.median([r["pct"] for r in ind])) if ind else None,
            "top5": [[r["name"], r["pct"]] for r in top],
            "bottom5": [[r["name"], r["pct"]] for r in bot],
            "top5_by_amount": [[r["name"], r["amount_yi"], r["pct"]] for r in top_amt],
        },
        "portfolio": {
            "tier": pf.get("position_tier"), "stock_pct": pf.get("stock_pct"),
            "cash_pct": pf.get("cash_pct"),
            "names": [p["name"] for p in (pf.get("positions") or [])],
            "all_stops_pct": (pf.get("hard_constraints") or {}).get("all_stops_total_pct"),
        },
        "risk": {
            "portfolio_vol": ((risk.get("objects") or {}).get("portfolio_12") or {}).get("annual_volatility_pct"),
            "portfolio_mdd": ((risk.get("objects") or {}).get("portfolio_12") or {}).get("max_drawdown_pct"),
            "vanke_garch_var95_pct": ((risk.get("objects") or {}).get("vanke_400") or {}).get("garch_var95_daily_pct"),
            "noise_prone_1d": ((risk.get("checks") or {}).get("B_stop_vs_garch_var95") or {}).get("noise_prone_1d_symbols"),
        },
        "account": {
            "total_assets": acct.get("total_assets"), "cash": acct.get("cash"),
            "positions": [[p.get("symbol"), p.get("name"), p.get("qty"), p.get("cost")] for p in acct.get("positions") or []],
        },
        "plan": {
            "date": plan.get("date"),
            "rules": [[r["id"], r.get("op"), r.get("price"), r.get("field", "price"), r.get("action")] for r in plan.get("rules") or []],
        },
        "watchlist": {
            "candidates": [[c.get("symbol"), c.get("name"), c.get("zone_top"), c.get("zone_floor")] for c in wl.get("candidates") or []],
            "emergency": wl.get("emergency"),
        },
    }
    out = os.path.join(DATA, f"brief-{date}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(brief, f, ensure_ascii=False, indent=1)

    if a.json:
        print(json.dumps(brief, ensure_ascii=False))
        return 0

    b = brief["breadth"]
    print(f"# 每日要点 {date}（完整 JSON: {out}）")
    print(f"## 宽度：{b['up']}涨/{b['down']}跌/{b['flat']}平 中位{b['median_pct']}% 涨停{b['limit_up']} 跌停{b['limit_down']} 成交{b['amount_yi']}亿（可交易{b['active']}）")
    print("## 指数（收盘 / MA20 / MA60 / MA250 / 250日低）")
    for k, v in brief["indices"].items():
        print(f"  {v['name']} {v['close']} / {v['ma20']} / {v['ma60']} / {v['ma250']} / {v['low250']}")
    s = brief["sectors"]
    print(f"## 板块：{s['count']}个 涨{s['up']}跌{s['down']} 中位{s['median_pct']}%")
    print("  领涨 " + "、".join(f"{n}{p:+.2f}%" for n, p in s["top5"]))
    print("  领跌 " + "、".join(f"{n}{p:+.2f}%" for n, p in s["bottom5"]))
    print("  成交前五 " + "、".join(f"{n}{a}亿({p:+.2f}%)" for n, a, p in s["top5_by_amount"]))
    p = brief["portfolio"]
    print(f"## 组合：{p['tier']} 股票{p['stock_pct']}% 现金{p['cash_pct']}% 全止损{p['all_stops_pct']}%")
    print("  " + "、".join(p["names"]))
    ac = brief["account"]
    if ac.get("total_assets"):
        print(f"## 账户：总资产{ac['total_assets']} 可用{ac['cash']} 持仓{ac['positions']}")
    pd = brief["plan"].get("date")
    stale = "（stale，非当天计划）" if brief["plan"].get("_stale") else ""
    print(f"## 计划：{pd or '无'}（{len(brief['plan']['rules'])}条规则）{stale}")
    print(f"## 候选：" + "、".join(f"{n}({lo}–{hi})" for _, n, hi, lo in brief["watchlist"]["candidates"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
