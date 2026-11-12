#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""股票池评分：记录四角色打分 → 综合评级 A/B/C（2026-11-10）。

用法：
  python3 tools/pool_review.py set --role 润研 --symbol sh600699 --score 8 --note "PE23 汽车板块资金流入"
  python3 tools/pool_review.py grade --symbol sh600699        # 看综合分
  python3 tools/pool_review.py show                          # 当日全部
  python3 tools/pool_review.py apply                          # 把评级写回 pool-<date>.json

评分口径：四角色各 0–10 分；综合 = 加权平均（润策0.2 润研0.3 润量0.3 润控0.2）；
  A 级 ≥8.0（最佳，红）；B 级 6.5–7.99（推荐，黄）；C 级 <6.5（后选，绿）。
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
D = dt.date.today().isoformat()
WEIGHTS = {"润策": 0.2, "润研": 0.3, "润量": 0.3, "润控": 0.2}


def path(day=D):
    return os.path.join(DATA, f"pool-review-{day}.json")


def load(day=D):
    if os.path.exists(path(day)):
        return json.load(open(path(day), encoding="utf-8"))
    return {"date": day, "items": {}}


def save(d, day=D):
    json.dump(d, open(path(day), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def grade_of(score):
    if score is None:
        return "—"
    if score >= 8.0:
        return "A"
    if score >= 6.5:
        return "B"
    return "C"


def composite(item):
    got = {k: v.get("score") for k, v in (item.get("scores") or {}).items() if v.get("score") is not None}
    if not got:
        return None
    total_w = sum(WEIGHTS[k] for k in got)
    return round(sum(v * WEIGHTS[k] for k, v in got.items()) / total_w, 2)


def cmd_set(a):
    d = load()
    it = d["items"].setdefault(a.symbol, {"symbol": a.symbol, "name": a.name or "", "scores": {}})
    if a.name:
        it["name"] = a.name
    it["scores"][a.role] = {"score": a.score, "note": a.note or "", "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
    it["composite"] = composite(it)
    it["grade"] = grade_of(it["composite"])
    save(d)
    print(f"SET {a.symbol} {a.role} {a.score} → 综合 {it['composite']} {it['grade']}")


def cmd_show(a):
    d = load()
    if not d["items"]:
        print("NO REVIEW")
        return 0
    for sym, it in d["items"].items():
        sc = " ".join(f"{k}{v['score']}" for k, v in it["scores"].items())
        print(f"{it.get('name') or ''} {sym}: 综合 {it.get('composite')} 级 {it.get('grade')} | {sc}")
    return 0


def cmd_apply(a):
    d = load()
    # 只认每日池文件 pool-YYYY-MM-DD.json（排除 pool-eval/config/review 等，避免误写）
    pat = re.compile(r"^pool-\d{4}-\d{2}-\d{2}\.json$")
    cands = [f for f in glob.glob(os.path.join(DATA, "pool-*.json")) if pat.match(os.path.basename(f))]
    if not cands:
        print("APPLY SKIP：找不到 data/pool-YYYY-MM-DD.json", file=sys.stderr)
        return 1
    pf = sorted(cands)[-1]
    pool = json.load(open(pf, encoding="utf-8"))
    for p in pool.get("picks") or []:
        it = d["items"].get(p["symbol"])
        if it:
            p["grade"] = it.get("grade")
            p["composite"] = it.get("composite")
            p["role_scores"] = {k: v.get("score") for k, v in it["scores"].items()}
        else:
            p["grade"] = "C"
            p["composite"] = None
    json.dump(pool, open(pf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 持仓评级（同一套四角色分数）
    acct_f = os.path.join(DATA, "account-state.json")
    held = []
    if os.path.exists(acct_f):
        for pos in (json.load(open(acct_f, encoding="utf-8")).get("positions") or []):
            it = d["items"].get(pos["symbol"]) or {}
            held.append({"symbol": pos["symbol"], "name": pos.get("name"),
                         "composite": it.get("composite"), "grade": it.get("grade") if it else None,
                         "role_scores": {k: v.get("score") for k, v in (it.get("scores") or {}).items()}})
    json.dump({"date": D, "holdings": held}, open(os.path.join(DATA, f"holdings-grade-{D}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("APPLIED →", os.path.basename(pf), "| holdings graded", sum(1 for x in held if x["grade"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set"); s.add_argument("--role", required=True); s.add_argument("--symbol", required=True)
    s.add_argument("--score", type=float, required=True); s.add_argument("--note", default=""); s.add_argument("--name", default="")
    s.set_defaults(fn=cmd_set)
    sub.add_parser("show").set_defaults(fn=cmd_show)
    sub.add_parser("apply").set_defaults(fn=cmd_apply)
    a = ap.parse_args()
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
