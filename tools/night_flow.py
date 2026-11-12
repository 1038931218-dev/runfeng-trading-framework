#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""晚间流程（确定性版，2026-11-12）：更新股票池 → 多策略选股 → 四角色打分 → 评级 → 次日计划 → 同步/推送。

设计原则：**不依赖大模型**，纯脚本完成全部动作，保证「手机不开机/模型不可用时」每晚仍有产出。
角色打分用规则化公式（可人工覆盖），分数落盘到 pool-review-<date>.json。

用法：
  python3 tools/night_flow.py            # 完整流程
  python3 tools/night_flow.py --fast     # 跳过多策略选股（快 5 倍）
"""
import datetime as dt
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
D = dt.date.today().isoformat()
DATA = os.path.join(ROOT, "data")


def run(args, timeout=1200):
    try:
        r = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa
        return 1, f"{type(e).__name__}: {e}"


def load(p, d=None):
    try:
        return json.load(open(os.path.join(ROOT, p), encoding="utf-8"))
    except Exception:
        return d if d is not None else {}


def latest(pat):
    fs = sorted(__import__("glob").glob(os.path.join(ROOT, pat)))
    return fs[-1] if fs else None


def score_role(role, item, ctx):
    """规则化打分 0–10，带一句理由。"""
    if role == "润策":
        idx_above = ctx["index_above_ma20"]
        s = 7.0 if idx_above else 5.0
        s += 0.5 if ctx["breadth_up"] else -0.5
        s -= 0.5 if item["mom20"] > 0.15 else 0
        return round(min(10, max(0, s)), 1), (f"指数{'站上' if idx_above else '仍在'}MA20，宽度{'偏强' if ctx['breadth_up'] else '偏弱'}")
    if role == "润研":
        pe = item.get("pe") or 0
        s = 6.0
        if 0 < pe <= 25: s += 1.5
        elif 25 < pe <= 60: s += 0.8
        elif 60 < pe <= 120: s -= 0.5
        elif pe > 120: s -= 1.5
        s += 0.8 if item["pct"] > 0 else -0.5
        return round(min(10, max(0, s)), 1), f"PE {pe}｜板块当日 {item['pct']}%"
    if role == "润量":
        s = 4.0
        s += 1.2 if item["ma20_slope"] > 0.005 else (-0.8 if item["ma20_slope"] < -0.005 else 0)
        s += 0.8 if item["vr"] >= 1.0 else 0.2
        s -= 0.8 if not (25 <= item["vol20"] <= 90) else 0
        ma60 = item.get("ma60")
        if ma60 is None:
            s -= 0.5
        elif item["price"] < ma60:
            s -= 1.0
        pos_txt = "MA60未能核实" if ma60 is None else ("站上MA60" if item["price"] >= ma60 else "仍在MA60下方")
        return round(min(10, max(0, s)), 1), (f"MA20{'上翘' if item['ma20_slope']>0.005 else '走平/下弯'}，{pos_txt}")
    if role == "润控":
        one_lot = item["price"] * 100
        s = 7.0
        s -= 1.5 if one_lot / max(1, ctx["total_assets"]) > 0.15 else 0
        s -= 1.5 if ctx["cash_pct"] < 65 else 0
        s -= 2.0 if ctx["positions"] >= 2 else 0
        return round(min(10, max(0, s)), 1), f"1手占资产 {one_lot / max(1, ctx['total_assets']):.1%}，现金 {ctx['cash_pct']:.0f}%，持仓 {ctx['positions']} 只"
    return 5.0, ""


def main():
    fast = "--fast" in sys.argv
    log = []

    pool_today = os.path.join(ROOT, "data", f"pool-{D}.json")
    if os.path.exists(pool_today) and "--refresh" not in sys.argv:
        log.append(("refresh_pool", 0, "已存在当日池，跳过（--refresh 强制重跑）"))
    else:
        rc, out = run(["tools/refresh_pool.py"])
        log.append(("refresh_pool", rc, out.strip().splitlines()[:6]))

    if not fast:
        rc, out = run(["tools/pick_stocks.py", "--top", "8", "--limit", "150"])
        log.append(("pick_stocks", rc, out.strip().splitlines()[:10]))

    pool_f = latest("data/pool-2*.json")
    if not pool_f or "pool-review" in pool_f:
        print("NIGHT_FLOW FAIL: 无 pool 文件")
        return 1
    pool = load(os.path.relpath(pool_f, ROOT), {})
    picks = pool.get("picks") or []

    # 多策略候选并入
    pk_f = latest("data/picks-*.json")
    extra = []
    if pk_f:
        for p in (load(os.path.relpath(pk_f, ROOT), {}).get("picks") or [])[:6]:
            if p.get("symbol") not in {x["symbol"] for x in picks}:
                extra.append({"symbol": p["symbol"], "name": p.get("name"), "price": p.get("price"),
                              "strategy": p.get("strategy")})

    acct = load("data/account-state.json", {})
    total = acct.get("total_assets") or 0
    cash = acct.get("cash") or 0
    snap = load(f"data/snapshot-{D}.json", {}) or load(os.path.relpath(latest("data/snapshot-*.json"), ROOT), {})
    rows = snap.get("rows") or []
    row_by = {r.get("symbol"): r for r in rows}
    breadth_up = sum(1 for r in rows if (r.get("pct") or 0) > 0) > sum(1 for r in rows if (r.get("pct") or 0) <= 0)
    xh = latest("data/index-tech-crosscheck.json")
    ic = load(os.path.relpath(xh, ROOT), {}) if xh else {}
    idx_above = bool(ic.get("sh000001", {}).get("close") and ic["sh000001"].get("ma20")
                     and ic["sh000001"]["close"] >= ic["sh000001"]["ma20"])

    ctx = {"total_assets": total, "cash_pct": (cash / total * 100) if total else 0,
           "positions": len(acct.get("positions") or []), "breadth_up": breadth_up, "index_above_ma20": idx_above}

    # 打分
    items = []
    for p in picks:
        r = row_by.get(p["symbol"]) or {}
        items.append({"symbol": p["symbol"], "name": p.get("name"), "price": p.get("price"),
                      "pct": r.get("pct") or 0, "pe": r.get("pe") or 0,
                      "ma20": p.get("ma20"), "ma60": p.get("ma60"),
                      "ma20_slope": p.get("ma20_slope") or 0, "vr": p.get("vr") or 0,
                      "vol20": p.get("vol20") or 0, "mom20": p.get("mom20") or 0})
    for e in extra:
        r = row_by.get(e["symbol"]) or {}
        items.append({"symbol": e["symbol"], "name": e.get("name"), "price": e.get("price"),
                      "pct": r.get("pct") or 0, "pe": r.get("pe") or 0,
                      "ma20": None, "ma60": None, "ma20_slope": 0, "vr": 0, "vol20": 0, "mom20": 0})

    cmd = ["tools/pool_review.py", "set"]
    for it in items:
        for role in ("润策", "润研", "润量", "润控"):
            sc, note = score_role(role, it, ctx)
            rc_s, out_s = run(cmd + ["--role", role, "--symbol", it["symbol"], "--name", it.get("name") or "",
                                     "--score", str(sc), "--note", note], timeout=120)
            if rc_s != 0:
                log.append((f"score {role} {it['symbol']}", rc_s, out_s.strip().splitlines()[:3]))

    rc, out = run(["tools/pool_review.py", "apply"])
    log.append(("pool_review apply", rc, out.strip()))

    rc, out = run(["tools/make_plan.py"])
    log.append(("make_plan", rc, out.strip().splitlines()[:2]))

    # 夜间报告
    review = load(f"data/pool-review-{D}.json", {})
    items_r = review.get("items") or {}
    grades = {"A": [], "B": [], "C": []}
    for sym, v in items_r.items():
        grades.setdefault(v.get("grade") or "C", []).append(f"{v.get('name')}({v.get('composite')})")
    lines = [f"# 晚间流程 · {D}", "",
             "> 由 `tools/night_flow.py` 自动生成（规则化打分，可人工覆盖）", "",
             f"- 股票池：{len(picks)} 只（+多策略 {len(extra)} 只）",
             f"- 评级：**A {len(grades.get('A', []))} / B {len(grades.get('B', []))} / C {len(grades.get('C', []))}**",
             f"- A级：{'、'.join(grades.get('A', [])) or '无'}",
             f"- B级：{'、'.join(grades.get('B', [])) or '无'}",
             f"- C级：{'、'.join(grades.get('C', [])) or '无'}",
             f"- 指数：{'站上MA20' if idx_above else 'MA20下方'}｜宽度：{'偏强' if breadth_up else '偏弱'}",
             f"- 账户：总资产 {total}、现金 {cash}（{ctx['cash_pct']:.1f}%）、持仓 {ctx['positions']} 只",
             ""]
    for k, rc2, out2 in log:
        lines.append(f"- `{k}` rc={rc2}｜{out2[0] if out2 else ''}")
    lines += ["", f"下一步：次日计划 `data/plan-<下一交易日>.json`（本脚本已生成）"]
    open(os.path.join(ROOT, "reports", f"night-{D}.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("NIGHT_FLOW OK " + "; ".join(f"{k}={'ok' if rc2 == 0 else 'fail'}" for k, rc2, _ in log))
    return 0


if __name__ == "__main__":
    sys.exit(main())