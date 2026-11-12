#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多策略选股（2026-11-12 重写）：六策略 + 行业过滤 + 策略风控闸门 + 成本提示。

策略：
  A 回踩MA20     多头排列 + 缩量回踩 MA20 上方 2% 内
  B 突破20日高   放量突破 20 日高
  C 涨停回踩     昨涨停 + 今日缩量踩 MA5
  D 平台突破     平台高点附近放量突破（不追高）
  E 超跌反弹     20 日跌幅 10–35% + 站回 MA5 + 缩量
  F 多头浅回踩   均线多头 + 回踩 MA10

过滤：
  1 可负担：1 手金额 ≤ 单票 20% 总资产 → 价格上限 price_cap
  2 行业：只保留「涨幅为正的板块」；若不足 5 个板块，回退到「成交额前 8」
  3 策略闸门：指数在 MA20 下方时禁用 C/E（下跌中继/情绪接力）

输出：data/picks-<date>.json + 控制台；`--limit N` 限制扫描数量（默认 250），`--no-industry` 关闭行业过滤。

用法: python3 tools/pick_stocks.py [--top 10] [--limit 120] [--no-industry]
"""
import datetime as dt
import glob
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import market  # noqa: E402

D = dt.date.today().isoformat()
MAIN = ("600", "601", "603", "605", "000", "001", "002", "003")
CACHE = os.path.join(ROOT, "data", "cache")


def kline_cached(sym, n=60):
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, f"kline-{sym}-{D}.json")
    if os.path.exists(f):
        try:
            return json.load(open(f, encoding="utf-8"))
        except Exception:
            pass
    try:
        b = market.kline(sym, n)
    except Exception:
        b = None
    if b:
        try:
            json.dump(b, open(f, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass
    return b


def stats(sym):
    b = kline_cached(sym, 60)
    if not b or len(b) < 30:
        return None
    c = [x["close"] for x in b]
    h = [x["high"] for x in b]
    l = [x["low"] for x in b]
    v = [x.get("volume") or 0 for x in b]
    n = len(c)
    ma5 = sum(c[-5:]) / 5
    ma10 = sum(c[-10:]) / 10
    ma20 = sum(c[-20:]) / 20
    ma60 = sum(c[-60:]) / 60 if n >= 60 else ma20
    mom20 = c[-1] / c[-21] - 1 if n >= 21 else 0
    vr = (sum(v[-5:]) / 5) / (sum(v[-20:]) / 20) if sum(v[-20:]) else 0
    rets = [c[i] / c[i - 1] - 1 for i in range(n - 20, n)]
    mu = sum(rets) / len(rets)
    vol = (sum((x - mu) ** 2 for x in rets) / len(rets)) ** 0.5 * (252 ** 0.5) * 100
    return dict(close=c[-1], ma5=ma5, ma10=ma10, ma20=ma20, ma60=ma60, mom20=mom20, vr=vr,
                high20=max(h[-20:]), vol20=vol, prev_pct=(c[-1] / c[-2] - 1) * 100)


def load_industry():
    f = os.path.join(ROOT, "data", f"industry-map-{D}.json")
    if not os.path.exists(f):
        return {}, set(), "无行业表"
    d = json.load(open(f, encoding="utf-8"))
    secs = d.get("sectors") or []
    strong = {s["name"] for s in secs if (s.get("pct") or 0) > 0}
    mode = "涨幅为正"
    if len(strong) < 5:
        strong = {s["name"] for s in sorted(secs, key=lambda x: -(x.get("amount_yi") or 0))[:8]}
        mode = "成交额前8（弱市回退）"
    return d.get("symbol_sector") or {}, strong, mode


def breakeven(amount):
    buy_fee = max(5.0, amount * 0.00025)
    sell_fee = max(5.0, amount * 0.00025) + amount * 0.0005
    return round((buy_fee + sell_fee) / amount * 100, 2)


def strategies(r, st, px, name):
    """返回 (strategy, score, note) 或 None。"""
    ma5, ma10, ma20, ma60 = st["ma5"], st["ma10"], st["ma20"], st["ma60"]
    vr, mom, vol = st["vr"], st["mom20"], st["vol20"]
    # A 回踩 MA20
    if ma20 > ma60 and abs(px - ma20) / ma20 <= 0.02 and 0.4 <= vr <= 1.2 and 0 <= mom <= 0.12 and px >= ma60:
        return ("A 回踩MA20", 3 + (1 - abs(px - ma20) / ma20) * 5 + min(vr, 1) * 2,
                f"MA20 {ma20:.2f} 上方多头，缩量 {vr:.2f}，mom20 {mom*100:.1f}%")
    # B 突破 20 日高
    if px >= st["high20"] * 0.995 and vr >= 1.3 and 0.02 <= mom <= 0.25 and ma20 > ma60:
        return ("B 突破20日高", 3 + min(vr, 2) * 2 + min(mom, 0.2) * 10,
                f"突破 {st['high20']:.2f}，量比 {vr:.2f}，mom20 {mom*100:.1f}%")
    # C 涨停回踩（昨涨停 + 今缩量踩 MA5）
    if st["prev_pct"] >= 9.8 and ma5 * 0.97 <= px <= ma5 * 1.01 and vr <= 0.8:
        return ("C 涨停回踩", 4 + (1 - vr) * 3, f"昨涨停 {st['prev_pct']:.1f}%，今缩量回踩 MA5 {ma5:.2f}")
    # D 平台突破
    if 0.995 <= px / st["high20"] <= 1.03 and vr >= 1.5 and 0.03 <= mom <= 0.18 and ma20 > ma60 and st["prev_pct"] < 5:
        return ("D 平台突破", 3.5 + min(vr, 2.5) * 1.6 + min(mom, 0.18) * 8,
                f"平台 {st['high20']:.2f} 附近放量 {vr:.2f}，mom20 {mom*100:.1f}%")
    # E 超跌反弹
    if -0.35 <= mom <= -0.10 and px >= ma5 and vr <= 1.1 and st["prev_pct"] > -3:
        return ("E 超跌反弹", min(10, 3 + abs(mom) * 20 + (1 - vr) * 2),
                f"20日跌幅 {mom*100:.1f}%，站回 MA5 {ma5:.2f}，缩量 {vr:.2f}")
    # F 多头浅回踩
    if ma5 > ma10 > ma20 > ma60 and abs(px - ma10) / ma10 <= 0.015 and 0.5 <= vr <= 1.3 and 0.05 <= mom <= 0.20:
        return ("F 多头浅回踩", 4 + (1 - abs(px - ma10) / ma10) * 4 + min(mom, 0.2) * 6,
                f"均线多头排列，回踩 MA10 {ma10:.2f}，mom20 {mom*100:.1f}%")
    return None


def main():
    top_n = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 8
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 250

    snap_f = sorted(glob.glob(os.path.join(ROOT, "data", "snapshot-*.json")))[-1]
    rows = json.load(open(snap_f, encoding="utf-8"))["rows"]

    # 可负担上限
    price_cap = 30.0
    try:
        a = json.load(open(os.path.join(ROOT, "data", "account-state.json"), encoding="utf-8"))
        if a.get("total_assets"):
            price_cap = round(a["total_assets"] * 0.20 / 100, 2)
    except Exception:
        pass

    cand = []
    for r in rows:
        code, name = str(r.get("code") or ""), r.get("name") or ""
        if not code.startswith(MAIN) or "ST" in name.upper() or "退" in name:
            continue
        px, amt = r.get("price") or 0, (r.get("amount") or 0) / 1e8
        if px < 4 or px > price_cap or amt < 2:
            continue
        cand.append(r)
    cand.sort(key=lambda x: -(x.get("amount") or 0))
    cand = cand[:lim]

    with ThreadPoolExecutor(max_workers=8) as ex:
        stats_list = list(ex.map(lambda x: stats(x["symbol"]), cand))

    sec_of, strong, sec_mode = load_industry()

    idx_above = True
    try:
        ib = kline_cached("sh000001", 20)
        if ib:
            idx_above = ib[-1]["close"] >= sum(x["close"] for x in ib[-20:]) / 20
    except Exception:
        pass
    blocked = set() if idx_above else {"C 涨停回踩", "E 超跌反弹"}

    picks = []
    for r, st in zip(cand, stats_list):
        if not st:
            continue
        px, name = r["price"], r["name"]
        sector = sec_of.get(r["symbol"]) or ""
        if strong and sector and sector not in strong and "--no-industry" not in sys.argv:
            continue
        got = strategies(r, st, px, name)
        if not got:
            continue
        strat, score, note = got
        if strat in blocked:
            continue
        v = st["vol20"]
        if 30 <= v <= 60:
            score += 0.6
        elif v > 85:
            score -= 0.8
        picks.append({"symbol": r["symbol"], "name": name, "price": px, "strategy": strat,
                      "score": round(min(10, score), 2), "note": note,
                      "sector": sector, "vol20": round(v, 1), "mom20": round(st["mom20"] * 100, 2),
                      "ma20": round(st["ma20"], 2), "ma60": round(st["ma60"], 2),
                      "vr": round(st["vr"], 2), "breakeven_pct": breakeven(px * 100)})

    picks.sort(key=lambda x: -x["score"])
    picks = picks[:top_n]
    out = {"date": D, "snapshot": os.path.basename(snap_f), "count": len(picks),
           "strong_mode": sec_mode, "strong_sectors": sorted(strong)[:10],
           "index_above_ma20": idx_above, "blocked_strategies": sorted(blocked),
           "price_cap": price_cap, "picks": picks}
    json.dump(out, open(os.path.join(ROOT, "data", f"picks-{D}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print(f"PICKS {D} 候选 {len(picks)} 只｜板块:{sec_mode}｜{'MA20上方' if idx_above else 'MA20下方 禁用' + ','.join(sorted(blocked))}｜1手上限 {price_cap} 元")
    for i, p in enumerate(picks, 1):
        print(f"  {i}. {p['name']} {p['symbol'][2:]} {p['price']:.2f} [{p['strategy']}] {p['sector']} 评分{p['score']} 回本+{p['breakeven_pct']}% — {p['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())