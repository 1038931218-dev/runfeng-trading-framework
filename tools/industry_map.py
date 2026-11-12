#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行业归属表：新浪板块成分股 → data/industry-map-<date>.json（2026-11-12）。

用途：选股时只保留「强势板块」里的个股（资金流入方向）。
缓存：当天文件存在则直接读；--refresh 强制重抓（49 个板块 × 1 请求，约 1–2 分钟）。

用法:
  python3 tools/industry_map.py            # 建/读当日映射
  python3 tools/industry_map.py --refresh  # 强制重抓
  python3 tools/industry_map.py --strong   # 打印强势板块（涨幅>0 且成交额前 20）
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
IND_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
NODE_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page=1&num=100&sort=symbol&asc=1&node={node}&symbol=")


def get(url, timeout=15):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def sectors():
    txt = get(IND_URL).decode("gbk", "ignore")
    m = re.search(r"=\s*(\{.*\})", txt, re.S)
    if not m:
        return {}
    return json.loads(m.group(1))


def build():
    secs = sectors()
    out = {}
    meta = []
    for node, v in secs.items():
        f = v.split(",")
        if len(f) < 12:
            continue
        try:
            pct = float(f[5]); amt = float(f[7]) / 1e8
        except ValueError:
            continue
        meta.append({"node": node, "name": f[1], "pct": round(pct, 2), "amount_yi": round(amt, 1)})
    meta.sort(key=lambda x: -x["amount_yi"])
    for s in meta:
        try:
            raw = get(NODE_URL.format(node=s["node"]), timeout=12).decode("utf-8", "ignore")
            for it in json.loads(raw) or []:
                sym = it.get("symbol")
                if sym:
                    out[sym] = s["name"]
        except Exception:
            continue
    return {"date": dt.date.today().isoformat(), "sectors": meta, "symbol_sector": out}


def main():
    path = os.path.join(DATA, f"industry-map-{dt.date.today():%Y-%m-%d}.json")
    if os.path.exists(path) and "--refresh" not in sys.argv:
        d = json.load(open(path, encoding="utf-8"))
    else:
        d = build()
        json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    if "--strong" in sys.argv:
        strong = [s for s in d["sectors"] if s["pct"] > 0][:20]
        print("强势板块（涨幅>0 且成交额前 20）：")
        for s in strong:
            print(f"  {s['name']} {s['pct']:+.2f}% 额{s['amount_yi']}亿")
        return 0
    print(f"INDUSTRY_MAP {d['date']} 板块 {len(d['sectors'])} 个股 {len(d['symbol_sector'])} → {os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())