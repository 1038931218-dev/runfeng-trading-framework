#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日盯盘计划生成器（2026-11-07，润行签发）。

作用：每天收盘后（或次日开盘前）把「最新计划」同步成 data/plan-<下一交易日>.json，
watch_intraday.py 会自动读取当天日期的计划文件，无需改代码。

输入（都可人工改）：
  data/watchlist.json        候选买点区间、持仓止损、指数规则
  data/account-state.json    实盘持仓/现金（收盘后由 15:05 任务或人工更新；缺省则用 watchlist 的 holdings_rules）
  data/index-tech-crosscheck.json  指数 MA20/250日低（缺省时用 kline 现场算）
  最新 models/portfolio-*.json     兜底止损价

输出：
  data/plan-<下一交易日>.json       （交易时段 watch_intraday.py 自动读取）

用法：
  python3 tools/make_plan.py                      # 生成下一交易日计划
  python3 tools/make_plan.py --date 2026-11-10    # 指定日期
  python3 tools/make_plan.py --dry-run            # 只打印，不写文件
"""
import argparse
import glob
import json
import os
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import market  # noqa: E402

DATA = os.path.join(ROOT, "data")
MODELS = os.path.join(ROOT, "models")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def next_trade_date(start=None, holidays=None):
    d = start or date.today()
    d = d + timedelta(days=1)
    holidays = set(holidays or [])
    for _ in range(15):
        if d.weekday() < 5 and d.isoformat() not in holidays:
            return d
        d = d + timedelta(days=1)
    return d


def kline_stats(symbol, count=250):
    """返回 close/ma20/ma60/ma250/low250/high250；失败返回 None。"""
    try:
        bars = market.kline(symbol, count)
    except Exception as e:
        print(f"[make_plan] {symbol} 日K失败：{e}", file=sys.stderr)
        return None
    if not bars:
        return None
    closes = [b["close"] for b in bars]
    lows = [b["low"] for b in bars]
    highs = [b["high"] for b in bars]
    n = len(closes)
    ma = lambda k: sum(closes[-k:]) / min(k, n)  # noqa: E731
    return {
        "date": bars[-1]["date"], "close": closes[-1],
        "ma20": round(ma(20), 3), "ma60": round(ma(60), 3), "ma250": round(ma(250), 3),
        "low250": round(min(lows[-250:]), 3), "high250": round(max(highs[-250:]), 3),
        "bars": n,
    }


def latest_portfolio():
    files = sorted(glob.glob(os.path.join(MODELS, "portfolio-*.json")))
    return load(files[-1], {}) if files else {}


def portfolio_stops(pf):
    out = {}
    for p in (pf.get("positions") or []):
        if p.get("stop_loss") is not None:
            out[p["symbol"].replace("sh", "sh", 1)] = p["stop_loss"]
    return out


def build_plan(target_date, dry_run=False):
    if isinstance(target_date, str):
        target_date = datetime.strptime(target_date, "%Y-%m-%d").date()
    wl = load(os.path.join(DATA, "watchlist.json"), {})
    if not wl:
        print("[make_plan] 缺少 data/watchlist.json", file=sys.stderr)
        return None
    idx_x = load(os.path.join(DATA, "index-tech-crosscheck.json"), {})
    acct = load(os.path.join(DATA, "account-state.json"), {})
    pf = latest_portfolio()
    stops = portfolio_stops(pf)

    indices = wl.get("index_rules") or {}
    idx_sym = indices.get("symbol", "sh000001")
    idx_stats = kline_stats(idx_sym)
    index_ma20 = idx_stats["ma20"] if idx_stats else (idx_x.get(idx_sym) or {}).get("ma20")
    index_low250 = idx_stats["low250"] if idx_stats else (idx_x.get(idx_sym) or {}).get("low250")

    rules = []
    # 指数规则
    if index_ma20:
        rules.append({"id": "index_ma20_up", "label": f"上证收复 MA20 {index_ma20}（升级观察）",
                      "symbol": idx_sym, "op": ">=", "price": round(index_ma20, 2),
                      "action": "notify", "cooldown_sec": 1800,
                      "note": "收盘站上且连续2日+量能确认后才考虑加仓，盘中只提示"})
    if index_low250:
        rules.append({"id": "index_250low_break", "label": f"上证跌破 250 日低 {index_low250}（降级）",
                      "symbol": idx_sym, "op": "<=", "price": round(index_low250, 2),
                      "action": "risk_off", "cooldown_sec": 900,
                      "note": "系统性风险：取消当日全部买入，只处理卖出"})

    # 持仓规则（账户状态优先，否则用 watchlist 的 holdings_rules）
    holdings = acct.get("positions") or []
    if holdings:
        hmap = {h["symbol"]: h for h in holdings}
        hrules = [r for r in (wl.get("holdings_rules") or []) if r["symbol"] in hmap]
        for h in holdings:
            if not any(r["symbol"] == h["symbol"] for r in hrules):
                stop = stops.get(h["symbol"])
                if stop:
                    hrules.append({"symbol": h["symbol"], "name": h.get("name", h["symbol"]),
                                   "qty": h.get("qty"), "stops": [round(stop, 3)]})
    else:
        hrules = wl.get("holdings_rules") or []

    for h in hrules:
        sym, name = h["symbol"], h.get("name", h["symbol"])
        qty = h.get("qty")
        stats = kline_stats(sym)
        for i, stop in enumerate(h.get("stops") or []):
            rules.append({
                "id": f"{sym}_stop_{i+1}_" + str(stop).replace(".", "_"),
                "label": f"{name} 止损 {stop}（" + ("云条件单/硬止损" if i == 0 else "二次止损") + "）",
                "symbol": sym, "op": "<=", "price": stop,
                "action": "sell_ready", "side": "sell", "qty": qty, "cooldown_sec": 600,
                "note": (h.get("note") or "") + (f"；现价 {stats['close']}，MA20 {stats['ma20']}" if stats else ""),
            })
        if h.get("rebound"):
            rq = h.get("reduce_qty")
            rules.append({"id": f"{sym}_rebound", "label": f"{name} 反弹 {h['rebound']} " + ("减仓" if rq else "提示"),
                          "symbol": sym, "op": ">=", "price": h["rebound"],
                          "action": "sell_ready" if rq else "notify",
                          "side": "sell" if rq else None, "qty": rq,
                          "cooldown_sec": 1800,
                          "note": h.get("note") or ("反弹到均线附近，提示是否减仓")})

    # 极端行情兜底规则（用户已授权可不经确认直接止损）
    em = wl.get("emergency") or {}
    if em.get("index_pct_drop") is not None:
        rules.append({"id": "index_pct_drop", "label": f"上证日内跌幅≤{em['index_pct_drop']}%（极端风险）",
                      "symbol": idx_sym, "op": "<=", "price": em["index_pct_drop"], "field": "pct",
                      "action": "risk_off", "cooldown_sec": 600,
                      "note": "极端行情：取消当日全部买入，评估减仓；润行可直接执行紧急止损"})
    hp = em.get("holding_pct_drop")
    if hp is not None:
        for h in hrules:
            rules.append({"id": f"{h['symbol']}_emergency_pct",
                          "label": f"{h.get('name', h['symbol'])} 日内跌幅≤{hp}% 紧急止损",
                          "symbol": h["symbol"], "op": "<=", "price": hp, "field": "pct",
                          "action": "emergency_sell", "side": "sell", "qty": h.get("qty"),
                          "cooldown_sec": 300,
                          "note": "用户已授权：不等用户确认，直接市价/对手价卖出该持仓"})

    # 候选买点（只有当日报/账户计划仍有效时才会被放进来）
    min_new = (wl.get("guards") or {}).get("min_new_position_value") or 0
    held_syms = {h.get("symbol") for h in holdings} if holdings else set()
    max_pos = (wl.get("guards") or {}).get("max_positions") or 2
    holdings_full = len(held_syms) >= max_pos
    skipped = []
    for c in wl.get("candidates") or []:
        sym = c["symbol"]
        stats = kline_stats(sym)
        top = c.get("zone_top")
        floor = c.get("zone_floor")
        # 区间缺失时用均线兜底：上沿=min(昨收, MA20)，下沿=MA60*0.97
        if top is None and stats:
            top = round(min(stats["close"], stats["ma20"]), 3)
        if floor is None and stats:
            floor = round(stats["ma60"] * 0.97, 3)
        if top is None:
            continue
        # 持仓已满 → 不生成任何买单规则（避免误买）
        if holdings_full:
            skipped.append(f"{c.get('name')}(持仓已 {len(held_syms)} 只，满仓不买入)")
            continue
        # 新开仓门槛：1 手金额过小（最低佣金吞噬收益）→ 跳过；已持有可加仓
        if min_new and c["symbol"] not in held_syms and top * 100 < min_new:
            skipped.append(f"{c.get('name')}(1手{top*100:.0f}<{min_new})")
            continue
        confirm = c.get("confirm_above")
        if confirm:
            rules.append({
                "id": f"{sym}_buy", "label": f"{c.get('name', sym)} 站回确认买点",
                "symbol": sym, "op": ">=", "price": round(confirm, 3), "ceiling": c.get("ceiling"),
                "action": "buy_ready", "side": "buy", "qty": c.get("qty", 100),
                "cooldown_sec": int(c.get("cooldown_sec") or 3600),
                "note": (c.get("note") or "") + "；须站回确认后才买，跌破不接",
            })
            continue
        rules.append({
            "id": f"{sym}_buy", "label": f"{c.get('name', sym)} 1 手买点",
            "symbol": sym, "op": "<=", "price": round(top, 3), "floor": floor,
            "action": "buy_ready", "side": "buy", "qty": c.get("qty", 100),
            "cooldown_sec": int(c.get("cooldown_sec") or 3600),
            "note": (c.get("note") or "") + (f"；MA20 {stats['ma20']}，MA60 {stats['ma60']}" if stats else ""),
        })

    watch = list(dict.fromkeys([idx_sym] + [r["symbol"] for r in rules]))
    if idx_sym != "sz399006":
        try:
            watch.insert(1, "sz399006")
        except Exception:
            pass
    plan_extra = {"skipped_candidates": skipped} if skipped else {}
    guards = dict(wl.get("guards") or {})
    if index_low250:
        guards["buy_block_if_index_below"] = index_low250
    plan = {
        "date": target_date.isoformat(),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "plan_name": f"{target_date.isoformat()} 交易日监控计划（make_plan 自动生成，润行终审后生效）",
        "account": {
            "broker": acct.get("broker", "某券商"),
            "total_assets": acct.get("total_assets"),
            "cash": acct.get("cash"),
            "positions": holdings or [
                {"symbol": h["symbol"], "name": h.get("name", h["symbol"]), "qty": h.get("qty")}
                for h in hrules],
        },
        "watch": watch,
        "rules": rules,
        "guards": guards,
        "session": dict(wl.get("session") or {"weekdays_only": True,
                                             "windows": ["09:15-11:30", "13:00-15:00"],
                                             "holidays": []}),
        "notify": {"phone": True, "note": "触发后润行通知用户并打开通达信预填单；成交须用户指纹/密码"},
        "source": {"watchlist": "data/watchlist.json", "account_state": "data/account-state.json",
                   "portfolio": os.path.basename(sorted(glob.glob(os.path.join(MODELS, 'portfolio-*.json')))[-1])
                   if glob.glob(os.path.join(MODELS, 'portfolio-*.json')) else None},
        "index_levels": {"ma20": index_ma20, "low250": index_low250},
        **plan_extra,
        "emergency_policy": {
            "authorized_by": "用户 2026-11-07",
            "L0": "计划内止损触发即卖（免密，不请示）",
            "L1": f"上证日跌幅≤{em.get('index_pct_drop')}% 或持仓日跌幅≤{em.get('holding_pct_drop')}% → 直接紧急止损",
            "L2": "离线兜底：通达信云条件单（当前仅人工委托，自动委托本机未开放）",
        },
    }
    if dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=1))
        return plan
    out = os.path.join(DATA, f"plan-{target_date.isoformat()}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    print(f"[make_plan] 已生成 {out}")
    print(f"  规则 {len(rules)} 条 | 监控 {len(watch)} 个代码 | 上证MA20 {index_ma20} | 250日低 {index_low250}")
    for r in rules:
        print(f"  - {r['id']}: {r['symbol']} {r['op']} {r['price']} → {r['action']}")
    return plan


def main():
    ap = argparse.ArgumentParser(description="生成下一交易日盯盘计划")
    ap.add_argument("--date", help="目标日期 YYYY-MM-DD，默认下一交易日")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    wl = load(os.path.join(DATA, "watchlist.json"), {})
    holidays = (wl.get("session") or {}).get("holidays") or []
    if a.date:
        target = datetime.strptime(a.date, "%Y-%m-%d").date()
    else:
        target = next_trade_date(date.today(), holidays)
    if not build_plan(target, dry_run=a.dry_run):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
