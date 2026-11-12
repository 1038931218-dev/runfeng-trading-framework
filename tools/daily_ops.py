#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日常运维一条龙（2026-11-07，润行签发）—— 给定时任务用的「低 Token 入口」。

把开盘/巡检/收盘的固定动作收敛成 3 个命令，Agent 每次唤醒只需：
    python3 tools/daily_ops.py open|check|close
然后按 ≤3 行输出行动，不再重新读一堆文件。

用法:
  python3 tools/daily_ops.py open     # 确保今日计划存在；打印一行状态 + 监控启动命令
  python3 tools/daily_ops.py check    # 读今日告警；有告警 exit 10 并打印攻略，无告警 exit 0
  python3 tools/daily_ops.py close    # 汇总告警、写 reports/watch-<date>.md、生成次日计划

所有输出都很短，便于 Agent 直接判断；细节仍写在文件里可查。
"""
import json
import os
import sys
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
DATA = os.path.join(ROOT, "data")
REPORTS = os.path.join(ROOT, "reports")
EXIT_ALERT = 10
DEFAULT_HOLIDAYS = {"2027-01-01"}  # 已知休市；其余节假日待核实后补


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def today_str():
    return date.today().isoformat()


def is_trading_day(d=None):
    """周末或 watchlist.session.holidays 里的日期返回 False。"""
    d = d or date.today()
    if d.weekday() >= 5:
        return False
    wl = load(os.path.join(DATA, "watchlist.json"), {}) or {}
    holidays = set((wl.get("session") or {}).get("holidays") or []) | DEFAULT_HOLIDAYS
    return d.isoformat() not in holidays


def plan_path(d):
    return os.path.join(DATA, f"plan-{d}.json")


def alerts_path(d):
    return os.path.join(DATA, f"alerts-{d}.jsonl")


def alert_state_path(d):
    return os.path.join(DATA, f"alert-state-{d}.json")


def load_alert_state(d):
    return load(alert_state_path(d), {}) or {"processed": 0}


def save_alert_state(d, st):
    with open(alert_state_path(d), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def read_alerts(d):
    p = alerts_path(d)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def ensure_plan(d):
    """确保今日计划存在；缺就用 make_plan 生成。返回 (plan, 是否新生成)。"""
    p = plan_path(d)
    plan = load(p)
    if plan:
        return plan, False
    from datetime import datetime as _dt
    import make_plan  # 同目录脚本
    plan = make_plan.build_plan(_dt.strptime(d, "%Y-%m-%d").date())
    return plan, True


# ---------- 子命令 ----------


def ensure_bridge():  # 手动/将来启用 Mika 时再调用
    """确保 Mika 桥接守护在跑（文件队列 RPC，零 token）。"""
    import subprocess
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            parts = [x for x in open(f'/proc/{pid}/cmdline', 'rb').read().decode('utf8', 'ignore').split('\0') if x]
        except Exception:
            continue
        if any(x.endswith('mika_bridge.py') for x in parts):
            return True
    try:
        subprocess.Popen(['python3', 'tools/mika_bridge.py'], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False



def ensure_guard():
    """交易时段守护：确保 session_guard 在跑（补起被休眠中断的监控）。"""
    import subprocess
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            parts=[x for x in open(f'/proc/{pid}/cmdline','rb').read().decode('utf8','ignore').split('\0') if x]
        except Exception:
            continue
        if any(x.endswith('session_guard.py') for x in parts):
            return True
    try:
        subprocess.Popen(['python3','tools/session_guard.py'], cwd=ROOT,
                         stdout=open('/tmp/session_guard.log','ab'), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False

def ensure_dashboard():
    import subprocess
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            parts=[x for x in open(f'/proc/{pid}/cmdline','rb').read().decode('utf8','ignore').split('\0') if x]
        except Exception:
            continue
        if any(x.endswith('dashboard.py') for x in parts):
            return True
    try:
        subprocess.Popen(['python3','tools/dashboard.py'], cwd=ROOT,
                         stdout=open('/tmp/dashboard.log','ab'), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:
        return False


def cmd_open():
    d = today_str()
    if not is_trading_day():
        print(f"OPEN SKIP {d} 非交易日")
        return 0
    ensure_bridge()
    ensure_guard()
    ensure_dashboard()
    try:
        import subprocess as _sp
        h = _sp.run([sys.executable, "tools/health_check.py"], cwd=ROOT, capture_output=True, text=True, timeout=180)
        print("OPEN_HEALTH:", (h.stdout or "").strip().splitlines()[0] if h.stdout else "")
    except Exception:
        pass
    plan, created = ensure_plan(d)
    if not plan:
        print(f"OPEN FAIL 无计划且生成失败 {d}")
        return 1
    idx = plan.get("index_levels") or {}
    acct = load(os.path.join(DATA, "account-state.json"), {})
    pos = acct.get("positions") or []
    print(f"OPEN {d} plan={'new' if created else 'exists'} rules={len(plan.get('rules') or [])} "
          f"watch={len(plan.get('watch') or [])} ma20={idx.get('ma20')} low250={idx.get('low250')}")
    if pos:
        print("HOLD " + "; ".join(f"{p['symbol']}:{p.get('qty')}" for p in pos))
    print(f"CMD bash run_in_background: bash tools/watch_loop.sh data/plan-{d}.json")
    print("NEXT 读通达信确认登录态；启动上面 CMD；09:31 模拟盘演示（买 601668 100 / 卖 000651 100）")
    return 0


def cmd_check():
    d = today_str()
    if not is_trading_day():
        print(f"CHECK SKIP {d} 非交易日")
        return 0
    alerts = read_alerts(d)
    st = load_alert_state(d)
    done = int(st.get("processed", 0))
    fresh = alerts[done:]
    alive = os.system("pgrep -f '[w]atch_(intraday|loop)' >/dev/null 2>&1") == 0
    if not fresh:
        print(f"CHECK {d} alerts={len(alerts)} unprocessed=0 watcher={'alive' if alive else 'dead'}")
        return 0
    last = fresh[-1]
    print(f"CHECK {d} alerts={len(alerts)} unprocessed={len(fresh)} watcher={'alive' if alive else 'dead'} "
          f"last={last.get('ts')} {last.get('label')} value={last.get('value', last.get('price'))} "
          f"action={last.get('action')} side={last.get('side')} qty={last.get('qty')}")
    acts = sorted({a.get("action") for a in fresh})
    print(f"ACTIONS {','.join(acts)} FILE data/alerts-{d}.jsonl")
    if any(a.get("action") in ("emergency_sell", "risk_off", "sell_ready") for a in fresh):
        print("URGENT 卖出类优先：通达信→交易→卖出→填单提交（免密）；成交后运行 `python3 tools/daily_ops.py ack`")
    else:
        print("WATCH 按计划处理；处理完运行 `python3 tools/daily_ops.py ack`")
    return EXIT_ALERT


def cmd_ack():
    d = today_str()
    alerts = read_alerts(d)
    st = load_alert_state(d)
    st["processed"] = len(alerts)
    st["acked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_alert_state(d, st)
    print(f"ACK {d} processed={len(alerts)}")
    return 0


def cmd_guard(*args):
    """下单前闸门：guard <symbol> <buy|sell> <price> <qty>。PASS/FAIL 简洁输出。"""
    if len(args) < 4:
        print("USAGE guard <symbol> <buy|sell> <price> <qty>")
        return 1
    sym, side, price, qty = args[0], args[1].lower(), float(args[2]), int(args[3])
    fails = []
    snap = load(os.path.join(DATA, "snapshot-2026-11-06.json"), {}) or {}
    row = next((r for r in snap.get("rows") or [] if r.get("symbol") == sym), None)
    acct = load(os.path.join(DATA, "account-state.json"), {}) or {}
    plan = load(plan_path(today_str()), {}) or {}
    import account_filter
    board, ok = account_filter.board_of(sym)
    if not ok:
        fails.append(f"板块权限 {board}")
    if qty <= 0 or qty % 100 != 0:
        fails.append("数量非100整数倍")
    if side == "buy":
        if row:
            if "ST" in row.get("name", ""):
                fails.append("ST/*ST")
            if row.get("amount", 0) < 2e8:
                fails.append("成交额<2亿")
            if (row.get("pe") or 0) <= 0:
                fails.append("PE<=0（亏损）")
            if abs(price / row["price"] - 1) > 0.11:
                fails.append("价格偏离现价>11%")
        cash = acct.get("cash") or 0
        need = price * qty + 5
        if need > cash:
            fails.append(f"现金不足（需{need:.0f}，可用{cash}）")
        pos = acct.get("positions") or []
        if len(pos) >= 2 and not any(p.get("symbol") == sym for p in pos):
            fails.append("持仓已2只")
        total = acct.get("total_assets") or 5044
        wl = load(os.path.join(DATA, "watchlist.json"), {}) or {}
        min_new = (wl.get("guards") or {}).get("min_new_position_value") or 0
        held_this = sum(p.get("qty", 0) for p in (acct.get("positions") or []) if p.get("symbol") == sym)
        if min_new and not held_this and need < min_new:
            fails.append(f"新开仓1手金额{need:.0f}<{min_new}（最低佣金使费比过高）")
        if need > total * 0.20:
            fails.append(f"单票超20%（{need:.0f}>20%资产）")
        cash_after = cash - need
        if cash_after < total * 0.60:
            fails.append(f"现金比例跌破60%（余{cash_after:.0f}={cash_after/total:.0%}）")
    else:
        held = sum(p.get("qty", 0) for p in (acct.get("positions") or []) if p.get("symbol") == sym)
        if qty > held:
            fails.append(f"可卖不足（持有{held}）")
    rules = [r for r in plan.get("rules") or [] if r.get("symbol") == sym]
    print(f"GUARD {sym} {side} {price} x{qty} board={board} rules={len(rules)} "
          + ("PASS" if not fails else "FAIL " + "；".join(fails)))
    return 0 if not fails else 2




def git_sync(timeout=120):
    """收盘自动提交并推送（失败不影响收盘结果）。返回一行摘要。"""
    import subprocess
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        return "git=skip(no repo)"
    try:
        subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"],
                       cwd=ROOT, timeout=timeout, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=ROOT, timeout=timeout, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, timeout=60)
        if diff.returncode == 0:
            return "git=nochange"
        msg = "收盘自动提交 " + datetime.now().strftime("%Y-%m-%d %H:%M")
        c = subprocess.run(["git", "-c", "user.name=runfeng", "-c", "user.email=runfeng@local",
                            "commit", "-m", msg], cwd=ROOT, timeout=timeout, capture_output=True, text=True)
        if c.returncode != 0:
            return "git=commit_fail"
        p = subprocess.run(["git", "push", "origin", "main"], cwd=ROOT, timeout=timeout,
                           capture_output=True, text=True)
        return "git=push_ok" if p.returncode == 0 else "git=push_fail"
    except Exception as e:  # noqa
        return f"git=error({type(e).__name__})"

def cmd_close():
    d = today_str()
    if not is_trading_day():
        print(f"CLOSE SKIP {d} 非交易日（不生成次日计划）")
        return 0
    alerts = read_alerts(d)
    plan = load(plan_path(d), {}) or {}
    wl = load(os.path.join(DATA, "watchlist.json"), {}) or {}
    acct = load(os.path.join(DATA, "account-state.json"), {}) or {}
    # 写当日摘要
    os.makedirs(REPORTS, exist_ok=True)
    out = os.path.join(REPORTS, f"watch-{d}.md")
    lines = [f"# 盘中监控日报 · {d}", "",
             f"> 自动生成 {datetime.now():%Y-%m-%d %H:%M}（润行签发）｜告警 {len(alerts)} 条", ""]
    if alerts:
        lines += ["| 时间 | 规则 | 标的 | 价格/值 | 动作 | 数量 |", "|---|---|---|---:|---|---:|"]
        for a in alerts:
            lines.append(f"| {a.get('ts','')} | {a.get('label','')} | {a.get('name','')} | "
                         f"{a.get('value', a.get('price'))} | {a.get('action','')} | {a.get('qty','')} |")
        lines += ["", f"原始流水：`data/alerts-{d}.jsonl`"]
    else:
        lines.append("今日无告警、无交易。")
    if acct:
        lines += ["", f"账户（收盘）：总资产 {acct.get('total_assets')}｜可用 {acct.get('cash')}｜"
                      f"持仓 {len(acct.get('positions') or [])} 只"]
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    # 生成次日计划
    try:
        import make_plan
        nxt = make_plan.next_trade_date(date.today(), (wl.get("session") or {}).get("holidays") or [])
        np = make_plan.build_plan(nxt)
        next_info = f"next_plan={nxt} rules={len(np.get('rules') or []) if np else 0}"
    except Exception as e:  # noqa
        next_info = f"next_plan=FAIL({type(e).__name__})"
    # 同步交接包给外部执行器（Mika），失败不影响收盘
    try:
        import sync_mika
        sync_mika.main()
    except Exception as e:  # noqa
        print(f"WARN sync_mika: {type(e).__name__}")
    # 每日行情 → 刷新股票池（写入 watchlist 候选，供次日计划）
    try:
        import subprocess as _sp
        _sp.run([sys.executable, "tools/refresh_pool.py"], cwd=ROOT, capture_output=True, timeout=900)
        pool_note2 = "pool_refresh=ok"
    except Exception:
        pool_note2 = "pool_refresh=fail"
    # 团队日更（事实层）：快照/板块/brief/筛选/风控/次日计划
    try:
        import subprocess as _sp
        t = _sp.run([sys.executable, "tools/team_daily.py"], cwd=ROOT, capture_output=True, text=True, timeout=900)
        team_note = "team_daily=ok" if t.returncode == 0 else "team_daily=fail"
    except Exception:
        team_note = "team_daily=error"
    try:
        import subprocess as _sp
        _sp.run([sys.executable, "tools/export_reports.py"], cwd=ROOT, capture_output=True, timeout=120)
    except Exception:
        pass
    try:
        acct = load(os.path.join(DATA, "account-state.json"), {}) or {}
        if acct:
            import json as _j
            with open(os.path.join(DATA, "account-history.jsonl"), "a", encoding="utf-8") as f:
                f.write(_j.dumps({"date": d, "total": acct.get("total_assets"),
                                  "cash": acct.get("cash"), "market_value": acct.get("market_value"),
                                  "note": "收盘自动记录"}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    print(f"POOL {pool_note2}")
    try:
        import subprocess as _sp2
        _sp2.run([sys.executable, "tools/pool_eval.py"], cwd=ROOT, capture_output=True, timeout=300)
        import datetime as _dt2
        if _dt2.date.today().weekday() == 4:
            _sp2.run([sys.executable, "tools/pool_tune.py"], cwd=ROOT, capture_output=True, timeout=300)
        print("POOL_EVAL ok")
    except Exception:
        print("POOL_EVAL fail")
    git_line = git_sync()
    print(f"CLOSE {d} alerts={len(alerts)} report=reports/watch-{d}.md {next_info} {git_line}")
    print("NEXT 15:05 已完成；提醒 11-12 前续期万科 示例止损价云条件单")
    return 0


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    fn = {"open": cmd_open, "check": cmd_check, "ack": cmd_ack, "close": cmd_close}.get(cmd)
    if cmd == "guard":
        return cmd_guard(*sys.argv[2:])
    if not fn:
        print(__doc__)
        return 1
    return fn()


if __name__ == "__main__":
    sys.exit(main())
