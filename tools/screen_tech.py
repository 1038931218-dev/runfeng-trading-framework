#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技术面全市场量化筛选（tech-quant 名下脚本，2026-11-06）。

数据源: data/snapshot-2026-11-06.json (Sina 快照) + tools/market.py kline (腾讯前复权日K)。
输出: models/screen-2026-11-06.csv（20-30 只，三组：趋势延续/超跌修复/回避）
      + 指数技术位、跌幅榜前 50 错杀/趋势破坏判别（stdout / /tmp JSON）。

复现命令:
  python3 tools/screen_tech.py                 # 成交额前 800 样本 + 跌幅榜前 50
  python3 tools/screen_tech.py --limit 300     # 网络慢时缩小样本（报告中注明）
  python3 tools/screen_tech.py --no-cache      # 强制重新抓取

口径:
  mom20/mom60  收盘价 20/60 日涨跌幅(%)
  vol20        近 20 个交易日对数收益标准差 * sqrt(252)，年化 %
  dd250        现价 / 近 250 日最高价 - 1（%，负数=回撤；不足 250 日按已有历史）
  vol_ratio    近 5 日均量 / 近 60 日均量
  price/pct    直接取自快照，与 CSV 完全一致（K 线收盘仅用于校验）
"""
import argparse, json, os, sys, time, statistics, csv
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import market  # noqa: E402

import glob as _glob
SNAP = sorted(_glob.glob(os.path.join(ROOT, "data", "snapshot-*.json")))[-1]
import datetime as _dt
OUT_CSV = os.path.join(ROOT, "models", f"screen-{_dt.date.today():%Y-%m-%d}.csv")
CACHE = "/tmp/tech_kline_cache_2026-11-06.json"
TRADE_DATE = "2026-11-06"
MIN_BARS = 120          # 少于 120 根日K（次新）不参与指标与分组
MAX_BARS = 250
DEADLINE_SEC = 420      # 全市场抓取硬上限 7 分钟，留出分析时间


# ---------- 数据抓取 ----------
def load_cache():
    if os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa
            return {}
    return {}


def save_cache(c):
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)


def fetch_one(sym):
    for attempt in range(2):
        try:
            bars = market.kline(sym, MAX_BARS)
            return sym, bars
        except Exception:  # noqa
            time.sleep(0.3 + attempt * 0.7)
    return sym, []


def fetch_many(syms, cache, workers=10):
    todo = [s for s in syms if s not in cache]
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i in range(0, len(todo), 100):
            if time.time() - t0 > DEADLINE_SEC:
                print(f"!! 抓取超时保护：已抓 {done}/{len(todo)}，剩余样本跳过", file=sys.stderr)
                break
            chunk = todo[i:i + 100]
            for sym, bars in ex.map(fetch_one, chunk):
                cache[sym] = bars
                done += 1
            if done % 200 == 0 or done == len(todo):
                print(f"   抓取 {done}/{len(todo)}  elapsed {time.time()-t0:.0f}s", file=sys.stderr)
    save_cache(cache)
    return cache


# ---------- 指标 ----------
def pct(a, b):
    return (a / b - 1.0) * 100.0 if b else 0.0


def metrics(bars):
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    vols = [b["volume"] for b in bars]
    c = closes[-1]
    rets = [statistics.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 20, len(closes))]
    sd = statistics.pstdev(rets) * (252 ** 0.5) * 100
    m = {
        "close": c,
        "mom20": pct(c, closes[-21]),
        "mom60": pct(c, closes[-61]),
        "vol20": sd,
        "dd250": (c / max(highs[-250:]) - 1.0) * 100.0,
        "vol_ratio": (sum(vols[-5:]) / 5.0) / (sum(vols[-60:]) / 60.0) if sum(vols[-60:]) else 0.0,
        "ma20": sum(closes[-20:]) / 20.0,
        "ma60": sum(closes[-60:]) / 60.0,
        "ma250": sum(closes[-250:]) / min(250, len(closes)),
        "low20": min(b["low"] for b in bars[-20:]),
        "high20": max(highs[-20:]),
    }
    return m


def pctl(vals, q):
    s = sorted(vals)
    if not s:
        return 0.0
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


# ---------- 分组规则 ----------
def classify(m, pct_today, universe):
    """返回 label；注意只对通过硬性流动性/历史门槛的样本调用。"""
    p20 = universe["mom20_p80"]
    # ---- 回避：高位破位 / 趋势破坏 / 放量杀跌 ----
    if m["close"] < m["ma60"] and m["close"] < m["ma250"]:
        return "回避"                      # 中期、长期趋势同时破坏
    if m["mom20"] >= p20 and m["dd250"] <= -12 and m["vol_ratio"] >= 1.5:
        return "回避"                      # 20 日涨幅全市场前 20% + 高位放量下跌 = 拥挤出清
    if m["vol_ratio"] >= 2.2 and m["close"] < m["ma20"]:
        return "回避"                      # 放量跌破 20 日线
    if m["mom60"] < -20 and m["close"] < m["ma60"]:
        return "回避"                      # 60 日趋势崩溃
    # ---- 趋势延续：中短趋势向上、未过度拥挤 ----
    if (m["mom20"] > 0 and m["mom60"] > 8 and m["close"] > m["ma20"] > m["ma60"]
            and m["dd250"] >= -18 and 0.6 <= m["vol_ratio"] <= 1.8
            and m["vol20"] <= 55 and pct_today > -5):
        return "趋势延续"
    # ---- 超跌修复：长期趋势未破（站上 MA250）、短期回调、缩量 ----
    if (m["close"] > m["ma250"] and m["mom20"] <= -8 and m["mom60"] >= -15
            and m["dd250"] >= -45 and m["vol_ratio"] <= 1.6
            and m["vol20"] <= 95 and pct_today > -8):
        return "超跌修复"
    return ""


def score_trend(m):
    s = 0
    s += 3 if m["mom20"] > 10 else (2 if m["mom20"] > 0 else 0)
    s += 2 if m["mom60"] > 15 else (1 if m["mom60"] > 0 else 0)
    s += 2 if m["dd250"] >= -10 else (1 if m["dd250"] >= -20 else 0)
    s += 2 if m["close"] > m["ma20"] > m["ma60"] else 0
    s += 1 if 0.6 <= m["vol_ratio"] <= 1.8 else 0
    return s


def score_oversold(m):
    s = 0
    s += 2 if m["mom20"] <= -15 else (1 if m["mom20"] <= -8 else 0)
    s += 2 if m["mom60"] > 5 else (1 if m["mom60"] > -5 else 0)
    s += 2 if m["close"] > m["ma250"] else 0
    s += 1 if m["dd250"] >= -22 else 0
    s += 1 if m["vol_ratio"] <= 1.3 else 0
    s += 1 if m["vol20"] <= 45 else 0
    return s


# ---------- 跌幅榜前 50 判别 ----------
def decline_label(m, pct_today):
    """跌幅榜前 50：错杀 vs 趋势破坏（可执行阈值，见报告第 4 节）。"""
    if pct_today <= -9.7:
        return "趋势破坏", "跌停/接近跌停，今日无法确认承接"
    if m["close"] < m["ma60"] * 0.97:
        return "趋势破坏", "收盘低于 MA60 达 3% 以上"
    if m["dd250"] < -45:
        return "趋势破坏", "距 250 日高点回撤 >45%"
    if m["mom60"] > 120:
        return "趋势破坏", "60 日涨幅 >120%，极端拥挤均值回归"
    if (m["mom60"] > 60 or m["mom20"] > 15) and m["vol_ratio"] > 1.8:
        return "趋势破坏", "前期涨幅大 + 放量下跌（拥挤出清）"
    mis = ((m["close"] > m["ma250"]) + (m["dd250"] > -30) + (m["vol_ratio"] <= 1.8)
           + (pct_today > -9.7) + (m["mom20"] > -15))
    if m["close"] > m["ma60"] and mis >= 4:
        return "错杀", f"错杀分{mis}/5（MA60 上方、回撤可控、非放量）"
    return "趋势破坏", "中期趋势破坏或放量出逃"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=800, help="按成交额取前 N 只（默认 800）")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    snap = json.load(open(SNAP, encoding="utf-8"))
    rows = [r for r in snap["rows"] if r["price"] > 0]
    by_amount = sorted(rows, key=lambda r: -r["amount"])
    sample = by_amount[: args.limit]
    decliners = sorted(rows, key=lambda r: r["pct"])[:50]

    syms = list(dict.fromkeys([r["symbol"] for r in sample] + [r["symbol"] for r in decliners]))
    cache = {} if args.no_cache else load_cache()
    print(f"样本: 成交额前 {len(sample)} 只 + 跌幅榜前 50（并集 {len(syms)} 只），"
          f"缓存命中 {len([s for s in syms if s in cache])}", file=sys.stderr)
    t0 = time.time()
    fetch_many(syms, cache)
    print(f"K 线抓取耗时 {time.time()-t0:.1f}s", file=sys.stderr)

    # 指标计算（仅样本；校验 last bar 日期与快照价格）
    recs, bad = [], {"no_bars": 0, "few_bars": 0, "stale": 0, "price_mismatch": 0}
    for r in sample:
        bars = cache.get(r["symbol"]) or []
        if not bars:
            bad["no_bars"] += 1
            continue
        if len(bars) < MIN_BARS:
            bad["few_bars"] += 1
            continue
        if bars[-1]["date"] != TRADE_DATE:
            bad["stale"] += 1
            continue
        if abs(bars[-1]["close"] / r["price"] - 1) > 0.005:
            bad["price_mismatch"] += 1
            continue
        m = metrics(bars)
        m["symbol"], m["name"] = r["symbol"], r["name"]
        m["price"], m["pct"] = r["price"], r["pct"]
        m["amount_yi"] = round(r["amount"] / 1e8, 2)
        recs.append(m)

    universe = {
        "n": len(recs),
        "mom20_p20": pctl([m["mom20"] for m in recs], 0.20),
        "mom20_p50": pctl([m["mom20"] for m in recs], 0.50),
        "mom20_p80": pctl([m["mom20"] for m in recs], 0.80),
        "mom60_p50": pctl([m["mom60"] for m in recs], 0.50),
        "dd250_p20": pctl([m["dd250"] for m in recs], 0.20),
        "vol20_p50": pctl([m["vol20"] for m in recs], 0.50),
        "vol20_p80": pctl([m["vol20"] for m in recs], 0.80),
        "vol_ratio_p80": pctl([m["vol_ratio"] for m in recs], 0.80),
    }

    for m in recs:
        m["label"] = classify(m, m["pct"], universe)

    # 选 20-30 只：趋势延续 10 / 超跌修复 10 / 回避 6（按分数排序）
    trend = sorted([m for m in recs if m["label"] == "趋势延续"],
                   key=lambda m: (-score_trend(m), -m["amount_yi"]))[:10]
    over = sorted([m for m in recs if m["label"] == "超跌修复" and m["mom60"] <= 60],
                  key=lambda m: (-score_oversold(m), -m["amount_yi"]))[:10]
    avoid = sorted([m for m in recs if m["label"] == "回避"],
                   key=lambda m: (m["dd250"], -m["vol_ratio"], -m["amount_yi"]))[:6]
    picked = trend + over + avoid

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    cols = ["symbol", "name", "price", "pct", "mom20", "mom60", "vol20",
            "dd250", "vol_ratio", "amount_yi", "label"]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for m in picked:
            w.writerow({k: (round(m[k], 4) if isinstance(m[k], float) else m[k]) for k in cols})

    # 跌幅榜前 50 判别
    dec_out = []
    for r in decliners:
        bars = cache.get(r["symbol"]) or []
        if len(bars) < MIN_BARS or bars[-1]["date"] != TRADE_DATE:
            dec_out.append({"symbol": r["symbol"], "name": r["name"], "pct": r["pct"],
                            "label": "数据不足", "reason": "K线不足/停牌", "amount_yi": round(r["amount"]/1e8, 2)})
            continue
        m = metrics(bars)
        m["symbol"], m["name"], m["pct"] = r["symbol"], r["name"], r["pct"]
        m["amount_yi"] = round(r["amount"] / 1e8, 2)
        lab, why = decline_label(m, r["pct"])
        dec_out.append({"symbol": r["symbol"], "name": r["name"], "pct": r["pct"],
                        "price": r["price"], "mom20": round(m["mom20"], 2),
                        "mom60": round(m["mom60"], 2), "dd250": round(m["dd250"], 2),
                        "vol_ratio": round(m["vol_ratio"], 2), "close_vs_ma60": round(100*(m["close"]/m["ma60"]-1), 2),
                        "close_vs_ma250": round(100*(m["close"]/m["ma250"]-1), 2),
                        "label": lab, "reason": why, "amount_yi": m["amount_yi"]})

    summary = {
        "as_of": TRADE_DATE, "sample_requested": args.limit, "sample_ok": len(recs),
        "bad": bad, "universe": universe,
        "groups": {"趋势延续": len([m for m in recs if m["label"] == "趋势延续"]),
                   "超跌修复": len([m for m in recs if m["label"] == "超跌修复"]),
                   "回避": len([m for m in recs if m["label"] == "回避"])},
        "picked": [{k: round(m[k], 2) if isinstance(m[k], float) else m[k] for k in cols} for m in picked],
        "decliners": dec_out,
        "decliner_counts": {k: sum(1 for d in dec_out if d["label"] == k)
                            for k in ["错杀", "趋势破坏", "数据不足"]},
    }
    with open("/tmp/tech_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print(json.dumps({k: summary[k] for k in ["sample_ok", "bad", "universe", "groups", "decliner_counts"]},
                     ensure_ascii=False, indent=1))
    print("\n== 入选 CSV ==")
    for m in picked:
        print(f"{m['label']} {m['symbol']} {m['name']:<8} price={m['price']:>8} pct={m['pct']:>7} "
              f"mom20={m['mom20']:>7.1f} mom60={m['mom60']:>7.1f} vol20={m['vol20']:>5.1f} "
              f"dd250={m['dd250']:>7.1f} vr={m['vol_ratio']:.2f} amt={m['amount_yi']:.1f}")
    print("\n== 跌幅榜前 50 ==")
    for d in dec_out:
        print(f"{d['label']:<5} {d['symbol']} {d['name']:<8} pct={d['pct']:>7.2f} amt={d['amount_yi']:>6.1f} "
              f"mom20={d.get('mom20','-'):>7} mom60={d.get('mom60','-'):>7} dd250={d.get('dd250','-'):>7} "
              f"vr={d.get('vol_ratio','-'):>5} vsMA60={d.get('close_vs_ma60','-'):>7} {d.get('reason','')}")
    print("\nCSV ->", OUT_CSV)


if __name__ == "__main__":
    main()
