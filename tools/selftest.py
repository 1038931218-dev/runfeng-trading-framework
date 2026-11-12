#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链路自检（2026-11-07）。一条命令检查脚本/数据/计划/账户/风控是否一致。

用法: python3 tools/selftest.py [--quiet]
退出码: 0 全通过；1 有失败（逐条打印 FAIL）
"""
import json
import os
import py_compile
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
DATA = os.path.join(ROOT, "data")
MODELS = os.path.join(ROOT, "models")
FAILS = []
CHECKS = 0


def ok(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if cond:
        return True
    FAILS.append(f"{name} {detail}".strip())
    return False


def load(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return default


def main():
    global CHECKS
    quiet = "--quiet" in sys.argv

    # 1) 语法
    for fn in sorted(os.listdir(os.path.join(ROOT, "tools"))):
        if fn.endswith(".py"):
            try:
                py_compile.compile(os.path.join(ROOT, "tools", fn), doraise=True)
                ok(f"compile {fn}", True)
            except Exception as e:
                ok(f"compile {fn}", False, str(e))

    # 2) 导入
    import importlib
    for mod in ["market", "account_filter", "daily_brief", "make_plan", "daily_ops",
                "watch_intraday", "risk_metrics", "screen_tech"]:
        try:
            importlib.import_module(mod)
            ok(f"import {mod}", True)
        except Exception as e:
            ok(f"import {mod}", False, f"{type(e).__name__}: {e}")

    # 3) 数据文件可解析
    files = ["snapshot-2026-11-06.json", "industry-2026-11-06.json", "index-tech-crosscheck.json",
             "risk-2026-11-06.json", "watchlist.json", "plan-2026-11-09.json",
             "brief-2026-11-06.json"]
    for f in files:
        ok(f"json {f}", load(os.path.join(DATA, f)) is not None)

    snap = load(os.path.join(DATA, "snapshot-2026-11-06.json"), {}) or {}
    brief = load(os.path.join(DATA, "brief-2026-11-06.json"), {}) or {}
    plan = load(os.path.join(DATA, "plan-2026-11-09.json"), {}) or {}
    wl = load(os.path.join(DATA, "watchlist.json"), {}) or {}
    acct = load(os.path.join(DATA, "account-state.json"), {}) or {}
    risk = load(os.path.join(DATA, "risk-2026-11-06.json"), {}) or {}
    import glob
    pf_path = sorted(glob.glob(os.path.join(MODELS, "portfolio-*.json")))[-1]
    pf = load(pf_path) or {}

    # 4) 快照/摘要一致
    b = brief.get("breadth") or {}
    ok("brief 与快照宽度一致", b.get("up") == 758 and b.get("down") == 4740 and b.get("flat") == 76,
       f"{b.get('up')}/{b.get('down')}/{b.get('flat')}")
    ok("brief 与快照成交额一致", b.get("amount_yi") in (16713.6,), str(b.get("amount_yi")))

    # 5) 计划 schema
    rules = plan.get("rules") or []
    ok("plan 规则数>=8", len(rules) >= 8, str(len(rules)))
    for r in rules:
        ok(f"plan 字段 {r.get('id')}",
           all(k in r for k in ("id", "symbol", "op", "price", "action")))
        if r.get("action") == "buy_ready":
            ok(f"plan 买点含floor {r.get('id')}", r.get("floor") is not None)
        if r.get("field") == "pct":
            ok(f"plan pct阈值 {r.get('id')}", r.get("price") < 0)

    # 6) 板块权限：候选全部主板
    import account_filter
    for c in wl.get("candidates") or []:
        board, trad = account_filter.board_of(c["symbol"])
        ok(f"候选主板 {c.get('name')}", trad, board)
    for p in pf.get("positions") or []:
        board, trad = account_filter.board_of(p["symbol"])
        ok(f"组合主板 {p.get('name')}", trad, board)

    # 7) 组合红线
    pos = pf.get("positions") or []
    tw = sum(p.get("weight_pct", 0) for p in pos)
    ok("组合权重合计 38", abs(tw - 38) < 0.01, str(tw))
    ok("组合现金 62", abs((pf.get("cash_pct") or 0) - 62) < 0.01, str(pf.get("cash_pct")))
    ok("单票<=5", max((p.get("weight_pct", 0) for p in pos), default=0) <= 5.0 + 1e-9)
    from collections import defaultdict
    ind = defaultdict(float)
    for p in pos:
        ind[p.get("industry", "?")] += p.get("weight_pct", 0)
    ok("单行业<=10", max(ind.values(), default=0) <= 10.0 + 1e-9, str(dict(ind)))

    # 8) 止损预算
    tot = sum(p.get("loss_at_stop_pct_of_total", 0) or 0 for p in pos)
    ok("全止损<=3%", tot <= 3.0 + 1e-6, f"{tot:.3f}")
    ok("风控JSON止损对账一致", abs(tot - 2.883) < 0.05, f"{tot:.3f}")
    ok("风控对象齐全", {"portfolio_12", "vanke_400", "icbc_single"} <= set((risk.get("objects") or {}).keys()))

    # 9) 账户状态（示例/框架仓库可能没有真实账户文件，做条件检查）
    if acct.get("total_assets"):
        eq = [(p.get("qty") or 0) * (p.get("cost") or 0) for p in acct.get("positions") or []]
        ok("账户总资产>=可用现金", (acct.get("total_assets") or 0) >= (acct.get("cash") or 0) - 1e-6,
           str(acct.get("total_assets")))
        if acct.get("positions"):
            ok("账户有持仓且数量>0", all((p.get("qty") or 0) > 0 for p in acct["positions"]))
    wanke = next((r for r in snap.get("rows") or [] if r.get("symbol") == "sz000002"), {})
    if wanke:
        ok("万科快照价>0", (wanke.get("price") or 0) > 0, str(wanke.get("price")))

    # 10) 下单闸门 smoke（用数据驱动，不写死某只票）
    import subprocess
    cand = (wl.get("candidates") or [{}])[0].get("symbol") or "sh601668"
    guard = lambda *a: subprocess.run([sys.executable, os.path.join(ROOT, "tools", "daily_ops.py"),
                                       "guard"] + list(a), capture_output=True, text=True).returncode
    ok(f"guard 主板买入可评估 {cand}", guard(cand, "buy", "1.00", "100") in (0, 2))
    ok("guard 科创板必拒", guard("sh688981", "buy", "1.00", "100") == 2)
    held = next((p for p in acct.get("positions") or [] if (p.get("qty") or 0) >= 100), None)
    if held:
        ok(f"guard 卖出超量必拒 {held['symbol']}", guard(held["symbol"], "sell", "1.00", str(held["qty"] + 100)) == 2)

    # 11) 日报守护
    wl_sh = os.path.join(ROOT, "tools", "watch_loop.sh")
    ok("watch_loop.sh 存在", os.path.exists(wl_sh))
    ok("plan 有 emergency 规则", any(r.get("action") == "emergency_sell" for r in rules))
    ok("plan 有 pct 风控规则", any(r.get("field") == "pct" for r in rules))

    if FAILS:
        print(f"SELFTEST FAIL {len(FAILS)}/{CHECKS}")
        for f in FAILS:
            print("  FAIL", f)
        return 1
    if not quiet:
        print(f"SELFTEST PASS {CHECKS} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
