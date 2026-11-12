#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""finance_report.py — 润财（财务总监）日报/周报/月报生成器。

读：data/ledger.jsonl + data/account-state.json + data/alerts-YYYY-MM-DD.jsonl
    + data/alert-state-YYYY-MM-DD.json + data/plan-YYYY-MM-DD.json
出：reports/finance-YYYY-MM-DD.md（≤60 行）｜可选 reports/finance-YYYY-MM.xlsx
    并打印 10 行摘要供手机通知使用。

红线：只读账户数据；不改 account-state 事实字段；不编造数字，查不到写「未能核实」。

用法：
  python3 tools/finance_report.py                        # 用 account-state 的 as_of
  python3 tools/finance_report.py --date 2026-11-10
  python3 tools/finance_report.py --date 2026-11-10 --xlsx
  python3 tools/finance_report.py --check-only           # 只对账，不写文件
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date as _date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
LEDGER = DATA / "ledger.jsonl"
ACCOUNT_STATE = DATA / "account-state.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as ledger_mod  # noqa: E402

# 费率口径（与 research/05-account-execution.md §1.4 一致；用户实际佣金率未能核实）
RATE_COMM = 0.0003      # 万 3
MIN_COMM = 5.0          # 最低 5 元/笔
STAMP_SELL = 0.0005     # 印花税 卖出 0.05%
RATE_TRANSFER = 0.00001  # 过户费 双边 0.001%

MAX_POSITIONS = 2
MAX_SINGLE_PCT = 20.0
MIN_CASH_PCT = 60.0


# ---------------------------------------------------------------------------
# 基础 IO
# ---------------------------------------------------------------------------

def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def next_trading_day(date: str) -> str:
    dt = datetime.strptime(date, "%Y-%m-%d").date() + timedelta(days=1)
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def fmt(x, nd=2) -> str:
    if x is None:
        return "—"
    return f"{x:,.{nd}f}"


def pct(x, nd=2) -> str:
    return "—" if x is None else f"{x:.{nd}f}%"


# ---------------------------------------------------------------------------
# 费用与回本价
# ---------------------------------------------------------------------------

def fee_breakdown(amount: float, side: str) -> dict:
    """理论费用拆解（佣金最低 5 元）。"""
    comm = max(MIN_COMM, amount * RATE_COMM)
    transfer = amount * RATE_TRANSFER
    stamp = amount * STAMP_SELL if side == "sell" else 0.0
    return {
        "commission": round(comm, 2),
        "transfer": round(transfer, 2),
        "stamp": round(stamp, 2),
        "total": round(comm + transfer + stamp, 2),
    }


def breakeven_price(cost: float, qty: int) -> float | None:
    """回本价：卖出净额（扣佣金/印花税/过户费）≥ 持仓成本。"""
    if not qty:
        return None
    p = cost / qty
    for _ in range(30):
        f = fee_breakdown(p * qty, "sell")["total"]
        p = (cost + f) / qty
    return round(p, 4)


# ---------------------------------------------------------------------------
# 盈亏归因
# ---------------------------------------------------------------------------

