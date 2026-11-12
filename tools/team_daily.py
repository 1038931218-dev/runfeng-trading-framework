#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""团队日更流水线（2026-11-10）：抓数 → 生成各角色事实要点 → reports/team-<date>.md

顺序：snapshot（缺才抓）→ industry → brief → screen（可选，慢）→ risk_metrics → make_plan（次日）
用法: python3 tools/team_daily.py [--fast]   # --fast 跳过 screen/risk（约 5 秒）
"""
import datetime as dt
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
TODAY = dt.date.today().isoformat()


def run(cmd, timeout=300):
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa
        return 1, f"{type(e).__name__}: {e}"


def load(p):
    try:
        with open(os.path.join(ROOT, p), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}




def _risk_metric(risk, key):
    try:
        return risk["objects"]["portfolio_12"][key]
    except Exception:
        return None


def _risk_stop(risk):
    try:
        return risk["checks"]["A_all_stops_budget"]["computed_total_pct"]
    except Exception:
        return None


def conclude(brief, ind, risk):
    """规则化结论（零 token）：四个角色各给 3 行判断，供日更使用。"""
    rows = ind.get("rows") or []
    w = brief.get("breadth") or {}
    idx = (brief.get("indices") or {}).get("sh000001") or {}
    close, ma20, ma60 = idx.get("close"), idx.get("ma20"), idx.get("ma60")
    last = (brief.get("date") or "")
    out = []
    # 润策
    pos = "MA20 上方" if close and ma20 and close >= ma20 else "MA20 下方"
    breadth = "偏强" if (w.get("up") or 0) > (w.get("down") or 0) else "偏弱"
    out.append(("润策（宏观）", [
        f"上证 {close}，位于 {pos}（MA20 {ma20} / MA60 {ma60}）",
        f"宽度 {w.get('up')}涨/{w.get('down')}跌，中位 {w.get('median_pct')}%，成交 {w.get('amount_yi')} 亿，情绪{breadth}",
        f"结论：{'维持防守档，等站稳 MA20 再加仓' if pos == 'MA20 下方' else '可维持现有仓位，不追高'}",
    ]))
    # 润研
    if rows:
        up = [r for r in rows if r["pct"] > 0]
        strong = "、".join(f"{r['name']}{r['pct']:+.2f}%" for r in rows[:3])
        weak = "、".join(f"{r['name']}{r['pct']:+.2f}%" for r in rows[-3:])
        out.append(("润研（行业）", [
            f"49 板块：涨 {len(up)} 跌 {len(rows)-len(up)}",
            f"强势：{strong}",
            f"弱势：{weak}",
        ]))
    else:
        out.append(("润研（行业）", ["板块数据缺失，未能核实"]))
    # 润量
    out.append(("润量（量化）", [
        "筛选结果见 models/screen-<日期>.csv（收盘后生成）",
        "原则：只买主板、1 手≥600 元、mom20<18.3%、成交额≥2 亿",
    ]))
    # 润控
    if risk:
        out.append(("润控（风控）", [
            f"组合波动 {_risk_metric(risk,'annual_volatility_pct')}%，MaxDD {_risk_metric(risk,'max_drawdown_pct')}%",
            f"VaR95 {_risk_metric(risk,'var95_daily_pct')}%，CVaR95 {_risk_metric(risk,'cvar95_daily_pct')}%，全止损预算 {_risk_stop(risk)}%",
            "结论：按计划止损，不扩大单票与总仓位",
        ]))
    else:
        out.append(("润控（风控）", ["风险指标缺失，未能核实"]))
    return out

def main():
    fast = "--fast" in sys.argv
    notes, ok = [], []

    # 1) 快照（缺则抓）
    snap_path = f"data/snapshot-{TODAY}.json"
    stale_snapshot = False
    try:
        mt = os.path.getmtime(os.path.join(ROOT, snap_path))
        stale_snapshot = dt.datetime.fromtimestamp(mt).hour < 14 or (dt.datetime.fromtimestamp(mt).hour == 14 and dt.datetime.fromtimestamp(mt).minute < 55)
    except OSError:
        stale_snapshot = True
    if (not os.path.exists(os.path.join(ROOT, snap_path))) or (dt.datetime.now().hour >= 15 and stale_snapshot):
        rc, out = run([PY, "tools/market.py", "snapshot"], 240)
        ok.append(("snapshot", rc == 0, out.strip().splitlines()[-1] if out.strip() else ""))
    else:
        ok.append(("snapshot", True, "已有"))

    # 2) 行业板块
    if not os.path.exists(os.path.join(ROOT, f"data/industry-{TODAY}.json")):
        rc, out = run([PY, "tools/industry.py"], 120)
        ok.append(("industry", rc == 0, out.strip()))
    else:
        ok.append(("industry", True, "已有"))

    # 3) brief
    rc, out = run([PY, "tools/daily_brief.py"], 180)
    ok.append(("brief", rc == 0, ""))

    # 4) screen（可选）
    if not fast:
        rc, out = run([PY, "tools/screen_tech.py", "--limit", "400"], 600)
        ok.append(("screen", rc == 0, out.strip().splitlines()[-1] if out.strip() else ""))

    # 5) risk
    if not fast:
        rc, out = run([PY, "tools/risk_metrics.py"], 300)
        ok.append(("risk", rc == 0, out.strip().splitlines()[-1] if out.strip() else ""))

    # 6) 次日计划
    rc, out = run([PY, "tools/make_plan.py"], 240)
    ok.append(("plan", rc == 0, ""))

    brief = load(f"data/brief-{TODAY}.json")
    # 用实时指数覆盖 brief 里的 close（交叉表可能是上一交易日）
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import market
        live_indices = market.quote(["sh000001", "sz399001", "sz399006", "sh000688"])
        for sym, q in live_indices.items():
            if brief.get("indices", {}).get(sym) and q.get("price"):
                brief["indices"][sym]["close"] = round(q["price"], 2)
    except Exception:
        pass
    ind = load(f"data/industry-{TODAY}.json")
    risk = load(f"data/risk-{TODAY}.json")  # risk-fallback
    if not risk:
        import glob as _g
        cand = sorted(_g.glob(os.path.join(ROOT, "data", "risk-*.json")))
        if cand:
            risk = load(os.path.relpath(cand[-1], ROOT))
    rows = ind.get("rows") or []
    top = "、".join(f"{r['name']}{r['pct']:+.2f}%" for r in rows[:5]) or "—"
    bottom = "、".join(f"{r['name']}{r['pct']:+.2f}%" for r in rows[-3:]) or "—"
    w = brief.get("breadth") or {}
    idx = brief.get("indices") or {}

    lines = [f"# 团队日更 · {TODAY}", "",
             "## 润策（宏观）",
             f"- 上证 {idx.get('sh000001',{}).get('close')} / MA20 {idx.get('sh000001',{}).get('ma20')} / MA60 {idx.get('sh000001',{}).get('ma60')}",
             f"- 宽度：{w.get('up')}涨/{w.get('down')}跌 中位{w.get('median_pct')}% 涨停{w.get('limit_up')}/跌停{w.get('limit_down')} 成交{w.get('amount_yi')}亿",
             "", "## 润研（行业）",
             f"- 49 板块 涨{sum(1 for r in rows if r['pct']>0)}跌{sum(1 for r in rows if r['pct']<=0)}",
             f"- 领涨：{top}", f"- 领跌：{bottom}",
             "", "## 润量（量化）",
             f"- 筛选结果：`models/screen-{TODAY}.csv`（若已生成）",
             "", "## 润控（风控）",
             f"- 组合波动 {_risk_metric(risk,'annual_volatility_pct')}%｜MaxDD {_risk_metric(risk,'max_drawdown_pct')}%｜VaR95 {_risk_metric(risk,'var95_daily_pct')}%｜CVaR95 {_risk_metric(risk,'cvar95_daily_pct')}%",
             f"- 全止损预算：{_risk_stop(risk)}%",
             "", "## 执行摘要", ""]
    for name, good, msg in ok:
        lines.append(f"- {name}: {'OK' if good else 'FAIL'} {msg}")
    lines += ["", "> 事实数据由脚本生成；结论由脚本规则化输出，人工可覆盖。"]
    try:
        for title, items in conclude(brief, ind, risk):
            lines += ["", f"### {title}"] + [f"- {x}" for x in items]
    except Exception as e:
        lines += ["", f"（结论生成失败：{type(e).__name__}）"]

    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    out_path = os.path.join(ROOT, "reports", f"team-{TODAY}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"TEAM_DAILY {TODAY} → reports/team-{TODAY}.md")
    for name, good, msg in ok:
        print(f"  {name:9s} {'OK  ' if good else 'FAIL'} {msg}")
    return 0 if all(g for _, g, _ in ok) else 1


if __name__ == "__main__":
    sys.exit(main())
