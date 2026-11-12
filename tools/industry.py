#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取新浪行业板块行情 → data/industry-YYYY-MM-DD.json（2026-11-10）。"""
import datetime as dt
import json
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"


def main():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0",
                                               "Referer": "https://finance.sina.com.cn/"})
    txt = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
    m = re.search(r"=\s*(\{.*\})", txt, re.S)
    if not m:
        print("industry: parse fail")
        return 2
    raw = json.loads(m.group(1))
    rows = []
    for node, v in raw.items():
        f = v.split(",")
        if len(f) < 12:
            continue
        try:
            rows.append({"node": node, "name": f[1], "count": int(f[2]),
                         "pct": round(float(f[5]), 2),
                         "amount_yi": round(float(f[7]) / 1e8, 1),
                         "leader_code": f[8], "leader_name": f[12] if len(f) > 12 else ""})
        except (ValueError, IndexError):
            continue
    rows.sort(key=lambda x: x["pct"], reverse=True)
    out = {"generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}
    path = os.path.join(DATA, f"industry-{dt.date.today():%Y-%m-%d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    up = sum(1 for r in rows if r["pct"] > 0)
    print(f"industry: {len(rows)} 个板块，涨 {up} 跌 {len(rows)-up} → {os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
