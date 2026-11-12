#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机看板：聚合账户/持仓/计划/告警/风控/财务 → 本地 HTTP 看板（127.0.0.1:8799）。
用法:
  python3 tools/dashboard.py            # 前台服务
  python3 tools/dashboard.py --build    # 只生成 static/dashboard.html
"""
import datetime as dt
import glob
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_QCACHE = {"t": 0, "data": {}}


def load(p, d=None):
    try:
        with open(os.path.join(ROOT, p), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d if d is not None else {}


def latest(pat):
    fs = sorted(glob.glob(os.path.join(ROOT, pat)))
    return fs[-1] if fs else None


def _quotes(syms):
    """实时行情（10 秒缓存）；盘中用 Sina/腾讯，收盘后返回收盘价。"""
    import time
    now = time.time()
    if now - _QCACHE["t"] < 10 and all(x in _QCACHE["data"] for x in syms):
        return _QCACHE["data"]
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import market
        q = market.quote(list(syms))
        if q:
            _QCACHE["data"] = q
            _QCACHE["t"] = now
        return q
    except Exception:
        return _QCACHE["data"] or {}


def _pool():
    """仓位池：持仓 + 候选 + 防守组合 + 备选，带标签、评级与实时价。"""
    grade_map = {}
    pf = sorted(glob.glob(os.path.join(ROOT, "data", "pool-*.json")))
    pf = [x for x in pf if "pool-review" not in x]
    if pf:
        for p in (json.load(open(pf[-1], encoding="utf-8")).get("picks") or []):
            grade_map[p["symbol"]] = (p.get("grade"), p.get("composite"))
    hgf = sorted(glob.glob(os.path.join(ROOT, "data", "holdings-grade-*.json")))
    if hgf:
        for x in (json.load(open(hgf[-1], encoding="utf-8")).get("holdings") or []):
            grade_map[x["symbol"]] = (x.get("grade"), x.get("composite"))
    tdx = load("data/tdx-pool.json", {}) or {}
    wl = load("data/watchlist.json", {}) or {}
    cand_syms = {c.get("symbol") for c in wl.get("candidates") or []}
    acct = load("data/account-state.json", {}) or {}
    held = {p.get("symbol") for p in acct.get("positions") or []}
    items, seen = [], set()
    for st in tdx.get("stocks") or []:
        sym = st.get("symbol")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        tag = st.get("tag") or ""
        g = (grade_map.get(sym) or (None, None))[0]
        if sym in held:
            tag = "持仓"
        elif g == "A":
            tag = "最佳"
        elif g == "B":
            tag = "推荐"
        elif g == "C":
            tag = "候选"
        elif sym in cand_syms:
            tag = "候选"
        items.append({"symbol": sym, "name": st.get("name"), "tag": tag})
    for p in acct.get("positions") or []:
        if p.get("symbol") not in seen:
            items.append({"symbol": p.get("symbol"), "name": p.get("name"), "tag": "持仓"})
    # 参考价：持仓=成本价；候选/推荐=计划买入价（plan 优先，其次 watchlist zone_top）
    plan_buy = {}
    pf2 = latest("data/plan-*.json")
    if pf2:
        for r in (json.load(open(pf2, encoding="utf-8")).get("rules") or []):
            if r.get("action") == "buy_ready" and r.get("price") is not None:
                plan_buy[r["symbol"]] = r["price"]
    for c in wl.get("candidates") or []:
        if c.get("zone_top") is not None:
            plan_buy.setdefault(c["symbol"], c["zone_top"])
    # 兜底：用池子评分文件的 MA20/现价 计算计划买价
    pfiles = [x for x in sorted(glob.glob(os.path.join(ROOT, "data", "pool-*.json"))) if "pool-review" not in x]
    if pfiles:
        for pk in (json.load(open(pfiles[-1], encoding="utf-8")).get("picks") or []):
            if pk.get("ma20") and pk.get("price"):
                plan_buy.setdefault(pk["symbol"], round(min(pk["ma20"], pk["price"] * 1.03), 2))
    pos_cost = {p.get("symbol"): p.get("cost") for p in acct.get("positions") or []}

    q = _quotes([x["symbol"] for x in items])
    for it in items:
        d = q.get(it["symbol"]) or {}
        it.update({"price": d.get("price"), "pct": d.get("pct"), "time": d.get("time")})
        pos = next((x for x in (acct.get("positions") or []) if x.get("symbol") == it["symbol"]), None)
        it["qty"] = pos.get("qty") if pos else 0
        it["pnl_pct"] = pos.get("pnl_pct") if pos else None
        if it["symbol"] in pos_cost:
            it["ref_price"] = pos_cost[it["symbol"]]
            it["ref_label"] = "成本"
        else:
            it["ref_price"] = plan_buy.get(it["symbol"])
            it["ref_label"] = "计划买价"
        g = grade_map.get(it["symbol"])
        it["grade"], it["composite"] = (g if g else (None, None))
    order = {"持仓": 0, "候选": 1, "防守组合": 2, "备选": 3}
    items.sort(key=lambda x: (order.get(x["tag"], 9), -(x.get("price") or 0)))
    return items


def _history():
    out = []
    fp = os.path.join(ROOT, "data", "account-history.jsonl")
    if os.path.exists(fp):
        for ln in open(fp, encoding="utf-8"):
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def _strategy_perf():
    f = os.path.join(ROOT, "data", "pool-eval.json")
    if not os.path.exists(f):
        return []
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        return []
    bs = d.get("by_strategy") or {}
    return [[k, v.get("n"), v.get("win_t1"), f"{v.get('win_rate')}%",
             f"{v.get('avg_t1_high_pct')}%"] for k, v in sorted(bs.items(), key=lambda kv: -(kv[1].get("win_rate") or 0))]


def _retro():
    f = latest("reports/retro-*.md") or latest("reports/daily-*.md") or latest("reports/team-*.md")
    if not f:
        return {"title": "暂无复盘", "body": ""}
    txt = open(f, encoding="utf-8").read()
    lines = [x.strip() for x in txt.splitlines() if x.strip()]
    title = lines[0].lstrip("# ").strip() if lines else os.path.basename(f)
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from export_reports import md_to_html
        body_html = md_to_html("\n".join(lines[1:40]))
    except Exception:
        body_html = "<br>".join(lines[1:14])
    return {"title": title, "html": body_html, "file": os.path.basename(f)}


def _warnings(plan, name_by):
    """按次日计划 + 实时价计算是否触达阈值；触达返回带数量的动作。"""
    import datetime as _dt
    now = _dt.datetime.now()
    in_session = now.weekday() < 5 and ((9 <= now.hour < 12) or (13 <= now.hour < 15)) and not (now.hour == 11 and now.minute > 30)
    items, triggered = [], []
    q = _quotes([r.get("symbol") for r in plan.get("rules") or [] if r.get("symbol") and r.get("symbol") != "sh000001"])
    for r in plan.get("rules") or []:
        sym = r.get("symbol")
        if not sym or sym == "sh000001":
            continue
        d = q.get(sym) or {}
        px = d.get("price")
        if not px:
            continue
        val = d.get("pct") if r.get("field") == "pct" else px
        threshold = r.get("price")
        if val is None or threshold is None:
            continue
        hit = (val <= threshold) if r.get("op") == "<=" else (val >= threshold)
        floor = r.get("floor")
        if hit and floor is not None and px < floor:
            hit = False
        code = sym[2:]
        nm = name_by.get(sym) or ""
        side = r.get("side") or ("buy" if r.get("action") == "buy_ready" else "sell")
        entry = {"id": r.get("id"), "symbol": sym, "disp": f"{nm}({code})" if nm else sym,
                 "action": r.get("action"), "side": side, "qty": r.get("qty"),
                 "threshold": threshold, "current": val, "hit": bool(hit)}
        items.append(entry)
        if hit:
            triggered.append(entry)
    return {"in_session": in_session, "items": items, "triggered": triggered}


def _plan_label(plan_date):
    if not plan_date:
        return "暂无计划"
    today = dt.date.today().isoformat()
    if plan_date == today:
        return "今日计划"
    try:
        pd = dt.date.fromisoformat(plan_date)
        if pd > dt.date.today():
            return "次日计划"
    except Exception:
        pass
    return "历史计划"


def snapshot():
    a = load("data/account-state.json")
    plan_f = latest("data/plan-*.json")
    plan = load(os.path.relpath(plan_f, ROOT)) if plan_f else {}
    alerts_f = os.path.join(ROOT, "data", f"alerts-{dt.date.today()}.jsonl")
    alerts = []
    if os.path.exists(alerts_f):
        for ln in open(alerts_f, encoding="utf-8"):
            try:
                alerts.append(json.loads(ln))
            except Exception:
                pass
    astate = load(f"data/alert-state-{dt.date.today()}.json", {})
    risk_f = latest("data/risk-*.json")
    risk = load(os.path.relpath(risk_f, ROOT)) if risk_f else {}
    mv = a.get("market_value") or sum((p.get("market_value") or 0) for p in a.get("positions") or [])
    try:
        pool_now = _pool()
        qmap = {x["symbol"]: x.get("price") for x in pool_now}
        mv_live = 0.0
        for p in a.get("positions") or []:
            px = qmap.get(p.get("symbol")) or p.get("price") or 0
            mv_live += (p.get("qty") or 0) * px
        if mv_live:
            mv = round(mv_live, 2)
            total_live = round((a.get("cash") or 0) + mv, 2)
            a["total_assets"] = total_live
            a["position_pct"] = round(mv / total_live * 100, 2) if total_live else 0
            # 实时盈亏：总盈亏=(现价-成本)*股数；当日盈亏=(现价-昨收)*股数
            qmap2 = {}
            try:
                q2 = _quotes([p.get("symbol") for p in a.get("positions") or []])
                qmap2 = q2
            except Exception:
                qmap2 = {}
            tot, day = 0.0, 0.0
            for p in a.get("positions") or []:
                qq = qmap2.get(p.get("symbol")) or {}
                px = qq.get("price") or qmap.get("price")
                prev = qq.get("prev_close")
                if px and p.get("cost"):
                    tot += (px - p["cost"]) * (p.get("qty") or 0)
                if px and prev:
                    day += (px - prev) * (p.get("qty") or 0)
            a["total_pnl"] = round(tot, 2)
            a["day_pnl"] = round(day, 2)
            a["live"] = True
            a["live_time"] = dt.datetime.now().strftime("%H:%M:%S")
    except Exception:
        pass
    hg = {}
    hgf = sorted(glob.glob(os.path.join(ROOT, "data", "holdings-grade-*.json")))
    if hgf:
        for x in (json.load(open(hgf[-1], encoding="utf-8")).get("holdings") or []):
            hg[x["symbol"]] = x
    hold_grade = hg
    pos = [{"name": p.get("name"), "qty": p.get("qty"), "cost": p.get("cost"),
            "price": p.get("price"), "pnl": p.get("pnl"), "pnl_pct": p.get("pnl_pct"),
            "grade": (hold_grade.get(p.get("symbol")) or {}).get("grade"),
            "composite": (hold_grade.get(p.get("symbol")) or {}).get("composite")}
           for p in a.get("positions") or []]
    snap_f2 = latest("data/snapshot-*.json")
    name_by = {}
    if snap_f2:
        for r in (load(os.path.relpath(snap_f2, ROOT)).get("rows") or []):
            name_by[r.get("symbol")] = r.get("name")
    for p in (a.get("positions") or []):
        name_by.setdefault(p.get("symbol"), p.get("name"))

    def disp(sym):
        code = (sym or "")[2:]
        nm = name_by.get(sym) or ""
        return f"{nm}({code})" if nm else (sym or "")

    warn = _warnings(plan, name_by if 'name_by' in dir() else {})
    buys = [r for r in plan.get("rules") or [] if r.get("action") == "buy_ready"]
    sells = [r for r in plan.get("rules") or [] if r.get("action") in ("sell_ready", "emergency_sell")]
    port = (risk.get("objects") or {}).get("portfolio_12") or {}
    # 个人财务
    prof = load("data/personal/profile.json", {})
    liabs = prof.get("liabilities") or []
    debt = round(sum((x.get("balance") or 0) for x in liabs), 2)
    assets_total = prof.get("assets_total") or (a.get("total_assets") or 0)
    emerg = prof.get("emergency_fund") or {}
    # 数据新鲜度
    def fdate(pat):
        f = latest(pat)
        return os.path.basename(f).split("-")[-3:] if f else None
    snap_f = latest("data/snapshot-*.json")
    sysinfo = {
        "snapshot": os.path.basename(snap_f)[9:-5] if snap_f else "—",
        "industry": (os.path.basename(latest("data/industry-*.json")) or "—")[9:-5] if latest("data/industry-*.json") else "—",
        "risk": (os.path.basename(risk_f)[5:-5] if risk_f else "—"),
        "plan": plan.get("date") or "—",
        "guard": _alive("session_guard.py"),
        "bridge": _alive("mika_bridge.py"),
        "dashboard": True,
    }
    fills = [{"name": f.get("name"), "side": f.get("side"), "price": f.get("price"),
              "qty": f.get("qty"), "time": f.get("time")} for f in (a.get("today_fills") or [])]
    alert_rows = [{"ts": x.get("ts"), "label": x.get("label"), "price": x.get("value"),
                   "action": x.get("action")} for x in alerts[-5:]]
    return {
        "time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "account": {"total": a.get("total_assets"), "cash": a.get("cash"),
                    "data_date": a.get("as_of"),
                    "is_today": a.get("as_of") == dt.date.today().isoformat(),
                    "live": a.get("live"), "live_time": a.get("live_time"),
                    "pnl_label": ("今日盈亏（实时）" if a.get("live") else "今日盈亏") if a.get("as_of") == dt.date.today().isoformat() else "昨日盈亏（按收盘价）",
                    "fill_label": "今日成交" if a.get("as_of") == dt.date.today().isoformat() else "昨日成交",
                    "market_value": mv, "day_pnl": a.get("day_pnl"),
                    "total_pnl": a.get("total_pnl"), "position_pct": a.get("position_pct"),
                    "as_of": a.get("as_of")},
        "positions": pos,
        "plan": {"date": plan.get("date"), "label": _plan_label(plan.get("date")), "rule_count": len(plan.get("rules") or []),
                 "buys": [{"sym": r.get("symbol"), "disp": disp(r.get("symbol")), "price": r.get("price"), "qty": r.get("qty")} for r in buys],
                 "sells": [{"id": r.get("id"), "sym": r.get("symbol"), "disp": disp(r.get("symbol")),
                            "price": r.get("price"), "qty": r.get("qty"),
                            "action": r.get("action")} for r in sells]},
        "alerts": {"count": len(alerts), "processed": astate.get("processed"),
                   "last": alerts[-1].get("label") if alerts else None},
        "risk": {"vol": port.get("annual_volatility_pct"), "mdd": port.get("max_drawdown_pct"),
                 "var95": port.get("var95_daily_pct"), "cvar95": port.get("cvar95_daily_pct"),
                 "stop_budget": ((risk.get("checks") or {}).get("A_all_stops_budget") or {}).get("computed_total_pct")},
        "watch": _watch_alive(),
        "finance": {"debt": debt, "assets": assets_total,
                    "net": round((assets_total or 0) - debt, 2),
                    "emergency": emerg.get("current") or 0,
                    "emergency_target": emerg.get("target") or 18043.32,
                    "pct": (round((emerg.get("current") or 0) / (emerg.get("target") or 18043.32) * 100, 1))},
        "system": sysinfo,
        "fills": fills,
        "alert_rows": alert_rows,
        "pool": _pool(),
        "history": _history(),
        "strategy": _strategy_perf(),
        "retro": _retro(),
        "warn": warn,
    }


def _alive(name):
    try:
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    parts = [x for x in open(f"/proc/{pid}/cmdline", "rb").read().decode("utf8", "ignore").split("\0") if x]
                except Exception:
                    continue
                if any(x.endswith(name) for x in parts):
                    return True
    except Exception:
        pass
    return False


def _watch_alive():
    try:
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    parts = [x for x in open(f"/proc/{pid}/cmdline", "rb").read().decode("utf8", "ignore").split("\0") if x]
                except Exception:
                    continue
                if len(parts) >= 2 and "watch_" in parts[1]:
                    return True
    except Exception:
        pass
    return False


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0f1115">
<title>润锋看板</title>
<link rel="manifest" href="/manifest.webmanifest">
<style>
body{margin:0;background:#0f1115;color:#e8e8e8;font:15px/1.5 -apple-system,"PingFang SC",sans-serif;padding:14px 14px 40px}
h1{font-size:19px;margin:0 0 4px;color:#f5c542;text-align:center}
#warnbar{border-radius:14px;padding:12px 14px;margin:0 0 12px;font-weight:700;font-size:16px}
.w-red{background:#5a1010;color:#ff4d4d;border:1px solid #ff4d4d;animation:blink .8s steps(2,start) infinite}
.w-amber{background:#4a3a08;color:#f5c542;border:1px solid #f5c542}
.w-ok{background:#12261a;color:#58d68d;border:1px solid #2e7d32}
@keyframes blink{50%{opacity:.35}}
.sub{color:#888;font-size:12px;margin-bottom:14px;text-align:center}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.card{background:#171a21;border:1px solid #232833;border-radius:14px;padding:12px}
.card h2{font-size:12px;color:#8b93a7;margin:0 0 8px;font-weight:500}
.big{font-size:24px;font-weight:700}
.pos{color:#ff6b6b}.neg{color:#58d68d}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:6px 4px;border-bottom:1px solid #232833;text-align:left}
th{color:#8b93a7;font-weight:500}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;background:#232833}
.ok{color:#58d68d}.warn{color:#f5c542}
#retro h2{font-size:14px;color:#8b93a7;margin:10px 0 4px}#retro h3{font-size:13px;color:#8b93a7;margin:8px 0 2px}#retro ul{padding-left:18px;margin:4px 0}#retro li{margin:3px 0}</style></head><body>
<h1>润锋交易系统</h1><div id="warnbar" class="w-ok">加载中…</div><div class="sub" id="ts">加载中…</div>
<div class="grid">
 <div class="card"><h2>总资产</h2><div class="big" id="total">—</div><div class="sub" id="pnl"></div></div>
 <div class="card"><h2>仓位 / 现金</h2><div class="big" id="pospct">—</div><div class="sub" id="cash"></div></div>
 <div class="card"><h2 id="pnlLabel">今日盈亏</h2><div class="big" id="daypnl">—</div><div class="sub" id="totalpnl"></div></div>
 <div class="card"><h2>监控</h2><div class="big" id="watch">—</div><div class="sub" id="alerts"></div>
<div style="display:flex;gap:8px;margin-top:8px">
<button id="btnStart" onclick="ctl('start')" style="flex:1;padding:8px 10px;border-radius:10px;border:0;background:#2e7d32;color:#fff">启动</button>
<button id="btnStop" onclick="ctl('stop')" style="flex:1;padding:8px 10px;border-radius:10px;border:0;background:#8e2b2b;color:#fff">停止</button>
</div></div>
</div>
<div class="card" style="margin-top:10px"><h2>仓位池 <span class="sub" style="font-size:11px">实时 · A/B/C 评级</span></h2><table id="pool"><thead><tr><th>名称</th><th>标签</th><th>等级</th><th>现价</th><th>涨跌</th><th>成本/计划价</th><th>持仓</th></tr></thead><tbody></tbody></table></div>
<div class="card" style="margin-top:10px"><h2>持仓</h2><table id="pos"><thead><tr><th>名称</th><th>数量</th><th>成本</th><th>现价</th><th>盈亏</th><th>等级</th></tr></thead><tbody></tbody></table></div>
<div class="card" style="margin-top:10px"><h2><span id="planlabel">计划</span> <span class="tag" id="plandate"></span></h2><div id="planbox">—</div></div>
<div class="card" style="margin-top:10px"><h2>风控</h2><div id="riskbox" class="sub">—</div></div>
<div class="card" style="margin-top:10px"><h2>收益曲线</h2><div id="chart"></div></div>
<div class="card" style="margin-top:10px"><h2>策略表现 <span class="sub" style="font-size:11px">次日最高≥+1% 为成功</span></h2><div id="stratbox" class="sub">—</div></div>
<div class="card" style="margin-top:10px"><h2>复盘摘要 <span class="tag" id="retrofile"></span></h2><div id="retro" style="font-size:13px;line-height:1.6"></div></div>
<div class="card" style="margin-top:10px"><h2>财务（个人）</h2><div id="finbox" class="sub">—</div></div>
<div class="card" style="margin-top:10px"><h2>系统状态</h2><div id="sysbox" class="sub">—</div></div>
<div class="card" style="margin-top:10px"><h2 id="fillLabel">今日成交</h2><div id="fillbox" class="sub">—</div></div>
<div class="card" style="margin-top:10px"><h2>最近告警</h2><div id="alertbox" class="sub">—</div></div>
<div class="sub" style="margin-top:14px">每 15 秒自动刷新 · 数据仅本机</div>
<script>
function tbl(headers, rows){
  let h='<table><thead><tr>'+headers.map(x=>`<th>${x}</th>`).join('')+'</tr></thead><tbody>';
  rows.forEach(r=>{h+='<tr>'+r.map(c=>`<td>${c??''}</td>`).join('')+'</tr>'});
  return h+'</tbody></table>';
}
async function tick(){
 const r=await fetch('/data.json?t='+Date.now()); const d=await r.json();
 document.getElementById('ts').textContent='更新 '+d.time;
 const wb=document.getElementById('warnbar'); const w=d.warn||{}; const trg=w.triggered||[];
 if(trg.length){
   const txt=trg.map(x=>`${x.side==='buy'?'买入':'卖出'} ${x.disp} ${x.qty||''} 股`).join(' ｜ ');
   wb.className=w.in_session?'w-red':'w-amber';
   wb.textContent=(w.in_session?'⚠ 已触达阈值：':'⏸ 非交易时段·触及阈值仅供预览：')+txt;
 } else {
   wb.className='w-ok';
   wb.textContent='未预警：暂无标的触及阈值';
 }
 document.getElementById('total').textContent='¥'+(d.account.total??'—');
 const dp=d.account.day_pnl; const e=document.getElementById('daypnl');
 e.textContent=(dp==null?'—':(dp>0?'+':'')+dp); e.className='big '+(dp>=0?'pos':'neg');
 document.getElementById('pnl').textContent='市值 ¥'+(d.account.market_value??'—');
 document.getElementById('totalpnl').textContent='累计 '+(d.account.total_pnl??'—')+(d.account.is_today?'':' · 数据 '+ (d.account.data_date||''))+(d.account.live?(' · 实时 '+d.account.live_time):'');
 document.getElementById('pnlLabel').textContent=d.account.pnl_label||'今日盈亏';
 document.getElementById('fillLabel').textContent=d.account.fill_label||'今日成交';
 document.getElementById('pospct').textContent=(d.account.position_pct??'—')+'%';
 document.getElementById('cash').textContent='可用 ¥'+(d.account.cash??'—');
 document.getElementById('watch').innerHTML=d.watch?'<span class="ok">运行中</span>':'<span class="warn">未运行</span>';
 document.getElementById('alerts').textContent='今日告警 '+(d.alerts.count||0)+' / 已处理 '+(d.alerts.processed||0);
 const tb=document.querySelector('#pos tbody'); tb.innerHTML='';
 (d.positions||[]).forEach(p=>{const tr=document.createElement('tr');
  tr.innerHTML=`<td>${String(p.name||'').replace(/\\s+/g,'')}</td><td>${p.qty??''}</td><td>${p.cost??''}</td><td>${p.price??''}</td><td class="${(p.pnl||0)>=0?'pos':'neg'}">${p.pnl??''}</td>
  <td>${p.grade?`<span style="font-weight:700;color:${p.grade==='A'?'#ff4d4d':p.grade==='B'?'#f5c542':'#58d68d'}">${p.grade}</span>`:'—'}</td>`;tb.appendChild(tr)});
 document.getElementById('planlabel').textContent=(d.plan&&d.plan.label)||'计划';
 document.getElementById('plandate').textContent=(d.plan&&d.plan.date)||'—';
 const prow=[];
 (d.plan.buys||[]).forEach(b=>prow.push(['<span class="pos">买入</span>', b.disp||b.sym, '≤'+b.price, b.qty]));
 (d.plan.sells||[]).forEach(s=>{const lab=(s.id||'').includes('rebound')?'减仓':(s.action==='emergency_sell'?'急卖':'止损');
  const pv=s.action==='emergency_sell'?('日内≤'+(-s.price)+'%'):s.price;
  const cls=s.action==='emergency_sell'?'warn':'neg';
  prow.push([`<span class="${cls}">${lab}</span>`, s.disp||s.sym, pv, s.qty])});
 document.getElementById('planbox').innerHTML=prow.length?tbl(['类型','标的','触发价','数量'],prow):'无';
 const pt=document.querySelector('#pool tbody'); pt.innerHTML='';
 (d.pool||[]).forEach(x=>{const tr=document.createElement('tr');
  const pct=(x.pct==null?'—':(x.pct>0?'+':'')+x.pct+'%');
  const cls=(x.pct||0)>=0?'pos':'neg';
  tr.innerHTML=`<td>${x.name||''}<br><span class="sub" style="font-size:11px">${(x.symbol||'').slice(2)}</span></td>
  <td><span class="tag">${x.tag||''}</span></td>
  <td>${x.grade?`<span style="font-weight:700;color:${x.grade==='A'?'#ff4d4d':x.grade==='B'?'#f5c542':'#58d68d'}">${x.grade}</span>`:'—'}</td>
  <td>${x.price??'—'}</td><td class="${cls}">${pct}</td><td>${x.ref_price!=null?Number(x.ref_price).toFixed(3):'—'}</td><td>${x.qty||''}</td>`;
  pt.appendChild(tr)});
 const hs=d.history||[]; const box=document.getElementById('chart');
 if(hs.length>1){const w=440,h=120,pad=6;
  const ts=hs.map(x=>x.total||0); const mn=Math.min(...ts)*0.995, mx=Math.max(...ts)*1.005;
  const xs=i=>pad+i*(w-2*pad)/(hs.length-1); const ys=v=>h-pad-(v-mn)/(mx-mn||1)*(h-2*pad);
  let pts=hs.map((x,i)=>`${xs(i).toFixed(1)},${ys(x.total||0).toFixed(1)}`).join(' ');
  let dots=hs.map((x,i)=>`<circle cx="${xs(i).toFixed(1)}" cy="${ys(x.total||0).toFixed(1)}" r="2.5" fill="#f5c542"/>`).join('');
  let labels=hs.map((x,i)=>`<text x="${xs(i).toFixed(1)}" y="${h-1}" font-size="9" fill="#888" text-anchor="middle">${(x.date||'').slice(5)}</text>`).join('');
  box.innerHTML=`<svg viewBox="0 0 ${w} ${h+8}" width="100%"><polyline points="${pts}" fill="none" stroke="#58d68d" stroke-width="2"/>${dots}${labels}</svg>
  <div class="sub">${hs[0].total} → ${hs[hs.length-1].total}</div>`;}
 const sb=d.strategy||[]; document.getElementById('stratbox').innerHTML=sb.length?
  tbl(['策略','样本','成功','成功率','均次日高'], sb):'暂无已实现样本（需积累 T+1 数据）';
 const rt=d.retro||{}; document.getElementById('retrofile').textContent=rt.file||'';
 document.getElementById('retro').innerHTML=rt.html||'—';
 const f=d.finance||{}; document.getElementById('finbox').innerHTML=tbl(['项目','数值'],[
  ['净资产', f.net??'—'], ['负债', f.debt??'—'],
  ['应急金', (f.emergency??'—')+' / '+(f.emergency_target??'—')],
  ['应急金进度', (f.pct??'—')+'%']]);
 const si=d.system||{}; const on=v=>v?'<span class=ok>运行</span>':'<span class=warn>停止</span>';
 document.getElementById('sysbox').innerHTML=tbl(['项目','状态'],[
  ['快照', si.snapshot], ['板块', si.industry], ['风控', si.risk], ['计划', si.plan],
  ['监控', d.watch?on(true):on(false)], ['守护', on(si.guard)], ['桥接', on(si.bridge)]]);
 const fb=d.fills||[]; document.getElementById('fillbox').innerHTML=fb.length?tbl(['时间','名称','方向','价格×数量'],
  fb.map(x=>[x.time, String(x.name||'').replace(/\\s+/g,''), x.side==='buy'?'<span class="pos">买入</span>':'<span class="neg">卖出</span>', `${x.price}×${x.qty}`])):'今日无成交';
 const amap={buy_ready:'买入提醒',sell_ready:'卖出提醒',emergency_sell:'紧急卖出',risk_off:'风险关闭',notify:'提示',data_issue:'数据异常'};
 const ab=d.alert_rows||[]; document.getElementById('alertbox').innerHTML=ab.length?tbl(['时间','规则','价格','动作'],
  ab.map(x=>[String(x.ts||'').slice(11), x.label, x.price, amap[x.action]||x.action])):'无告警';
 const k=d.risk||{}; document.getElementById('riskbox').innerHTML=tbl(['指标','数值'],[
  ['组合波动', (k.vol??'—')+'%'], ['MaxDD', (k.mdd??'—')+'%'],
  ['VaR95', (k.var95??'—')+'%'], ['CVaR95', (k.cvar95??'—')+'%'], ['全止损', (k.stop_budget??'—')+'%']]);
}
async function ctl(a){const r=await fetch('/api/watch?action='+a);const j=await r.json();
 alert(j.msg); tick();}
tick(); setInterval(tick,15000);
</script></body></html>"""

