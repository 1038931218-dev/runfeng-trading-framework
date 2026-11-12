#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日行情→股票池刷新（2026-11-10）。

规则（主板、可负担、趋势不破、不追高）：
  1. 仅沪深主板（600/601/603/605/000/001/002/003），排除 ST/退市
  2. 价格 6–30 元（1 手 ≥600 元，满足最小金额门槛）
  3. 成交额 ≥2 亿；PE>0；市值 100–3000 亿
  4. 趋势：收盘 ≥ MA60*0.97；不追高：收盘 ≤ MA20*1.06
  5. mom20 在 -8% ~ +15%；20 日量比 ≥0.8
评分：趋势(MA20/MA60 斜率) + 回踩贴合度 + 量能；取前 5。
输出：data/pool-YYYY-MM-DD.json（排名+理由）；更新 data/tdx-pool.json（持仓+新候选，供通达信待选区与次日计划）。
用法：python3 tools/refresh_pool.py [--top 5]
"""
import datetime as dt
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import market  # noqa: E402

D = dt.date.today().isoformat()
MAIN = ("600", "601", "603", "605", "000", "001", "002", "003")


def kstats(sym):
    try:
        bars = market.kline(sym, 260)
    except Exception:
        return None
    if not bars or len(bars) < 60:
        return None
    c = [b["close"] for b in bars]
    v = [b.get("volume") or 0 for b in bars]
    n = len(c)
    ma20 = sum(c[-20:]) / 20
    ma60 = sum(c[-60:]) / 60
    mom20 = c[-1] / c[-21] - 1 if n >= 21 else 0
    vr = (sum(v[-5:]) / 5) / (sum(v[-20:]) / 20) if n >= 20 and sum(v[-20:]) else 0
    ma20_prev = sum(c[-25:-5]) / 20 if n >= 25 else ma20
    rets = [c[i] / c[i - 1] - 1 for i in range(n - 20, n)]
    mu = sum(rets) / len(rets)
    vol = (sum((x - mu) ** 2 for x in rets) / len(rets)) ** 0.5 * (252 ** 0.5) * 100
    trs = []
    for i in range(n - 14, n):
        h = max(b["high"] for b in bars[i - 1:i + 1]); l = min(b["low"] for b in bars[i - 1:i + 1])
        pc = c[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs) / len(trs) / c[-1] * 100
    return {"close": c[-1], "ma20": ma20, "ma60": ma60, "mom20": mom20, "vr": vr,
            "ma20_slope": (ma20 - ma20_prev) / ma20_prev if ma20_prev else 0,
            "vol20": round(vol, 1), "atr14": round(atr, 2)}


def main():
    import json as _j
    cfg = {}
    cfg_f = os.path.join(ROOT, "data", "pool-config.json")
    if os.path.exists(cfg_f):
        cfg = _j.load(open(cfg_f, encoding="utf-8"))
    w = cfg.get("weights") or {}
    w_slope = w.get("ma20_slope", 0.30); w_fit = w.get("ma_fit", 0.25)
    w_vr = w.get("vol_ratio", 0.15); w_vol = w.get("volatility", 0.30)
    mom_max = cfg.get("mom20_max", 0.15); amt_min = cfg.get("amount_min_yi", 2)
    vol_min = cfg.get("vol20_min", 25); vol_max = cfg.get("vol20_max", 90)
    top_n = 5
    if "--top" in sys.argv:
        top_n = int(sys.argv[sys.argv.index("--top") + 1])
    snap_f = sorted(glob.glob(os.path.join(ROOT, "data", "snapshot-*.json")))[-1]
    snap = json.load(open(snap_f, encoding="utf-8"))
    rows = snap["rows"]
    wl = json.load(open(os.path.join(ROOT, "data", "watchlist.json"), encoding="utf-8"))
    acct = json.load(open(os.path.join(ROOT, "data", "account-state.json"), encoding="utf-8"))
    held = {p["symbol"] for p in acct.get("positions") or []}

    cands = []
    for r in rows:
        code = str(r.get("code") or "")
        if not code.startswith(MAIN):
            continue
        nm = r.get("name") or ""
        if "ST" in nm.upper() or "退" in nm:
            continue
        px = r.get("price") or 0
        amt = (r.get("amount") or 0) / 1e8
        pe = r.get("pe")
        cap = r.get("mktcap_yi") or 0
        if not (6.0 <= px <= 30.0) or amt < amt_min or not pe or pe <= 0 or not (100 <= cap <= 3000):
            continue
        cands.append(r)
    cands.sort(key=lambda x: -(x.get("amount") or 0))
    cands = cands[:120]

    scored = []
    for r in cands:
        st = kstats(r["symbol"])
        if not st:
            continue
        px = r["price"]
        if px < st["ma60"] * 0.97 or px > st["ma20"] * 1.06:
            continue
        if not (-0.08 <= st["mom20"] <= mom_max) or st["vr"] < 0.8:
            continue
        if not (vol_min <= st["vol20"] <= vol_max):
            continue
        score = (w_slope * st["ma20_slope"] * 10 + w_fit * (1 - abs(px - st["ma20"]) / st["ma20"])
                 + w_vr * min(st["vr"], 2) + w_vol * min(st["vol20"] / 60, 1.5))
        scored.append({"symbol": r["symbol"], "code": r.get("code"), "name": r.get("name"),
                       "price": px, "pct": r.get("pct"), "amount_yi": round((r.get("amount") or 0) / 1e8, 2),
                       "pe": r.get("pe"), "mom20": round(st["mom20"] * 100, 2),
                       "ma20": round(st["ma20"], 2), "ma60": round(st["ma60"], 2),
                       "vol20": st["vol20"], "atr14": st["atr14"],
                       "ma20_slope": round(st["ma20_slope"], 4), "vr": round(st["vr"], 3),
                       "score": round(score, 3)})
    scored.sort(key=lambda x: -x["score"])
    picks = [x for x in scored if x["symbol"] not in held][:top_n]

    out = {"date": D, "snapshot": os.path.basename(snap_f), "rule": "主板/6-30元/成交额≥2亿/PE>0/市值100-3000亿/收盘≥MA60*0.97/≤MA20*1.06/mom20 -8%~15%/量比≥0.8/年化波动25%~90%",
           "universe": len(cands), "scanned": len(scored), "picks": picks}
    path = os.path.join(ROOT, "data", f"pool-{D}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 更新给通达信「待选区」的池子：持仓 + 新候选
    tdx = json.load(open(os.path.join(ROOT, "data", "tdx-pool.json"), encoding="utf-8")) if os.path.exists(os.path.join(ROOT, "data", "tdx-pool.json")) else {}
    old_hold = [x for x in tdx.get("stocks") or [] if x.get("tag") == "持仓"]
    stocks = old_hold + [{"symbol": p["symbol"], "code": p["code"], "name": p["name"], "tag": "候选"} for p in picks]
    seen, dedup = set(), []
    for x in stocks:
        if x["symbol"] in seen:
            continue
        seen.add(x["symbol"])
        dedup.append(x)
    tdx.update({"group": "待选区", "as_of": D, "count": len(dedup), "stocks": dedup})
    json.dump(tdx, open(os.path.join(ROOT, "data", "tdx-pool.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    cand = []
    for i, p in enumerate(picks):
        top = round(min(p["ma20"], p["price"] * 1.03), 2)
        floor = round(min(p["price"], p["ma20"]) * 0.97, 2)
        item = {"symbol": p["symbol"], "name": p["name"], "side": "buy", "qty": 100,
                "zone_top": top, "zone_floor": floor, "priority": i + 2,
                     "cooldown_sec": 3600,
                "note": f"评分{p['score']}；波动{p['vol20']}%；mom20 {p['mom20']}%；额{p['amount_yi']}亿"}
        # 收盘在 MA60 之下 → 必须站回确认才买（避免买在下跌中继）
        if p["price"] < p["ma60"]:
            item["confirm_above"] = round(p["ma60"], 2)
            item["ceiling"] = round(p["ma60"] * 1.03, 2)
            item["note"] += "；低于MA60，须站回确认"
        cand.append(item)
    wl["candidates"] = cand
    wl.setdefault("guards", {})["pool_updated"] = D
    json.dump(wl, open(os.path.join(ROOT, "data", "watchlist.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    try:
        import subprocess as _sp
        _sp.run([sys.executable, os.path.join(ROOT, "tools", "pool_review.py"), "apply"], capture_output=True, timeout=60)
    except Exception:
        pass
    print(f"POOL {D} universe={len(cands)} passed={len(scored)} picks={len(picks)}")
    for i, p in enumerate(picks, 1):
        print(f"  {i}. {p['name']} {p['code']} {p['price']}元 评分{p['score']} mom20 {p['mom20']}% 额{p['amount_yi']}亿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
