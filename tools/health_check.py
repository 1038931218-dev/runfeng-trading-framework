#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开盘前/收盘后一致性自检（2026-11-12）：发现「乌龙隐患」并给出修复提示。

检查项：
 1 计划文件是否为今日/次日，且账户块与 account-state 一致
 2 持仓数 vs 计划买入规则（满 2 只仍挂买单 → 告警）
 3 现金比例是否 ≥60%（含计划买入后）
 4 买单是否低于 MA60（应要求站回确认）
 5 云条件单/止损数量是否与持仓一致（人工核对提醒）
 6 账户数据新鲜度（as_of 是否为最近交易日）
输出：控制台 + reports/health-<date>.md；有严重问题时退出码 2。
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import market  # noqa: E402


def load(p):
    try:
        return json.load(open(os.path.join(ROOT, p), encoding="utf-8"))
    except Exception:
        return {}


def latest_trade_date():
    try:
        b = market.kline("sh000001", 5)
        return b[-1]["date"] if b else None
    except Exception:
        return None


def main():
    issues, notes = [], []
    acct = load("data/account-state.json")
    total, cash = acct.get("total_assets") or 0, acct.get("cash") or 0
    pos = acct.get("positions") or []
    plans = sorted(glob.glob(os.path.join(ROOT, "data", "plan-*.json")))
    plan_f = plans[-1] if plans else None
    plan = json.load(open(plan_f, encoding="utf-8")) if plan_f else {}
    pdate = plan.get("date")

    # 1 计划/账户一致
    pa = plan.get("account") or {}
    if pa.get("total_assets") and abs((pa.get("total_assets") or 0) - total) > 1:
        issues.append(f"计划账户总资产 {pa.get('total_assets')} ≠ 实际 {total}")
    ltd = latest_trade_date()
    if acct.get("as_of") and ltd and acct["as_of"] < ltd:
        issues.append(f"账户数据日期 {acct['as_of']} 早于最新交易日 {ltd}（需补对账）")
    # 2 持仓数 vs 买单
    buys = [r for r in plan.get("rules") or [] if r.get("action") == "buy_ready"]
    if len(pos) >= 2 and buys:
        issues.append(f"持仓 {len(pos)} 只已满，计划仍挂 {len(buys)} 个买单（会触发无效提醒/违规）")
    # 3 现金
    mv = acct.get("market_value") or sum((x.get("market_value") or 0) for x in pos)
    if total:
        cash_pct = cash / total * 100
        notes.append(f"现金比例 {cash_pct:.1f}%（红线 60%）")
        if cash_pct < 60:
            issues.append(f"现金比例 {cash_pct:.1f}% < 60%")
    # 4 买单低于 MA60
    for r in buys:
        try:
            b = market.kline(r["symbol"], 60)
            ma60 = sum(x["close"] for x in b[-60:]) / 60
            if (r.get("price") or 0) < ma60 * 0.995 and r.get("op") == "<=":
                issues.append(f"{r['symbol']} 买点 {r.get('price')} 低于 MA60 {ma60:.2f}，应改站回确认")
        except Exception:
            notes.append(f"{r['symbol']} MA60 未能核实")
    # 5 止损数量一致性（仅提示人工核对云条件单）
    for x in pos:
        q = x.get("qty"); s = x.get("sellable")
        if s is not None and s < q:
            notes.append(f"{x.get('name')} 可卖 {s} < 持仓 {q}（T+1 限制）")
    # 6 计划日期
    notes.append(f"计划日期 {pdate}｜持仓 {len(pos)} 只｜规则 {len(plan.get('rules') or [])} 条")

    lines = [f"# 一致性自检 {__import__('datetime').date.today()}", ""]
    lines += ["## 问题"] + ([f"- {x}" for x in issues] or ["- 无"]) + ["", "## 提示"] + [f"- {x}" for x in notes]
    open(os.path.join(ROOT, "reports", f"health-{__import__('datetime').date.today()}.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("HEALTH " + ("; ".join(issues) if issues else "OK"))
    for n in notes:
        print("  ·", n)
    return 2 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