MANIFEST = {"name": "润锋看板", "short_name": "润锋", "start_url": "/", "display": "standalone",
            "background_color": "#0f1115", "theme_color": "#0f1115", "icons": []}


_CACHE = {"snap": None, "t": 0, "err": None}
_CACHE_LOCK = __import__("threading").Lock()


def _warm():
    import threading, time as _t
    while True:
        try:
            d = snapshot()
            with _CACHE_LOCK:
                _CACHE["snap"] = d
                _CACHE["t"] = _t.time()
                _CACHE["err"] = None
        except Exception as e:
            with _CACHE_LOCK:
                _CACHE["err"] = f"{type(e).__name__}: {e}"
        _t.sleep(8)


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        b = body.encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path.startswith("/api/watch"):
            import subprocess
            from urllib.parse import parse_qs, urlparse
            act = (parse_qs(urlparse(self.path).query).get("action") or [""])[0]
            msg = ""
            if act == "start":
                if _watch_alive():
                    msg = "already running"
                else:
                    plan = latest(f"data/plan-{__import__('datetime').date.today():%Y-%m-%d}.json") or latest("data/plan-*.json")
                    if not plan:
                        msg = "no plan available"
                    else:
                        subprocess.Popen(["bash", "tools/watch_loop.sh", os.path.relpath(plan, ROOT)], cwd=ROOT,
                                         stdout=open("/tmp/watch_loop.log", "ab"), stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
                        msg = "started"
            elif act == "stop":
                import signal
                killed = 0
                for pid in os.listdir("/proc"):
                    if not pid.isdigit():
                        continue
                    try:
                        parts = [x for x in open(f"/proc/{pid}/cmdline", "rb").read().decode("utf8", "ignore").split("\0") if x]
                    except Exception:
                        continue
                    if len(parts) >= 2 and parts[0] in ("bash", "python3") and "watch_" in parts[1]:
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                            killed += 1
                        except Exception:
                            pass
                msg = f"stopped {killed}"
            self._send(200, json.dumps({"ok": True, "msg": msg, "watch": _watch_alive()}), "application/json")
            return
        if self.path.startswith("/data.json"):
            with _CACHE_LOCK:
                snap = _CACHE["snap"]
                err = _CACHE["err"]
            if snap is None:
                self._send(200, json.dumps({"time": "", "account": {}, "pool": [], "positions": [], "plan": {},
                                             "alerts": {}, "risk": {}, "finance": {}, "system": {},
                                             "fills": [], "alert_rows": [], "history": [], "retro": {},
                                             "warn": {"in_session": False, "items": [], "triggered": []},
                                             "error": err or "warmup"}, ensure_ascii=False), "application/json")
            else:
                self._send(200, json.dumps(snap, ensure_ascii=False), "application/json")
        elif self.path.startswith("/manifest.webmanifest"):
            self._send(200, json.dumps(MANIFEST, ensure_ascii=False), "application/manifest+json")
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    def log_message(self, *a):
        pass


def main():
    if "--build" in sys.argv:
        out = os.path.join(ROOT, "static", "dashboard.html")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(PAGE.replace("fetch('/data.json?t='+Date.now())", "fetch('data.json?t='+Date.now())"))
        print("BUILD", out)
        return 0
    port = 8799
    import threading
    threading.Thread(target=_warm, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"dashboard on http://127.0.0.1:{port}/")
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
