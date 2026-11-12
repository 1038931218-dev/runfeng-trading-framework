#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当日摘要（供收盘/值守会话发通知，10 行内，零 token 计算）。"""
import datetime as dt
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = dt.date.today().isoformat()


def load(p):
    try:
        with open(os.path.join(ROOT, p), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    acct = load("data/account-state.json")
    al_file = os.path.join(ROOT, "data", f"alerts-{D}.jsonl")
    alerts = sum(1 for _ in open(al_file, encoding="utf-8")) if os.path.exists(al_file) else 0
    st = load(f"data/alert-state-{D}.json")
    fills = acct.get("today_fills") or []
    pos = acct.get("positions") or []
    plans = sorted(glob.glob(os.path.join(ROOT, "data", "plan-2026-*.json")))
    nxt = plans[-1] if plans else ""
    nxt_name = os.path.basename(nxt) if nxt else "无"
    lines = [
        f"日期 {D}｜告警 {alerts} 条（已处理 {st.get('processed', 0)}）",
        f"总资产 {acct.get('total_assets')}｜可用 {acct.get('cash')}｜仓位 {acct.get('position_pct')}%",
        f"当日盈亏 {acct.get('day_pnl')}｜累计 {acct.get('total_pnl')}",
    ]
    if fills:
        for f in fills:
            lines.append(f"成交 {f.get('name')} {f.get('side')} {f.get('price')}×{f.get('qty')} @{f.get('time')}")
    else:
        lines.append("成交：无")
    lines.append("持仓 " + "、".join(f"{p.get('name')}{p.get('qty')}股" for p in pos))
    lines.append(f"次日计划 {nxt_name}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