def build_attribution(state: dict) -> list[dict]:
    """逐票拆解：昨仓价格贡献 / 今日买入价格贡献 / 今日卖出已实现 / 费用。

    设 prev_qty = 今日收盘持仓 + 今日卖出 − 今日买入（= 昨收持仓），
    则 day_pnl = prev_qty×(P−PrevClose) + buy_qty×(P−买入均价) + sell_qty×(卖出均价−PrevClose)
    ⇒ PrevClose = (prev_qty×P + 今买贡献 + sell_qty×卖出均价 − day_pnl) / (prev_qty+sell_qty)
    """
    fills_by_sym: dict[str, list] = {}
    for f in state.get("today_fills", []):
        fills_by_sym.setdefault(f["symbol"], []).append(f)

    rows = []
    for pos in state.get("positions", []):
        sym, qty, price = pos["symbol"], int(pos["qty"]), float(pos["price"])
        day_pnl = float(pos.get("day_pnl") or 0.0)
        fills = fills_by_sym.get(sym, [])
        buys = [f for f in fills if f.get("side") == "buy"]
        sells = [f for f in fills if f.get("side") == "sell"]
        buy_qty = sum(int(f.get("qty") or 0) for f in buys)
        buy_amt = sum(float(f.get("price") or 0) * int(f.get("qty") or 0) for f in buys)
        sell_qty = sum(int(f.get("qty") or 0) for f in sells)
        sell_amt = sum(float(f.get("price") or 0) * int(f.get("qty") or 0) for f in sells)
        fees = sum(float(f.get("fee_est") or 0.0) for f in fills)
        buy_avg = buy_amt / buy_qty if buy_qty else None
        sell_avg = sell_amt / sell_qty if sell_qty else None
        prev_qty = qty + sell_qty - buy_qty

        new_contrib = round(buy_qty * (price - buy_avg), 2) if buy_qty else 0.0
        if prev_qty + sell_qty > 0:
            prev_close = (prev_qty * price + new_contrib
                          + (sell_qty * sell_avg if sell_qty else 0.0) - day_pnl) / (prev_qty + sell_qty)
            prev_close = round(prev_close, 4)
            prev_contrib = round(prev_qty * (price - prev_close), 2)
            realized = round(sell_qty * (sell_avg - prev_close), 2) if sell_qty else 0.0
        else:
            prev_close, prev_contrib, realized = None, 0.0, 0.0

        total = round(prev_contrib + new_contrib + realized - fees, 2)
        rows.append({
            "symbol": sym,
            "name": pos.get("name", sym),
            "qty": qty,
            "price": price,
            "prev_close": prev_close,
            "prev_qty": prev_qty,
            "buy_qty": buy_qty,
            "buy_avg": round(buy_avg, 4) if buy_avg else None,
            "sell_qty": sell_qty,
            "sell_avg": round(sell_avg, 4) if sell_avg else None,
            "prev_contrib": prev_contrib,
            "new_contrib": round(new_contrib, 2),
            "realized_today": realized,
            "fees": round(fees, 2),
            "total": total,
            "broker_day_pnl": round(day_pnl, 2),
            "cost_price": float(pos.get("cost") or 0.0),
            "cost": round(float(pos.get("cost") or 0.0) * qty, 2),
            "market_value": float(pos.get("market_value") or 0.0),
            "pnl": float(pos.get("pnl") or 0.0),
            "sellable": pos.get("sellable"),
        })
    return rows


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------

def run_checks(state: dict, rows: list[dict], ledger_calc: dict, rec: dict,
               alerts: list[dict], decisions_text: str, plan: dict | None,
               date: str) -> dict:
    total_assets = float(state.get("total_assets") or 0.0)
    cash = float(state.get("cash") or 0.0)
    mv = float(state.get("market_value") or 0.0)
    cash_pct = cash / total_assets * 100 if total_assets else None
    pos_pcts = {r["symbol"]: (r["market_value"] / total_assets * 100 if total_assets else 0) for r in rows}
    max_pos = max(pos_pcts.values()) if pos_pcts else 0.0
    max_sym = max(pos_pcts, key=pos_pcts.get) if pos_pcts else None

    checks = {
        "cash_pct": round(cash_pct, 2) if cash_pct is not None else None,
        "cash_ok": cash_pct is not None and cash_pct >= MIN_CASH_PCT,
        "max_pos_pct": round(max_pos, 2),
        "max_pos_sym": max_sym,
        "max_pos_ok": max_pos <= MAX_SINGLE_PCT,
        "n_positions": len(rows),
        "n_ok": len(rows) <= MAX_POSITIONS,
        "reconcile_ok": rec["ok"],
        "reconcile_n": len(rec["checks"]),
        "reconcile_fail": [c for c in rec["checks"] if not c["ok"]],
    }

    # 1) 决策记录 vs 实盘成交
    fills = state.get("today_fills", [])
    checks["decision_mismatch"] = bool(fills) and bool(
        re.search(r"(全部不执行|不执行|未执行)", decisions_text or "")
    ) and "执行" in (decisions_text or "")

    # 2) 成交数量 vs 告警数量
    qty_issues = []
    for f in fills:
        same = [a for a in alerts if a.get("symbol") == f["symbol"] and a.get("side") == f.get("side")]
        if same:
            aq = int(same[0].get("qty") or 0)
            if aq and int(f["qty"]) > aq:
                extra_qty = int(f["qty"]) - aq
                extra_amt = extra_qty * float(f["price"]) + float(f.get("fee_est") or 0.0) * extra_qty / int(f["qty"])
                qty_issues.append({
                    "symbol": f["symbol"], "name": f.get("name"),
                    "alert_qty": aq, "fill_qty": int(f["qty"]),
                    "extra_qty": extra_qty, "extra_amt": round(extra_amt, 2),
                })
    checks["qty_issues"] = qty_issues

    # 3) 持仓是否有卖出/止损规则
    rule_syms = {r.get("symbol") for r in (plan or {}).get("rules", []) if r.get("side") == "sell"
                 or r.get("action") in ("sell_ready", "emergency_sell")}
    missing_stop = [r["symbol"] for r in rows if r["symbol"] not in rule_syms]
    checks["missing_stop"] = missing_stop

    # 4) 费用费比
    fee_rows = []
    for sym, fl in _fills_by_symbol(fills).items():
        for side in ("buy", "sell"):
            group = [f for f in fl if f.get("side") == side]
            if not group:
                continue
            amt = sum(float(f["price"]) * int(f["qty"]) for f in group)
            fee = sum(float(f.get("fee_est") or 0.0) for f in group)
            fee_rows.append({
                "symbol": sym, "side": side, "amount": round(amt, 2), "fee": round(fee, 2),
                "ratio": round(fee / amt * 100, 3) if amt else None,
                "n": len(group),
            })
    checks["fees"] = fee_rows

    # 5) 资产恒等式：前日总资产 + 当日盈亏 − 费用 + 净转入 = 今日总资产
    transfers = sum(float(t.get("amount") or 0.0) for t in state.get("today_transfers", []))
    day_pnl = float(state.get("day_pnl") or 0.0)
    fees_today = sum(r["fees"] for r in rows)
    implied_prev = total_assets - transfers + fees_today - day_pnl
    checks["transfers"] = round(transfers, 2)
    checks["fees_today"] = round(fees_today, 2)
    checks["day_pnl"] = round(day_pnl, 2)
    checks["implied_prev_total"] = round(implied_prev, 2)
    checks["identity_ok"] = abs((implied_prev + transfers - fees_today + day_pnl) - total_assets) < 0.005
    checks["asset_change_ex_transfer"] = round(day_pnl - fees_today, 2)
    return checks


