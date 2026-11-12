#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger.py — 润财（财务总监）append-only 记账台账。

设计原则
--------
1. **只追加**：`data/ledger.jsonl` 只 append，不修改、不删除历史分录（审计可追溯）。
2. **字段固定**：date, type, symbol, price, qty, fee, amount, note, category
   + ts（写入时间，用于留痕）。type ∈ transfer/buy/sell/fee/dividend/life。
3. **不产生交易**：本工具只记账，不连券商、不下单、不改 account-state 事实字段。

口径（与 research/05-account-execution.md 一致）
------------------------------------------------
- 买入现金流出 = amount + fee（amount = price × qty 成交金额）
- 卖出现金流入 = amount - fee（卖出印花税 0.05% 含在 fee 里）
- 买入成本 = amount + fee，成本价 = 买入成本 / qty（加权平均）
- 转正数=转入，负数=转出；life 为个人生活收支，与交易账户分账（category=life）

用法
----
  python3 tools/ledger.py add --date 2026-11-10 --type buy --symbol sh601006 \
      --price 4.89 --qty 200 --fee 5.01 --note "加仓，成交时间 10:13:29"
  python3 tools/ledger.py add --date 2026-11-10 --type transfer --amount 6857.10 \
      --note "银证转入，余额 10110.52"
  python3 tools/ledger.py list [--date 2026-11-10] [--type buy] [--symbol sh601006]
  python3 tools/ledger.py summary [--as-of 2026-11-10]
  python3 tools/ledger.py check [--account-state data/account-state.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "ledger.jsonl"
ACCOUNT_STATE = ROOT / "data" / "account-state.json"

TYPES = ("transfer", "buy", "sell", "fee", "dividend", "life")
CATEGORIES = ("trade", "life")
FIELDS = ("date", "type", "symbol", "price", "qty", "fee", "amount", "note", "category", "ts")

COMMISSION_RATE = 0.0003   # 万 3（用户实际佣金率未能核实，保守假设）
COMMISSION_MIN = 5.0       # 最低 5 元/笔
STAMP_SELL = 0.0005        # 印花税：卖出单边 0.05%
TRANSFER_FEE = 0.00001     # 过户费：双边 0.001%


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load(path: Path = LEDGER) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"[ledger] 第 {lineno} 行不是合法 JSON：{exc}")
            out.append(row)
    return out


