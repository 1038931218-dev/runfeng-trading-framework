#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中实时监控（2026-11-07，润行签发）。

用途：交易时段内按固定间隔拉实时行情，逐条比对「当日计划」里的触发条件；命中就
① 打 ALERT 行、② 写入 data/alerts-YYYY-MM-DD.jsonl、③ 退出（退出会被 Agent 的
后台任务机制捕获 → 立刻叫醒润行处理）。最多可加 --keep-alive 持续监控到收盘。

用法:
  python3 tools/watch_intraday.py                     # 计划 data/plan-YYYY-MM-DD.json，默认 300 秒
  python3 tools/watch_intraday.py --interval 180      # 3 分钟
  python3 tools/watch_intraday.py --once              # 只查一次（适合定时任务）
  python3 tools/watch_intraday.py --test-with-snapshot  # 用 data/snapshot 离线自测规则
  python3 tools/watch_intraday.py --keep-alive        # 不因命中而退出（持续到收盘/手动停）

退出码: 0=无触发/正常结束 10=有触发（供后台任务识别） 1=异常

边界：只读行情，不下单；下单在通达信 App 里由润行填单、用户本人指纹/密码确认。
行情源 Sina hq.sinajs.cn（免费、约实时），非本机时区问题按 Asia/Shanghai 处理
（本机时区即 CST）。节假日：plan.session.holidays 里列出的日期不监控。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import market  # noqa: E402

DATA = os.path.join(ROOT, "data")
EXIT_ALERT = 10


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def parse_hhmm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def in_session(now, plan):
    """返回 (是否在交易时段, 原因)。周末/节假日/时段外返回 False。"""
    sess = plan.get("session") or {}
    if sess.get("weekdays_only", True) and now.weekday() >= 5:
        return False, "周末"
    if now.strftime("%Y-%m-%d") in set(sess.get("holidays") or []):
        return False, "节假日"
    mins = now.hour * 60 + now.minute
    for w in sess.get("windows") or ["09:15-11:30", "13:00-15:00"]:
        a, b = w.split("-")
        if parse_hhmm(a) <= mins <= parse_hhmm(b):
            return True, "交易时段"
    return False, "非交易时段"


def cond_hit(op, value, threshold):
    if value is None:
        return False
    if op == "<=":
        return value <= threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == ">":
        return value > threshold
    raise ValueError(f"未知比较符: {op}")


def prices_from_snapshot(codes):
    """离线自测：个股取 data/snapshot 现价，指数取 index-tech-crosscheck 收盘。"""
    snap = load_json(os.path.join(DATA, "snapshot-2026-11-06.json"), {}) or {}
    rows = {r["symbol"]: r for r in snap.get("rows", [])}
    idx = load_json(os.path.join(DATA, "index-tech-crosscheck.json"), {}) or {}
    out = {}
    for c in codes:
        if c in rows:
            out[c] = {"name": rows[c]["name"], "price": rows[c]["price"],
                      "pct": rows[c]["pct"], "date": "2026-11-06", "time": "15:00:00"}
        elif c in idx:
            out[c] = {"name": idx[c]["name"], "price": idx[c]["close"], "pct": None,
                      "date": idx[c].get("date", "2026-11-06"), "time": "15:00:00"}
    return out


def fetch(codes, use_snapshot=False):
    if use_snapshot:
        return prices_from_snapshot(codes)
    return market.quote(codes)


def cooldown_ok(state, rule, now_ts):
    last = (state.get("last_alert") or {}).get(rule["id"], 0)
    return now_ts - last >= int(rule.get("cooldown_sec", 300))


