#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成通达信「待选区」自选股同步清单（2026-11-08）。

股票池 = 当日候选（watchlist.candidates）+ 组合名单（最新 portfolio）去重，
输出 data/tdx-pool.json，并打印给执行器（Mika）的指令文本。
用法: python3 tools/pool_for_tdx.py [--text]
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MODELS = os.path.join(ROOT, "models")


def load(p, d=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d


def main():
    snap = load(os.path.join(DATA, "snapshot-2026-11-06.json"), {}) or {}
    name_by = {r["symbol"]: r["name"] for r in snap.get("rows") or []}
    wl = load(os.path.join(DATA, "watchlist.json"), {}) or {}
    pf_files = sorted(glob.glob(os.path.join(MODELS, "portfolio-*.json")))
    pf = load(pf_files[-1], {}) if pf_files else {}
    acct = load(os.path.join(DATA, "account-state.json"), {}) or {}

    pool, seen = [], set()

    def add(sym, name, tag):
        if sym in seen or not sym:
            return
        seen.add(sym)
        pool.append({"symbol": sym, "code": sym[2:], "name": name or name_by.get(sym, ""), "tag": tag})

    for c in wl.get("candidates") or []:
        add(c.get("symbol"), c.get("name"), "候选")
    for p in pf.get("positions") or []:
        add(p.get("symbol"), p.get("name"), "防守组合")
    for h in acct.get("positions") or []:
        add(h.get("symbol"), h.get("name"), "持仓")

    out = {"group": "待选区", "as_of": "2026-11-08", "count": len(pool), "stocks": pool}
    path = os.path.join(DATA, "tdx-pool.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    lines = [f"【通达信自选同步】请把以下 {len(pool)} 只股票加入通达信「自选股」→ 分类「待选区」（已有分类就追加，没有就新建）："]
    for i, s in enumerate(pool, 1):
        lines.append(f"{i}. {s['name']} {s['code']}（{s['tag']}）")
    lines.append("要求：逐只确认已加入「待选区」；完成后回报「已添加 N 只 + 失败名单（若有）」；不要动其他自选分类，不要下单。")
    text = "\n".join(lines)
    if "--text" not in sys.argv:
        print(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