def _to_float(value, field: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise SystemExit(f"[ledger] {field} 不是数字：{value!r}")


def _to_int(value, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise SystemExit(f"[ledger] {field} 不是整数：{value!r}")


def validate(entry: dict) -> dict:
    """校验并补全一条分录。"""
    e = dict(entry)
    for key in FIELDS:
        e.setdefault(key, None)

    date = str(e.get("date") or "").strip()
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        raise SystemExit(f"[ledger] date 必须为 YYYY-MM-DD：{date!r}")
    e["date"] = date

    etype = str(e.get("type") or "").strip()
    if etype not in TYPES:
        raise SystemExit(f"[ledger] type 必须是 {TYPES} 之一：{etype!r}")
    e["type"] = etype

    category = str(e.get("category") or "").strip() or ("life" if etype == "life" else "trade")
    if category not in CATEGORIES:
        raise SystemExit(f"[ledger] category 必须是 {CATEGORIES} 之一：{category!r}")
    if etype == "life" and category == "trade":
        category = "life"
    e["category"] = category

    price = _to_float(e.get("price"), "price")
    qty = _to_int(e.get("qty"), "qty")
    fee = _to_float(e.get("fee"), "fee")
    amount = _to_float(e.get("amount"), "amount")

    if etype in ("buy", "sell"):
        if not e.get("symbol"):
            raise SystemExit(f"[ledger] {etype} 必须给 --symbol")
        if price is None or price <= 0:
            raise SystemExit(f"[ledger] {etype} 必须给正数 --price")
        if qty is None or qty <= 0:
            raise SystemExit(f"[ledger] {etype} 必须给正数 --qty")
        if amount is None:
            amount = round(price * qty, 2)
        if fee is None:
            fee = 0.0
    elif etype == "transfer":
        if amount is None or amount == 0:
            raise SystemExit("[ledger] transfer 必须给非零 --amount（正=转入，负=转出）")
        fee = fee or 0.0
    elif etype in ("fee", "dividend", "life"):
        if amount is None:
            raise SystemExit(f"[ledger] {etype} 必须给 --amount")
        fee = fee or 0.0
        if etype == "life":
            category = "life"

    e["price"] = price
    e["qty"] = qty
    e["fee"] = round(fee, 2) if fee is not None else 0.0
    e["amount"] = round(amount, 2) if amount is not None else None
    e["symbol"] = (str(e["symbol"]).strip() or None) if e.get("symbol") else None
    e["note"] = str(e.get("note") or "").strip()
    e["ts"] = e.get("ts") or _now()
    return e


def append(entry: dict, path: Path = LEDGER) -> dict:
    e = validate(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(OrderedDict((k, e[k]) for k in FIELDS), ensure_ascii=False) + "\n")
    return e


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------

def calc(rows: list[dict]) -> dict:
    """按账面口径汇总。返回现金、各票持仓、费用、投入等。"""
    cash = 0.0
    fees = 0.0
    deposited = 0.0     # 净转入（trade）
    life_in = 0.0
    life_out = 0.0
    dividends = 0.0
    positions: dict[str, dict] = {}
    realized = 0.0

    for r in sorted(rows, key=lambda x: (x.get("date") or "", x.get("ts") or "")):
        t = r["type"]
        amount = float(r.get("amount") or 0.0)
        fee = float(r.get("fee") or 0.0)
        fees += fee

        if t == "transfer":
            cash += amount
            deposited += amount
        elif t == "buy":
            cash -= amount + fee
            sym = r["symbol"]
            pos = positions.setdefault(sym, {"qty": 0, "cost": 0.0, "buy_amt": 0.0, "sell_amt": 0.0})
            pos["qty"] += int(r["qty"])
            pos["cost"] += amount + fee
            pos["buy_amt"] += amount
        elif t == "sell":
            cash += amount - fee
            sym = r["symbol"]
            pos = positions.setdefault(sym, {"qty": 0, "cost": 0.0, "buy_amt": 0.0, "sell_amt": 0.0})
            if pos["qty"] > 0:
                avg = pos["cost"] / pos["qty"]
                sold_cost = avg * int(r["qty"])
                realized += (amount - fee) - sold_cost
                pos["cost"] -= sold_cost
                pos["qty"] -= int(r["qty"])
                if pos["qty"] == 0:
                    pos["cost"] = 0.0
            pos["sell_amt"] += amount
        elif t == "fee":
            cash -= amount
        elif t == "dividend":
            cash += amount
            dividends += amount
        elif t == "life":
            cash -= amount
            if amount >= 0:
                life_out += amount
            else:
                life_in += -amount

    for sym, pos in positions.items():
        pos["avg_cost"] = round(pos["cost"] / pos["qty"], 6) if pos["qty"] else None
        pos["cost"] = round(pos["cost"], 2)

    return {
        "count": len(rows),
        "trades": sum(1 for r in rows if r["type"] in ("buy", "sell")
                      and "期初" not in (r.get("note") or "")),
        "cash": round(cash, 2),
        "fees": round(fees, 2),
        "deposited": round(deposited, 2),
        "dividends": round(dividends, 2),
        "life_out": round(life_out, 2),
        "positions": positions,
        "realized_pnl": round(realized, 2),
        "dates": [r["date"] for r in rows],
    }


def filter_rows(rows, date=None, etype=None, symbol=None):
    out = rows
    if date:
        out = [r for r in out if r["date"] == date]
    if etype:
        out = [r for r in out if r["type"] == etype]
    if symbol:
        out = [r for r in out if r.get("symbol") == symbol]
    return out


# ---------------------------------------------------------------------------
# 对账
# ---------------------------------------------------------------------------

def reconcile(rows: list[dict], account_state_path: Path = ACCOUNT_STATE) -> dict:
    """把台账推算结果与 account-state 只读快照核对，返回差异清单。"""
    s = calc(rows)
    checks = []

    def add(name, book, broker, tol, unit=""):
        diff = round((book or 0) - (broker or 0), 2)
        checks.append({
            "item": name,
            "ledger": book,
            "broker": broker,
            "diff": diff,
            "ok": abs(diff) <= tol,
            "unit": unit,
        })

    if not account_state_path.exists():
        return {"ok": False, "checks": [{"item": "account-state 缺失", "ok": False}], "summary": s}

    st = json.loads(account_state_path.read_text(encoding="utf-8"))
    add("可用资金", s["cash"], st.get("cash"), 0.01, "元")
    mv = 0.0
    pos_map = {p["symbol"]: p for p in st.get("positions", [])}
    for sym, p in s["positions"].items():
        if p["qty"] == 0:
            continue
        b = pos_map.get(sym)
        add(f"{sym} 股数", p["qty"], (b or {}).get("qty"), 0, "股")
        add(f"{sym} 成本价", p["avg_cost"], (b or {}).get("cost"), 0.0001, "元")
    for sym, b in pos_map.items():
        mv += float(b.get("market_value") or 0.0)
    add("参考市值", mv, st.get("market_value"), 0.01, "元")
    add("总资产", s["cash"] + mv, st.get("total_assets"), 0.01, "元")
    # 当日盈亏 = 各持仓当日盈亏之和（不含费用）
    day_pnl_sum = round(sum(float(p.get("day_pnl") or 0.0) for p in st.get("positions", [])), 2)
    add("当日盈亏(持仓合计)", day_pnl_sum, st.get("day_pnl"), 0.01, "元")
    # 累计盈亏：台账口径 = 市值 + 累计卖出回款 - 累计买入成交额 - 累计费用（已实现+浮动）
    total_buy_amt = sum(p["buy_amt"] for p in s["positions"].values())
    total_sell_amt = sum(p["sell_amt"] for p in s["positions"].values())
    book_total_pnl = round(mv + total_sell_amt - total_buy_amt - s["fees"], 2)
    add("累计盈亏(台账)", book_total_pnl, st.get("total_pnl"), 0.02, "元")
    if st.get("total_assets"):
        add("仓位%", round((mv / st["total_assets"]) * 100, 2), st.get("position_pct"), 0.05, "%")
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "summary": s, "state": st}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_add(args):
    entry = {
        "date": args.date,
        "type": args.type,
        "symbol": args.symbol,
        "price": args.price,
        "qty": args.qty,
        "fee": args.fee,
        "amount": args.amount,
        "note": args.note,
        "category": args.category,
    }
    e = append(entry, Path(args.file))
    print(f"[ledger] + {e['date']} {e['type']} "
          f"{e.get('symbol') or ''} {('%.4g' % e['price']) if e['price'] else ''} "
          f"{('x%s' % e['qty']) if e['qty'] else ''} amount={e['amount']} fee={e['fee']}")
    return 0


def cmd_list(args):
    rows = filter_rows(load(Path(args.file)), args.date, args.type, args.symbol)
    if not rows:
        print("[ledger] 无分录")
        return 0
    print(f"{'date':10} {'type':8} {'symbol':10} {'price':>8} {'qty':>6} {'fee':>6} {'amount':>10}  note")
    for r in rows:
        print(f"{r['date']:10} {r['type']:8} {(r.get('symbol') or '-'):10} "
              f"{('%.4f' % r['price']) if r.get('price') else '-':>8} "
              f"{(str(r['qty']) if r.get('qty') else '-'):>6} "
              f"{('%.2f' % r['fee']):>6} "
              f"{('%.2f' % r['amount']) if r.get('amount') is not None else '-':>10}  {r.get('note', '')}")
    print(f"[ledger] 共 {len(rows)} 条")
    return 0


def cmd_summary(args):
    rows = [r for r in load(Path(args.file)) if not args.as_of or r["date"] <= args.as_of]
    s = calc(rows)
    print("== 台账汇总（append-only 口径）==")
    print(f"分录：{s['count']} 条｜日期：{min(s['dates']) if s['dates'] else '-'} ~ {max(s['dates']) if s['dates'] else '-'}")
    print(f"净转入：{s['deposited']:,.2f} 元｜推算可用现金：{s['cash']:,.2f} 元")
    print(f"累计费用：{s['fees']:,.2f} 元｜股息：{s['dividends']:,.2f} 元")
    print(f"已实现盈亏：{s['realized_pnl']:,.2f} 元")
    if s["positions"]:
        print("持仓（账面成本，未含市价）：")
        for sym, p in s["positions"].items():
            if p["qty"] == 0:
                continue
            print(f"  {sym}: {p['qty']} 股｜成本价 {p['avg_cost']:.4f}｜成本额 {p['cost']:,.2f}")
    if s["life_out"]:
        print(f"个人生活支出（category=life，另账）：{s['life_out']:,.2f} 元")
    return 0


def cmd_check(args):
    rows = load(Path(args.file))
    r = reconcile(rows, Path(args.account_state) if args.account_state else ACCOUNT_STATE)
    s = r["summary"]
    print("== 台账 vs account-state 只读快照 ==")
    for c in r["checks"]:
        mark = "OK " if c["ok"] else "差异"
        if "diff" in c:
            print(f"[{mark}] {c['item']}: 台账 {c['ledger']} / 券商 {c['broker']} (差 {c['diff']}{c['unit']})")
        else:
            print(f"[{mark}] {c['item']}")
    print(f"结论：{'全部对平' if r['ok'] else '存在差异，见上'}")
    return 0 if r["ok"] else 1


def main(argv=None):
    p = argparse.ArgumentParser(description="润财台账（append-only）")
    p.add_argument("--file", default=str(LEDGER),
                   help="台账文件，默认 data/ledger.jsonl；个人账用 data/personal/ledger.jsonl")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="追加一条分录")
    a.add_argument("--date", required=True)
    a.add_argument("--type", required=True, choices=TYPES)
    a.add_argument("--symbol")
    a.add_argument("--price", type=float)
    a.add_argument("--qty", type=float)
    a.add_argument("--fee", type=float, default=0.0)
    a.add_argument("--amount", type=float)
    a.add_argument("--note", default="")
    a.add_argument("--category", choices=CATEGORIES, default="trade")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="列出分录")
    l.add_argument("--date")
    l.add_argument("--type", choices=TYPES)
    l.add_argument("--symbol")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("summary", help="汇总")
    s.add_argument("--as-of")
    s.set_defaults(func=cmd_summary)

    c = sub.add_parser("check", help="与 account-state 对账")
    c.add_argument("--account-state")
    c.set_defaults(func=cmd_check)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