def guard_pass(rule, price):
    """调用 daily_ops.guard 校验买单；不可用则保守放行（提醒仍受人工确认）。"""
    import subprocess, sys as _s
    try:
        sym = rule["symbol"]; qty = rule.get("qty") or 100
        r = subprocess.run([_s.executable, "tools/daily_ops.py", "guard", sym, "buy", str(price), str(qty)],
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def sellable_ok(rule):
    """读取账户可卖数量，T+1 不可卖不下发卖单提醒。"""
    import json as _j
    try:
        a = _j.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "account-state.json"), encoding="utf-8"))
        for p in a.get("positions") or []:
            if p.get("symbol") == rule["symbol"]:
                sellable = p.get("sellable")
                if sellable is None:
                    return True
                return sellable >= (rule.get("qty") or 0)
    except Exception:
        return True
    return True


def evaluate(plan, prices, state, now):
    """返回命中列表（已过滤 cooldown 与 guard）。"""
    hits = []
    now_ts = time.time()
    guards = plan.get("guards") or {}
    no_new_before = guards.get("no_new_position_before")
    block_below = guards.get("buy_block_if_index_below")
    idx_break = None
    if block_below is not None:
        idx = prices.get("sh000001")
        if idx and idx.get("price"):
            idx_break = idx["price"] <= block_below

    for rule in plan.get("rules") or []:
        p = prices.get(rule["symbol"])
        if not p or not p.get("price"):
            continue
        field = rule.get("field", "price")   # price（默认）或 pct（当日涨跌幅）
        value = p.get("price") if field == "price" else p.get("pct")
        if value is None:
            continue
        if not cond_hit(rule["op"], value, rule["price"]):
            continue
        # 买入规则的区间下沿：跌破下沿视为破位，不买（等站回再挂）
        floor = rule.get("floor")
        if floor is not None and p["price"] < floor:
            continue
        ceiling = rule.get("ceiling")
        if ceiling is not None and p["price"] > ceiling:
            continue
        if not cooldown_ok(state, rule, now_ts):
            continue

        action = rule.get("action", "notify")
        # guard 1：买入类在 10:00 前不执行
        if action == "buy_ready" and no_new_before:
            if (now.hour * 60 + now.minute) < parse_hhmm(no_new_before):
                continue
        # guard 1.5：买单先跑 guard（仓位/现金/单票/持仓数），FAIL 则不提醒
        if action == "buy_ready":
            if not guard_pass(rule, p.get("price")):
                continue
        # guard 1.6：卖单检查可卖数量（T+1）
        if action in ("sell_ready", "emergency_sell"):
            if not sellable_ok(rule):
                continue
        # guard 2：上证跌破 250 日低 → 取消买入
        if action == "buy_ready" and idx_break:
            continue

        hits.append({
            "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
            "rule_id": rule["id"],
            "label": rule.get("label", rule["id"]),
            "symbol": rule["symbol"],
            "name": p.get("name"),
            "price": p["price"],
            "field": rule.get("field", "price"),
            "value": value,
            "op": rule["op"],
            "threshold": rule["price"],
            "action": action,
            "side": rule.get("side"),
            "qty": rule.get("qty"),
            "note": rule.get("note", ""),
        })
        state.setdefault("last_alert", {})[rule["id"]] = now_ts

    # 同一标的同一方向只报一条「最深」的（避免 3.16/3.10 同时命中重复提醒）
    best = {}
    for h in hits:
        key = (h["symbol"], h.get("side") or "-")
        cur = best.get(key)
        if cur is None:
            best[key] = h
        else:
            deeper = (h["threshold"] < cur["threshold"]) if h["op"] in ("<=", "<") else (h["threshold"] > cur["threshold"])
            if deeper:
                best[key] = h
    return list(best.values())