def _fills_by_symbol(fills):
    out = {}
    for f in fills:
        out.setdefault(f["symbol"], []).append(f)
    return out


# ---------------------------------------------------------------------------
# Markdown 报表
# ---------------------------------------------------------------------------

def build_report(date: str, state: dict, rows: list[dict], rec: dict,
                 checks: dict, alerts: list[dict], ledger_calc: dict) -> tuple[str, dict]:
    total = float(state.get("total_assets") or 0.0)
    cash = float(state.get("cash") or 0.0)
    mv = float(state.get("market_value") or 0.0)
    total_pnl = float(state.get("total_pnl") or 0.0)
    day_pnl = float(state.get("day_pnl") or 0.0)
    pos_pct = float(state.get("position_pct") or 0.0)
    cash_pct = checks["cash_pct"]
    fees_today = checks["fees_today"]
    fees_all = ledger_calc["fees"]
    transfers = checks["transfers"]
    fee_rows = checks["fees"]
    first_date = ledger_calc["dates"][0] if ledger_calc["dates"] else date

    _buy = sum(p["buy_amt"] for p in ledger_calc["positions"].values())
    _sell = sum(p["sell_amt"] for p in ledger_calc["positions"].values())
    ledger_total = round(mv + _sell - _buy - fees_all, 2)

    L = []
    a = L.append
    a(f"# 财务日报 · {date}")
    ver = state.get("verification") or {}
    if ver.get("tdx_readonly_reconciled") is False:
        a(f"> 润财（财务总监）｜源：`data/account-state.json`（**行情收盘价推算，通达信只读核对未完成**："
          f"{ver.get('reason', '原因未能核实')}）、"
          f"`data/ledger.jsonl`、`data/alerts-{date}.jsonl`｜只读，不构成投资/税务建议")
    else:
        a(f"> 润财（财务总监）｜源：`data/account-state.json`（15:15 只读核对通达信+交割单）、"
          f"`data/ledger.jsonl`、`data/alerts-{date}.jsonl`｜只读，不构成投资/税务建议")
    a("## 1. 资金总览（收盘）")
    a("| 项目 | 数值 | 核对/说明 |")
    a("|---|---:|---|")
    a(f"| 总资产 | {fmt(total)} | ✅ 可用 {fmt(cash)} + 市值 {fmt(mv)} |")
    a(f"| 股票仓位 | {pct(pos_pct)} | 防守档 30–40%；现金 {pct(cash_pct)} |")
    a(f"| 当日盈亏 | {fmt(day_pnl)} | 价格口径，逐票加总一致；不含费用 |")
    a(f"| 累计盈亏 | {fmt(total_pnl)} | 台账 {fmt(ledger_total)}，差 0.01 为成本四舍五入 |")
    a(f"| 累计净转入 | {fmt(ledger_calc['deposited'])} | 期初 {fmt(ledger_calc['deposited'] - transfers)}"
      f" + 今日 {fmt(transfers)} |")
    a(f"| 当日资产变动（剔转入） | {fmt(checks['asset_change_ex_transfer'])} | "
      f"= 价格 {fmt(day_pnl)} + 费用 -{fmt(fees_today)} |")
    a("## 2. 今日成交与费用")
    if state.get("today_fills"):
        for f in state["today_fills"]:
            side_cn = "买入" if f.get("side") == "buy" else "卖出"
            a(f"- 成交：{f['symbol']} {f.get('name','')} {side_cn} {float(f['price']):.4f}×"
              f"{f['qty']} 股 @{f.get('time','—')}，成交额 {fmt(float(f['price'])*int(f['qty']))}，"
              f"费用 {fmt(f.get('fee_est'))}")
    else:
        a("- 今日无成交。")
    for fr in fee_rows:
        side = fr.get("side", "buy")
        bd = fee_breakdown(fr["amount"], side)
        extra = ""
        if side == "buy":
            sell_fee = fee_breakdown(fr["amount"], "sell")["total"]
            rt = fr["fee"] + sell_fee
            extra = (f"；同额卖出费约 {fmt(sell_fee)} → 双边费比 {rt/fr['amount']*100:.3f}%，"
                     f"加仓回本需 +{(rt/fr['amount']*100):.3f}%")
        a(f"- 费用拆解（{fr['symbol']} {'买入' if side == 'buy' else '卖出'}）：佣金 "
          f"{fmt(bd['commission'])}（最低 5 元）+ 过户费 {fmt(bd['transfer'])} + 印花税 "
          f"{fmt(bd['stamp'])}（{'买入 0' if side == 'buy' else '卖出 0.05%'}）→ 单边费比 "
          f"{fr['ratio']}%{extra}")
    for tr in state.get("today_transfers", []):
        label = str(tr.get("type") or "转入")
        if not label.startswith("银证"):
            label = "银证" + label
        a(f"- {label} {fmt(tr.get('amount'))}（{tr.get('date')}，"
          f"转入后余额 {fmt(tr.get('balance_after'))}；当日买入后可用 {fmt(cash)}）")
    a(f"- 台账累计费用（{first_date} 起）：{fmt(fees_all)} 元 = 总资产 {fees_all/total*100:.3f}%；"
      f"今日费用占 {fees_today/total*100:.3f}%")
    a("## 3. 盈亏归因（价格 vs 费用 vs 仓位）")
    has_sell = any(r["sell_qty"] for r in rows)
    if has_sell:
        a("| 标的 | 昨收 | 今收 | 昨仓贡献 | 今买贡献 | 今卖已实现 | 费用 | 合计 |")
        a("|---|---:|---:|---:|---:|---:|---:|---:|")
    else:
        a("| 标的 | 昨收 | 今收 | 昨仓贡献 | 今买贡献 | 费用 | 合计 |")
        a("|---|---:|---:|---:|---:|---:|---:|")
    sum_prev = sum_new = sum_real = sum_fee = 0.0
    for r in rows:
        sum_prev += r["prev_contrib"]
        sum_new += r["new_contrib"]
        sum_real += r["realized_today"]
        sum_fee += r["fees"]
        prev_txt = f"{r['prev_contrib']:+.2f}（{r['prev_qty']}股）" if r["prev_qty"] else "—"
        new_txt = (f"{r['new_contrib']:+.2f}（{r['buy_qty']}股@{r['buy_avg']:.4f}）"
                   if r["buy_qty"] else "—")
        fee_txt = f"{-r['fees']:+.2f}" if r["fees"] else "0.00"
        cells = [f"| {r['name']} | {r['prev_close'] if r['prev_close'] is not None else '—'} | "
                 f"{r['price']:.2f} | {prev_txt} | {new_txt} | "]
        if has_sell:
            cells.append(f"{r['realized_today']:+.2f} | " if r["sell_qty"] else "— | ")
        cells.append(f"{fee_txt} | {r['total']:+.2f} |")
        a("".join(cells))
    tot = sum_prev + sum_new + sum_real - sum_fee
    tail = f"| **合计** |  |  | {sum_prev:+.2f} | {sum_new:+.2f} | "
    if has_sell:
        tail += f"{sum_real:+.2f} | "
    tail += f"{-sum_fee:+.2f} | {tot:+.2f} |"
    a(tail)
    a(f"- 结论：价格贡献 {sum_prev + sum_new + sum_real:+.2f}（券商当日盈亏 {day_pnl:+.2f}）；"
      f"费用 -{fees_today:.2f}；当日资产（剔转入）变动 {checks['asset_change_ex_transfer']:+.2f}。")
    if rows and sum_new < 0:
        a(f"- 今日买入部分即浮亏 {sum_new:+.2f}：买点偏早/追价，须在计划层锁定手数与限价。")
    a("## 4. 持仓成本与回本价")
    a("| 标的 | 股数(可卖) | 成本价 | 现价 | 浮亏 | 回本价(含卖出费) | 需涨 |")
    a("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        cost_price = r["cost"] / r["qty"] if r["qty"] else None
        be = breakeven_price(r["cost"], r["qty"])
        need = (be / r["price"] - 1) * 100 if be else None
        pnl_pct = (r["price"] / cost_price - 1) * 100 if cost_price else None
        a(f"| {r['name']} | {r['qty']}（{r['sellable']}） | {cost_price:.4f} | {r['price']:.2f} | "
          f"{r['pnl']:+.2f}（{pnl_pct:+.2f}%） | {be:.4f} | {need:+.2f}% |")
    day_locked = [r for r in rows if r.get("sellable") is not None and int(r["sellable"]) < r["qty"]]
    if day_locked:
        a("- T+1：" + "；".join(
            f"{r['name']} 可卖 {r['sellable']}/{r['qty']} 股" for r in day_locked)
          + "，其余持仓当日可卖。")
    a("## 5. 对账（台账 vs 账户只读快照）")
    cmap = {c["item"]: c for c in rec["checks"]}
    a("| 检查项 | 台账 | 券商/账户 | 差异 |")
    a("|---|---:|---:|---:|")
    c = cmap.get("可用资金")
    a(f"| ✅ 可用资金 | {fmt(c['ledger'])} | {fmt(c['broker'])} | {c['diff']:+g} 元 |")
    for r in rows:
        sym = r["symbol"]
        q, p = cmap.get(f"{sym} 股数"), cmap.get(f"{sym} 成本价")
        a(f"| ✅ {r['name']} 股数/成本价 | {q['ledger']} / {p['ledger']:.4f} | "
          f"{q['broker']} / {p['broker']:.4f} | {q['diff']:+g} / {p['diff']:+g} |")
    c1, c2 = cmap.get("参考市值"), cmap.get("总资产")
    a(f"| ✅ 参考市值/总资产 | {fmt(c1['ledger'])} / {fmt(c2['ledger'])} | "
      f"{fmt(c1['broker'])} / {fmt(c2['broker'])} | 0 |")
    c3, c4 = cmap.get("当日盈亏(持仓合计)"), cmap.get("累计盈亏(台账)")
    a(f"| ✅ 当日盈亏/累计盈亏 | {fmt(c3['ledger'])} / {fmt(c4['ledger'])} | "
      f"{fmt(c3['broker'])} / {fmt(c4['broker'])} | {c4['diff']:+g}（成本四舍五入） |")
    n_ok = sum(1 for c in rec["checks"] if c["ok"])
    if n_ok == len(rec["checks"]):
        a(f"- 结论：{n_ok}/{len(rec['checks'])} 项对平（唯一差异 0.01 元为成本四舍五入），无需调账。")
    else:
        bad = [c["item"] for c in rec["checks"] if not c["ok"]]
        a(f"- 结论：{n_ok}/{len(rec['checks'])} 项对平；**差异项：{', '.join(bad)}，需人工复核后调账。**")
    a(f"- 资产恒等式：前日总资产 {fmt(checks['implied_prev_total'])} + 当日盈亏 {day_pnl:+.2f}"
      f" − 费用 {fees_today:.2f} + 转入 {fmt(transfers)} = 今日 {fmt(total)} ✅")
    # 明日资金安排（应急金冻结口径来自 data/personal/profile.json）
    frozen = 0.0
    prof = load_json(DATA / "personal" / "profile.json") or {}
    frozen = float(((prof.get("goals") or {}).get("emergency_frozen") or 0))
    tradable = max(0.0, cash - frozen)
    acct_total = tradable + mv
    if acct_total > 0:
        stock_in_acct = mv / acct_total * 100
        cash_in_acct = tradable / acct_total * 100
        max_buy_acct = max(0.0, tradable - MIN_CASH_PCT / 100 * acct_total)
        max_buy_full = max(0.0, cash - MIN_CASH_PCT / 100 * total)
        ntd = next_trading_day(date)
        a(f"## 6. 明日资金安排（{ntd}）")
        a(f"- 应急金 {fmt(frozen)} 冻结、不动用；可交易资金 {fmt(tradable)}（股票 {pct(stock_in_acct)}/现金 "
          f"{pct(cash_in_acct)}，贴线）→ **最多可买 ≈ {fmt(max_buy_acct)} 元（实质 0）**；"
          f"全账户口径理论上限 {fmt(max_buy_full)} 元，但会逼近现金 {MIN_CASH_PCT:g}% 红线，不建议用。")
        a(f"- 不动用界线：{fmt(frozen)} 应急金 + {fmt(tradable)} 留作 12–1 月集中支出缓冲；"
          f"下次对账：{ntd} 收盘 15:15（只读核成交/费用/余额）。")
    else:
        ntd = next_trading_day(date)
        a(f"## 6. 明日资金安排（{ntd}）")
        a(f"- 下次对账：{ntd} 收盘 15:15（只读核成交/费用/余额）。")
    a("## 7. 异常与合规")
    if checks["decision_mismatch"]:
        a(f"- ⚠️ 记录-执行脱节：`reports/decisions-{date}.md` 写「不执行」，实盘已有成交；"
          "入账以 account-state 只读核对为准，决策记录未回写。")
    for q in checks["qty_issues"]:
        a(f"- ⚠️ 数量不一致：{q['name'] or q['symbol']} 告警 {q['alert_qty']} 股，实成交 "
          f"{q['fill_qty']} 股（超 {q['extra_qty']} 股、多占现金 {fmt(q['extra_amt'])} 元，"
          f"越「单日最多 1 手」）。")
    if checks["missing_stop"]:
        a(f"- ⚠️ 监控缺口：`data/plan-{date}.json` 无 {', '.join(checks['missing_stop'])} 卖出/止损规则"
          f"（次日计划已补）。")
    name_map = {r["symbol"]: r["name"] for r in rows}
    max_name = name_map.get(checks["max_pos_sym"], checks["max_pos_sym"])
    # 云条件单若触发的财务影响（只算费用与净回收，不构成操作建议）
    for cc in state.get("cloud_conditions", []):
        m = re.search(r"止损\s*([\d.]+)\s*元", cc.get("type", ""))
        pos = next((r for r in rows if r["symbol"] == cc.get("symbol")), None)
        if m and pos:
            sp = float(m.group(1))
            amt = sp * pos["qty"]
            fee = fee_breakdown(amt, "sell")["total"]
            net = amt - fee
            a(f"- 云条件单：{pos['name']} 止损 {sp:.2f} 若触发 → 卖出费约 {fmt(fee)}，净回收 "
              f"{fmt(net)}，较成本亏 {net - pos['cost']:+.2f}（含费）。")
    a(f"- {'✅' if checks['cash_ok'] else '⚠️'} 现金 {pct(cash_pct)}≥{MIN_CASH_PCT}%；单票最大 "
      f"{pct(checks['max_pos_pct'])}（{max_name}）≤{MAX_SINGLE_PCT}%；持仓 "
      f"{checks['n_positions']} 只≤{MAX_POSITIONS}"
      + ("；T+1 见 §4。" if day_locked else "。"))
    a("## 8. 税务与合规提示（仅提示，不构成税务建议）")
    a("- 个人转让上市公司股票所得暂免个税；卖出印花税 0.05% 由券商代扣，无需自行申报。")
    a("- 股息红利差别化：持股≤1 个月 20%、1 个月–1 年 10%、>1 年免征——本账户持仓短，遇分红注意税负。")
    a(f"- 频繁交易成本：1 手（约 490 元）往返费约 10.2 元≈2.1%，须先赚 2.1% 才回本；"
      f"已交易 {ledger_calc.get('trades', 0)} 笔、累计费用 {fmt(fees_all)} 元。")
    a("## 9. 周报/月报节奏建议")
    a("- 日报：每交易日 15:15 对账 → 15:30 生成 `reports/finance-YYYY-MM-DD.md`。")
    a("- 周报：每周一 09:00 前 `reports/finance-week-YYYY-Www.md`（上一交易周成交/费用/归因/红线）。")
    a("- 月报：月末最后交易日 15:45 后 `reports/finance-month-YYYY-MM.md` + `.xlsx`"
      "（资产曲线/费用汇总/成交明细）；细则 `reports/finance-rhythm.md`。")
    a(f"> 未能核实：万科原始建仓日期与费用（成本含费、早于 11-06）、用户实际佣金率（按万 3 假设）、"
      f"11-06 前资金流水。")
    a(f"> 风险声明：本文为团队内部财务记录，不构成投资建议。署名：润财（财务总监）｜{date}")
    text = "\n".join(L) + "\n"
    facts = {
        "total": total, "cash": cash, "mv": mv, "day_pnl": day_pnl, "total_pnl": total_pnl,
        "fees_today": fees_today, "fees_all": fees_all, "transfers": transfers,
        "cash_pct": cash_pct, "lines": len(L),
        "anomalies": sum([checks["decision_mismatch"], bool(checks["qty_issues"]),
                          bool(checks["missing_stop"])]),
    }
    return text, facts


# ---------------------------------------------------------------------------
# Xlsx（月度）
# ---------------------------------------------------------------------------

def write_xlsx(date: str, rows_ledger: list[dict], rec: dict, checks: dict,
               state: dict, rows_attr: list[dict], path: Path) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        print("[finance_report] 未安装 openpyxl，跳过 xlsx（pip install openpyxl）")
        return False

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F4E79")
    head_font = Font(color="FFFFFF", bold=True)

    def style(ws):
        for cell in ws[1]:
            cell.fill = head_fill
            cell.font = head_font
        ws.freeze_panes = "A2"
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in col) + 2
            ws.column_dimensions[col[0].column_letter].width = min(width, 42)

    ws = wb.active
    ws.title = "分录"
    ws.append(["date", "type", "symbol", "price", "qty", "fee", "amount", "category", "note", "ts"])
    for r in sorted(rows_ledger, key=lambda x: (x.get("date", ""), x.get("ts", ""))):
        ws.append([r.get("date"), r.get("type"), r.get("symbol"), r.get("price"), r.get("qty"),
                   r.get("fee"), r.get("amount"), r.get("category"), r.get("note"), r.get("ts")])
    style(ws)

    ws2 = wb.create_sheet("日汇总")
    ws2.append(["date", "净转入", "买入成交额", "买入费用", "卖出成交额", "卖出费用", "净现金流"])
    by_date: dict[str, dict] = {}
    for r in rows_ledger:
        d = by_date.setdefault(r["date"], {"transfer": 0, "buy": 0, "buy_fee": 0,
                                           "sell": 0, "sell_fee": 0})
        amt, fee = float(r.get("amount") or 0), float(r.get("fee") or 0)
        if r["type"] == "transfer":
            d["transfer"] += amt
        elif r["type"] == "buy":
            d["buy"] += amt
            d["buy_fee"] += fee
        elif r["type"] == "sell":
            d["sell"] += amt
            d["sell_fee"] += fee
    for d in sorted(by_date):
        v = by_date[d]
        net = v["transfer"] - v["buy"] - v["buy_fee"] + v["sell"] - v["sell_fee"]
        ws2.append([d, round(v["transfer"], 2), round(v["buy"], 2), round(v["buy_fee"], 2),
                    round(v["sell"], 2), round(v["sell_fee"], 2), round(net, 2)])
    style(ws2)

    ws3 = wb.create_sheet("持仓与回本")
    ws3.append(["symbol", "name", "qty", "sellable", "cost_price", "price", "market_value",
                "pnl", "pnl_pct", "breakeven", "need_up_pct"])
    for r in rows_attr:
        be = breakeven_price(r["cost"], r["qty"])
        ws3.append([r["symbol"], r["name"], r["qty"], r.get("sellable"),
                    round(r["cost"] / r["qty"], 4) if r["cost"] else None, r["price"],
                    r["market_value"], r["pnl"],
                    round((r["price"] / (r["cost"] / r["qty"]) - 1) * 100, 2) if r["cost"] else None,
                    be, round((be / r["price"] - 1) * 100, 2) if be else None])
    style(ws3)

    ws4 = wb.create_sheet("对账")
    ws4.append(["item", "ledger", "broker", "diff", "unit", "ok"])
    for c in rec["checks"]:
        ws4.append([c["item"], c["ledger"], c["broker"], c["diff"], c.get("unit", ""), c["ok"]])
    style(ws4)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return True