def main():
    ap = argparse.ArgumentParser(description="盘中计划条件监控（只读行情，不下单）")
    ap.add_argument("--plan", help="计划文件，默认 data/plan-YYYY-MM-DD.json（取今天）")
    ap.add_argument("--interval", type=int, default=300, help="轮询间隔秒，默认 300")
    ap.add_argument("--once", action="store_true", help="只查一次")
    ap.add_argument("--keep-alive", action="store_true", help="命中后继续监控到收盘")
    ap.add_argument("--max-runtime", type=int, default=8 * 3600, help="最长运行秒数（保护）")
    ap.add_argument("--test-with-snapshot", action="store_true", help="离线用快照自测规则")
    ap.add_argument("--state", default=os.path.join(DATA, "watch-state.json"))
    ap.add_argument("--alert-file", default=None, help="默认 data/alerts-YYYY-MM-DD.jsonl")
    a = ap.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    plan_path = a.plan or os.path.join(DATA, f"plan-{today}.json")
    plan = load_json(plan_path)
    if not plan:
        print(f"[watch] 找不到计划文件 {plan_path}", file=sys.stderr)
        return 1

    codes = list(dict.fromkeys(plan.get("watch") or []))
    state = load_json(a.state, {}) or {}
    alert_file = a.alert_file or os.path.join(DATA, f"alerts-{today}.jsonl")
    t0 = time.time()
    polls = 0
    fail_streak = 0
    offline = a.test_with_snapshot

    while True:
        now = datetime.now()
        if not offline and (now.hour * 60 + now.minute) > parse_hhmm("15:00"):
            print("[watch] 已收盘（15:00 后），退出")
            return 0
        if not offline:
            ok, why = in_session(now, plan)
            if not ok:
                print(f"[watch] {now:%H:%M} 跳过：{why}")
                if a.once:
                    return 0
                time.sleep(min(60, max(5, a.interval)))
                if time.time() - t0 > a.max_runtime:
                    print("[watch] 超过最长运行时间，退出")
                    return 0
                continue
        try:
            prices = fetch(codes, use_snapshot=offline)
            fail_streak = 0
        except Exception as e:  # 网络抖动不退出，下一轮重试
            fail_streak = fail_streak + 1
            print(f"[watch] 行情获取失败({fail_streak}/5)：{type(e).__name__}: {e}", file=sys.stderr)
            if fail_streak >= 5:
                hit = {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "rule_id": "data_issue",
                       "label": "行情源连续失败（数据异常）", "symbol": "-", "name": "-",
                       "price": None, "value": None, "field": "price", "op": "-",
                       "threshold": None, "action": "data_issue", "side": None, "qty": None,
                       "note": "连续5次取行情失败，检查网络/接口；监控继续重试"}
                with open(alert_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(hit, ensure_ascii=False) + "\n")
                print(f"ALERT {hit['ts']} | {hit['label']} | action=data_issue")
                return EXIT_ALERT
            if a.once:
                return 1
            time.sleep(min(30, a.interval))
            continue

        # 收盘后行情日期不是今天 → 视为未开盘/已收盘（非离线模式）
        if not offline and prices:
            any_date = next(iter(prices.values())).get("date")
            if any_date and any_date != today:
                print(f"[watch] 行情日期 {any_date} ≠ 今天 {today}（未开盘或已收盘），退出")
                return 0

        polls += 1
        hits = evaluate(plan, prices, state, now)
        save_json(a.state, state)
        if hits:
            with open(alert_file, "a", encoding="utf-8") as f:
                for h in hits:
                    f.write(json.dumps(h, ensure_ascii=False) + "\n")
            for h in hits:
                print(f"ALERT {h['ts']} | {h['label']} | {h['name']}({h['symbol']}) "
                      f"{h['value']} {h['op']} {h['threshold']} | action={h['action']} "
                      f"side={h.get('side')} qty={h.get('qty')}")
            if not a.keep_alive:
                print(f"[watch] 触发 {len(hits)} 条，退出等待处理（file={alert_file}）")
                return EXIT_ALERT
        else:
            watch_summary = " ".join(
                f"{prices[c]['name']}={prices[c]['price']}" for c in codes if c in prices)
            print(f"[watch] {now:%H:%M:%S} poll#{polls} 无触发 | {watch_summary}")

        if a.once:
            return 0
        if time.time() - t0 > a.max_runtime:
            print("[watch] 超过最长运行时间，退出")
            return 0
        time.sleep(max(20, a.interval))


if __name__ == "__main__":
    sys.exit(main())