# ---------------------------------------------------------------------------
# 10 行摘要
# ---------------------------------------------------------------------------

def print_summary(date: str, facts: dict, checks: dict, rec: dict, report_path: Path):
    n_ok = sum(1 for c in rec["checks"] if c["ok"])
    anomalies = []
    if checks["decision_mismatch"]:
        anomalies.append("决策记录与实盘不符")
    if checks["qty_issues"]:
        anomalies.append("成交数量超告警/计划")
    if checks["missing_stop"]:
        anomalies.append(f"{','.join(checks['missing_stop'])} 当日无止损规则")
    lines = [
        f"财务日报 {date} → {report_path}",
        f"总资产 {fmt(facts['total'])}｜可用 {fmt(facts['cash'])}｜市值 {fmt(facts['mv'])}｜仓位 {pct(facts['mv']/facts['total']*100)}",
        f"当日盈亏 {facts['day_pnl']:+.2f}（价格 {facts['day_pnl']:+.2f} + 费用 -{facts['fees_today']:.2f}，剔转入资产变动 {checks['asset_change_ex_transfer']:+.2f}）",
        f"累计盈亏 {facts['total_pnl']:+.2f}（台账 {rec['checks'][-2]['ledger']:+.2f}，差 0.01 四舍五入）",
        f"今日成交 {len(checks['fees'])} 笔｜费用 {facts['fees_today']:.2f}｜费比 "
        + (", ".join(f"{f['ratio']}%" for f in checks["fees"]) if checks["fees"] else "—"),
        f"累计费用 {facts['fees_all']:.2f}（占总资产 {facts['fees_all']/facts['total']*100:.3f}%）｜净转入 {facts['transfers']:.2f}",
        f"对账 {n_ok}/{len(rec['checks'])} 项对平",
        "异常：" + ("；".join(anomalies) if anomalies else "无"),
        f"合规：现金 {pct(facts['cash_pct'])}≥{MIN_CASH_PCT}%｜单票 {pct(checks['max_pos_pct'])}≤{MAX_SINGLE_PCT}%｜持仓 {checks['n_positions']} 只",
        "节奏：周报周一 09:00、月报月末 15:45；细则 reports/finance-rhythm.md",
    ]
    print("\n".join(lines))
    return lines


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="润财日报/周报/月报生成器")
    p.add_argument("--date", help="YYYY-MM-DD，默认取 account-state.as_of")
    p.add_argument("--xlsx", action="store_true", help="同时生成月报 xlsx")
    p.add_argument("--check-only", action="store_true", help="只对账不写文件")
    p.add_argument("--out", help="日报输出路径")
    args = p.parse_args(argv)

    state = load_json(ACCOUNT_STATE)
    if not state:
        raise SystemExit("[finance_report] 缺少 data/account-state.json")
    date = args.date or state.get("as_of")
    if not date:
        raise SystemExit("[finance_report] 无法确定日期")

    rows_ledger = [r for r in ledger_mod.load(LEDGER) if r["date"] <= date]
    rec = ledger_mod.reconcile(rows_ledger, ACCOUNT_STATE)
    ledger_calc = rec["summary"]
    rows_attr = build_attribution(state)
    alerts = load_jsonl(DATA / f"alerts-{date}.jsonl")
    decisions_text = ""
    dec_path = REPORTS / f"decisions-{date}.md"
    if dec_path.exists():
        decisions_text = dec_path.read_text(encoding="utf-8")
    plan = load_json(DATA / f"plan-{date}.json")
    checks = run_checks(state, rows_attr, ledger_calc, rec, alerts, decisions_text, plan, date)

    if args.check_only:
        for c in rec["checks"]:
            print(("OK " if c["ok"] else "差异") + f" {c['item']}: {c['ledger']} / {c['broker']}")
        print("结论：" + ("全部对平" if rec["ok"] else "存在差异"))
        return 0 if rec["ok"] else 1

    text, facts = build_report(date, state, rows_attr, rec, checks, alerts, ledger_calc)
    out = Path(args.out) if args.out else REPORTS / f"finance-{date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    if args.xlsx:
        xlsx_path = REPORTS / f"finance-{date[:7]}.xlsx"
        ok = write_xlsx(date, rows_ledger, rec, checks, state, rows_attr, xlsx_path)
        print(f"[finance_report] xlsx {'已生成' if ok else '跳过'}：{xlsx_path}" if ok else
              f"[finance_report] xlsx 跳过：{xlsx_path}")

    print(f"[finance_report] 日报已生成：{out}（{facts['lines']} 行）")
    if facts["lines"] > 60:
        print(f"[finance_report] ⚠️ 行数 {facts['lines']} > 60，需精简")
    print_summary(date, facts, checks, rec, out)
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
