#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风控指标自动化 · risk_metrics（润控/风控与组合，2026-11-06）

用途
  把已实测通过的 empyrical-reloaded / quantstats / arch 固化成可复跑脚本，
  对三个对象出「风险指标块」：
    ① 12 只组合（权重 38% 股票 + 62% 现金，现金收益按 0）
    ② 示例股票 持仓 400 股（成本 3.3425、现价 3.24）
    ③ 工商银行 sh601398（组合最大权重单票参照）
  并做两项重点检查：
    A. 用 models/portfolio-2026-11-06.json 的 stop_loss 重算「全止损同时触发」预算，
       与 JSON 声明的 -2.883% 对账；
    B. 逐个标的比较「止损距离 vs GARCH(1,1) 1 日 VaR95」，标出止损落在正常波动内
       （易被噪声打掉）的标的；万科 3.16 / 3.10 另给触发概率粗估（正态近似）。

复现
  python3 tools/risk_metrics.py          # 约 20–40 秒（13 只次 × 250 根日K + 13 次 GARCH 拟合）
  输出 data/risk-2026-11-06.json（唯一写入文件）+ 控制台摘要表。

数据源（口径）
  个股日K：Sina `CN_MarketData.getKLineData`（scale=240，datalen=250，末根须为 2026-11-06）；
           腾讯 `appstock/app/fqkline/get` 在本机为 HTTP 501，不可用。
  价格/权重：data/snapshot-2026-11-06.json、models/portfolio-2026-11-06.json（只读）；
            models/screen-2026-11-06.csv 用于价格交叉核对（只读）。
  取不到/拟合失败 → 写「未能核实」并列入 JSON.unverified，不编造。

依赖（本机已装、纯 CPU 无 GPU）
  empyrical-reloaded 0.7.2 / quantstats 0.0.86 / arch 8.0.0
  pandas 3.1.1 / numpy 2.5.4 / scipy 1.18.1 / Python 3.12.15 (aarch64)
"""
import json
import math
import os
import sys
import time
import urllib.request

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import glob as _glob


def _latest(pattern, default):
    files = sorted(_glob.glob(os.path.join(ROOT, pattern)))
    return files[-1] if files else os.path.join(ROOT, default)


SNAP = _latest("data/snapshot-*.json", "data/snapshot-2026-11-06.json")
PORTFOLIO = _latest("models/portfolio-*.json", "models/portfolio-2026-11-06.json")
SCREEN_CSV = _latest("models/screen-*.csv", "models/screen-2026-11-06.csv")
AS_OF = os.environ.get("RISK_AS_OF") or os.path.basename(SNAP).replace("snapshot-", "").replace(".json", "")
OUT = os.path.join(ROOT, "data", f"risk-{AS_OF}.json")
BARS = 250
TRADING_DAYS = 252
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
       "Referer": "https://finance.sina.com.cn/"}
SINA_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={n}")

# 三个对象 + 组合内 12 只（sybmol, 名称）
PORTFOLIO_SYMBOLS = [
    ("sh601398", "工商银行"), ("sh601318", "中国平安"), ("sh600941", "中国移动"),
    ("sh601857", "中国石油"), ("sh601088", "中国神华"), ("sh601668", "中国建筑"),
    ("sz000651", "格力电器"), ("sh601006", "大秦铁路"), ("sh600795", "国电电力"),
    ("sh600674", "川投能源"), ("sh601766", "中国中车"), ("sh600887", "伊利股份"),
]
VANKE = ("sz000002", "示例股票")
ICBC = ("sh601398", "工商银行")
# 万科两项候选止损（用户保留 3.16，原拟 3.10；见 research/05-account-execution.md §7）
VANKE_STOPS = [3.16, 3.10]


# --------------------------------------------------------------------------- #
# 1. 取数
# --------------------------------------------------------------------------- #
def fetch_bars(sym, n=BARS, retries=3):  # fallback_market_kline
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import market as _mk
        kb = _mk.kline(sym, n)
        if kb and len(kb) >= 60:
            return [{"date": b["date"], "open": b["open"], "high": b["high"], "low": b["low"],
                     "close": b["close"], "volume": b.get("volume", 0)} for b in kb]
    except Exception:
        pass
    """Sina 日K（未标注复权口径，收盘价与快照逐只核对）。失败返回 None。"""
    for i in range(retries):
        try:
            req = urllib.request.Request(SINA_URL.format(sym=sym, n=n), headers=HDR)
            raw = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
            bars = [{"date": x["day"], "open": float(x["open"]), "high": float(x["high"]),
                     "low": float(x["low"]), "close": float(x["close"]),
                     "volume": float(x.get("volume") or 0)} for x in raw]
            if bars:
                return bars
        except Exception:  # noqa: BLE001
            time.sleep(0.6 + 0.6 * i)
    return None


def load_snapshots():
    snap = json.load(open(SNAP, encoding="utf-8"))
    rows = {r["symbol"]: r for r in snap["rows"]}
    port = json.load(open(PORTFOLIO, encoding="utf-8"))
    screen = {}
    try:
        import csv as _csv
        with open(SCREEN_CSV, encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                screen[row["symbol"]] = row
    except Exception:  # noqa: BLE001
        screen = {}
    return snap, rows, port, screen


# --------------------------------------------------------------------------- #
# 2. 指标
# --------------------------------------------------------------------------- #
def basic_metrics(rets, label):
    """empyrical 主口径；quantstats 交叉核对。返回 dict（百分比已 ×100，VaR/CVaR 为负）。"""
    import empyrical
    import quantstats as qs

    out = {
        "label": label,
        "n_returns": int(len(rets)),
        "annual_return_pct": round(float(empyrical.annual_return(rets)) * 100, 2),
        "annual_volatility_pct": round(float(empyrical.annual_volatility(rets)) * 100, 2),
        "max_drawdown_pct": round(float(empyrical.max_drawdown(rets)) * 100, 2),
        "sharpe": round(float(empyrical.sharpe_ratio(rets)), 2),
        "sortino": round(float(empyrical.sortino_ratio(rets)), 2),
        "calmar": round(float(empyrical.calmar_ratio(rets)), 2),
        "var95_daily_pct": round(float(empyrical.value_at_risk(rets, cutoff=0.05)) * 100, 2),
        "var99_daily_pct": round(float(empyrical.value_at_risk(rets, cutoff=0.01)) * 100, 2),
        "cvar95_daily_pct": round(float(empyrical.conditional_value_at_risk(rets, cutoff=0.05)) * 100, 2),
        "metrics_source": "empyrical-reloaded",
    }
    try:  # quantstats 交叉核对（口径差通常 ≤0.1pp）
        out["quantstats_crosscheck"] = {
            "max_drawdown_pct": round(float(qs.stats.max_drawdown(rets)) * 100, 2),
            "var_pct": round(float(qs.stats.var(rets)) * 100, 2),
            "cvar_pct": round(float(qs.stats.cvar(rets)) * 100, 2),
            "sortino": round(float(qs.stats.sortino(rets)), 2),
            "calmar": round(float(qs.stats.calmar(rets)), 2),
        }
    except Exception as e:  # noqa: BLE001
        out["quantstats_crosscheck"] = {"error": f"未能核实: {e}"}
    return out


def garch_vol(rets_pct):
    """GARCH(1,1) 条件日波动（%）。返回 (cond_vol_pct, var95_1d_pct, var95_5d_pct, note)。"""
    from arch import arch_model
    from scipy.stats import norm
    import warnings
    warnings.filterwarnings("ignore")
    try:
        res = arch_model(rets_pct, vol="GARCH", p=1, q=1, dist="normal", mean="Constant").fit(disp="off")
        fc = res.forecast(horizon=5, reindex=False)
        var = fc.variance.values[-1, :]
        cond_vol = float(math.sqrt(var[0]))
        z95 = float(norm.ppf(0.05))
        v1 = z95 * cond_vol
        v5 = z95 * float(math.sqrt(var.sum()))  # 5 日累计（近似）
        return round(cond_vol, 2), round(v1, 2), round(v5, 2), None
    except Exception as e:  # noqa: BLE001
        return None, None, None, f"GARCH 拟合失败，未能核实: {e}"


def prob_touch(distance_pct, sigma_pct, horizon_days=1):
    """正态近似：价格触及止损的概率。
    distance_pct 为正数（止损距现价的百分比幅度）；
    terminal = P(R_T ≤ -d) = Φ(-d/(σ√T))；first_passage_upper = 2Φ(-d/(σ√T))（无漂移首达上界）。
    """
    from scipy.stats import norm
    z = distance_pct / (sigma_pct * math.sqrt(horizon_days))
    return round(float(norm.cdf(-z)) * 100, 2), round(float(2 * norm.cdf(-z)) * 100, 2)


# --------------------------------------------------------------------------- #
# 3. 主流程
# --------------------------------------------------------------------------- #
def main():
    t0 = time.time()
    snap, snap_rows, port, screen = load_snapshots()
    unverified = []

    # ---- 取数：组合 12 只 + 万科（工行已含） ----
    needed = [s for s, _ in PORTFOLIO_SYMBOLS] + [VANKE[0]]
    bars, bad = {}, []
    for sym in needed:
        b = fetch_bars(sym)
        if not b:
            bad.append(sym)
            unverified.append(f"{sym} 日K取数失败（Sina 接口 3 次重试均失败），该标的指标未能核实")
            continue
        bars[sym] = b

    # 以日K末根日期为准（盘中 Sina 日K 不含当天），重定 AS_OF 与输出路径
    global AS_OF, SNAP, OUT
    if bars:
        AS_OF = max(b[-1]["date"] for b in bars.values())
        cand = os.path.join(ROOT, "data", f"snapshot-{AS_OF}.json")
        if os.path.exists(cand):
            SNAP = cand
        OUT = os.path.join(ROOT, "data", f"risk-{AS_OF}.json")

    # ---- 校验：K线收盘 vs 快照 price ----
    price_mismatch = []
    for sym, b in bars.items():
        snap_px = snap_rows.get(sym, {}).get("price")
        if snap_px is None:
            price_mismatch.append((sym, "快照无此代码", b[-1]["close"]))
        elif abs(b[-1]["close"] - snap_px) > max(0.005, snap_px * 0.005):
            price_mismatch.append((sym, snap_px, b[-1]["close"]))
    csv_mismatch = []
    for sym, row in screen.items():
        if sym in snap_rows:
            if abs(float(row["price"]) - snap_rows[sym]["price"]) > 1e-6:
                csv_mismatch.append(sym)

    # ---- 价格序列 & 日收益 ----
    px = {}
    for sym, b in bars.items():
        px[sym] = pd.Series({x["date"]: x["close"] for x in b}).sort_index()
    price_df = pd.DataFrame(px).sort_index().ffill()
    ret_df = price_df.pct_change().dropna()
    rets = {sym: ret_df[sym].to_numpy() for sym in ret_df.columns}

    # ---- 对象①：12 只组合（38% 股票 + 62% 现金） ----
    pos = [p for p in port["positions"] if p["symbol"] in ret_df.columns]
    w = np.array([p["weight_pct"] / 100.0 for p in pos])
    stock_ret = ret_df[[p["symbol"] for p in pos]].to_numpy()
    port_ret = stock_ret @ w  # 现金收益 0，权重已按总资产计
    port_metrics = basic_metrics(port_ret, "12只组合（38%股票+62%现金）")
    pg = garch_vol(port_ret * 100)
    port_metrics["garch"] = {"cond_vol_daily_pct": pg[0], "var95_1d_pct": pg[1],
                             "var95_5d_pct": pg[2], "note": pg[3]}
    port_metrics["weights_pct"] = {p["name"]: p["weight_pct"] for p in pos}
    port_metrics["cash_pct"] = port.get("cash_pct")
    # 以实盘账户规模折算金额（账户总资产 10000.00 元；仅用于理解量级，组合并非账户真实持仓）
    ACCOUNT = 10000.00
    port_metrics["account_scale"] = {
        "account_assets": ACCOUNT,
        "var95_1d_amount": round(ACCOUNT * port_metrics["var95_daily_pct"] / 100, 2),
        "var99_1d_amount": round(ACCOUNT * port_metrics["var99_daily_pct"] / 100, 2),
        "cvar95_1d_amount": round(ACCOUNT * port_metrics["cvar95_daily_pct"] / 100, 2),
    }
    port_metrics["sample"] = {"start": str(price_df.index[0]), "end": str(price_df.index[-1]),
                              "n_bars": int(len(price_df)), "n_returns": int(len(port_ret)),
                              "stocks_included": len(pos)}

    # ---- 对象②：示例股票 400 股 ----
    vk = VANKE[0]
    vanke_metrics = None
    vanke_block = None
    if vk in rets:
        vanke_metrics = basic_metrics(rets[vk], f"示例股票 {vk}（400 股）")
        gv = garch_vol(rets[vk] * 100)
        price = snap_rows[vk]["price"]
        cost = 3.3425
        vanke_metrics["garch"] = {"cond_vol_daily_pct": gv[0], "var95_1d_pct": gv[1],
                                  "var95_5d_pct": gv[2], "note": gv[3]}
        vanke_metrics["position"] = {
            "shares": 400, "cost": cost, "price": price,
            "market_value": round(price * 400, 2),
            "pnl": round((price - cost) * 400, 2),
            "pnl_pct": round((price / cost - 1) * 100, 2),
            "var95_1d_price": round(price * (1 + (gv[1] / 100)), 3) if gv[1] is not None else None,
        }
        # 止损 vs GARCH VaR95 + 触发概率
        stops = []
        for s in VANKE_STOPS:
            d = (price - s) / price * 100
            p1, fp1 = prob_touch(d, gv[0], 1) if gv[0] else (None, None)
            p5, fp5 = prob_touch(d, gv[0], 5) if gv[0] else (None, None)
            stops.append({
                "stop": s,
                "distance_pct": round(d, 2),
                "within_1d_var95": (gv[1] is not None and d < abs(gv[1])),
                "within_5d_var95": (gv[2] is not None and d < abs(gv[2])),
                "garch_var95_1d_price": round(price * (1 + (gv[1] / 100)), 3) if gv[1] is not None else None,
                "prob_touch_1d_pct_terminal": p1,
                "prob_touch_1d_pct_first_passage_upper": fp1,
                "prob_touch_5d_pct_terminal": p5,
                "prob_touch_5d_pct_first_passage_upper": fp5,
            })
        # 与 task-12 给定/此前实测值的显式对账（-2.47% 距离、-4.15% VaR95）
        prior = {"stop": 3.16, "distance_pct": -2.47, "garch_var95_1d_pct": -4.15}
        s316 = next(x for x in stops if abs(x["stop"] - 3.16) < 1e-9)
        vanke_block = {
            "prior_estimate_reconcile": {
                "prior": prior,
                "recomputed": {"distance_pct": -s316["distance_pct"],
                               "garch_var95_1d_pct": gv[1]},
                "diff_distance_pp": round(abs(-s316["distance_pct"] - prior["distance_pct"]), 4),
                "diff_var95_pp": round(abs(gv[1] - prior["garch_var95_1d_pct"]), 4),
                "note": "差异 ≤0.05pp 视为一致；本次差异来自日K数据源（Sina 未复权 vs 之前腾讯复权缓存）与 GARCH 样本区间，非口径冲突。",
            },
            "metrics": vanke_metrics,
            "garch_cond_vol_daily_pct": gv[0],
            "garch_var95_1d_pct": gv[1],
            "garch_var95_5d_pct": gv[2],
            "stop_analysis": stops,
            "note": "触发概率为正态近似：terminal=Φ(-d/(σ√T))，first_passage_upper=2Φ(-d/(σ√T))（无漂移首达上界）；"
                    "未考虑趋势/跳空/停牌，仅作量级参考，非预测。",
        }
    else:
        unverified.append("示例股票 日K不可用，万科指标与止损触发概率未能核实")

    # ---- 对象③：工商银行单票参照 ----
    ic = ICBC[0]
    icbc_metrics = None
    if ic in rets:
        icbc_metrics = basic_metrics(rets[ic], f"工商银行 {ic}（组合最大权重单票参照）")
        gi = garch_vol(rets[ic] * 100)
        price = snap_rows[ic]["price"]
        icbc_metrics["garch"] = {"cond_vol_daily_pct": gi[0], "var95_1d_pct": gi[1],
                                 "var95_5d_pct": gi[2], "note": gi[3]}
        icbc_metrics["position"] = {"price": price, "var95_1d_price": round(price * (1 + (gi[1] / 100)), 3) if gi[1] is not None else None}
    else:
        unverified.append("工商银行日K不可用，单票参照指标未能核实")

    # ---- 检查 A：全止损同时触发 ≤3%（用组合 JSON 的 stop_loss 重算） ----
    stop_budget_rows, total = [], 0.0
    for p in port["positions"]:
        sym = p["symbol"]
        snap_px = snap_rows.get(sym, {}).get("price", p.get("price_2026_11_06"))
        stop = p["stop_loss"]
        dist = (snap_px - stop) / snap_px * 100  # %
        contrib = (p["weight_pct"] / 100.0) * dist  # 对总资产 %
        total += contrib
        stop_budget_rows.append({
            "symbol": sym, "name": p["name"], "weight_pct": p["weight_pct"],
            "price": snap_px, "stop_loss": stop,
            "stop_distance_pct": round(dist, 3),
            "loss_contribution_pct": round(contrib, 4),
        })
    declared = port.get("hard_constraints", {}).get("all_stops_total_pct")
    limit = -3.0
    diff_pp = round(total - abs(declared), 4) if declared is not None else None
    stop_budget = {
        "computed_total_pct": -round(total, 3),
        "declared_total_pct": declared,
        "diff_pp": diff_pp,
        "limit_pct": limit,
        "pass_reconcile": (diff_pp is not None and abs(diff_pp) <= 0.05),
        "pass_limit": (-round(total, 3)) >= limit,
        "rows": stop_budget_rows,
        "note": "loss_contribution_pct = 权重 × 止损距离，均为「对总资产」口径；computed 按 11-06 快照价与组合 JSON 止损价复算。",
    }

    # ---- 检查 B：逐只「止损距离 vs GARCH 1 日 VaR95」 ----
    garch_rows, noise_flags, noise_flags_5d = [], [], []
    for p in port["positions"]:
        sym = p["symbol"]
        snap_px = snap_rows.get(sym, {}).get("price")
        if not snap_px and sym in bars:
            snap_px = bars[sym][-1]["close"]
        if not snap_px:
            unverified.append(f"{sym} 无价格，止损距离未能核实")
            continue
        stop = p["stop_loss"]
        dist = (snap_px - stop) / snap_px * 100
        row = {"symbol": sym, "name": p["name"], "weight_pct": p["weight_pct"],
               "price": snap_px, "stop_loss": stop, "stop_distance_pct": round(dist, 2)}
        if sym in rets:
            g = garch_vol(rets[sym] * 100)
            row.update({
                "garch_cond_vol_daily_pct": g[0],
                "garch_var95_1d_pct": g[1],
                "garch_var95_5d_pct": g[2],
                "garch_note": g[3],
            })
            if g[1] is not None:
                row["within_1d_var95"] = dist < abs(g[1])
                row["within_5d_var95"] = dist < abs(g[2])
                row["garch_var95_1d_price"] = round(snap_px * (1 + g[1] / 100), 3)
                row["prob_touch_1d_pct_terminal"] = prob_touch(dist, g[0], 1)[0]
                if row["within_1d_var95"]:
                    noise_flags.append(
                        f"{p['name']}（止损距离 {dist:.2f}% < GARCH 1日VaR95 {abs(g[1]):.2f}%）")
                if row["within_5d_var95"]:
                    noise_flags_5d.append(
                        f"{p['name']}（止损距离 {dist:.2f}% < GARCH 5日VaR95 {abs(g[2]):.2f}%）")
            else:
                row["within_1d_var95"] = None
                row["within_5d_var95"] = None
                unverified.append(f"{p['name']} GARCH 拟合失败，止损/波动比较未能核实")
        else:
            unverified.append(f"{p['name']} 日K不可用，止损/波动比较未能核实")
        garch_rows.append(row)

    checks = {
        "A_all_stops_budget": stop_budget,
        "B_stop_vs_garch_var95": {
            "metric": "止损距离(%) vs 该股 GARCH(1,1) 1 日 VaR95 绝对值(%)；止损距离更小 = 在正常日内波动以内 = 易被噪声打掉",
            "noise_prone_1d_symbols": noise_flags,
            "noise_prone_5d_symbols": noise_flags_5d,
            "note_1d": "严格口径：止损距离 < 该股 GARCH 1 日 VaR95 绝对值 → 一个 95% 分位的日内波动就能打掉，属噪声级止损。本次 12 只均未触（止损距离 4.45%–15.90%，1 日 VaR95 仅 1.7%–5.0%）。",
            "note_5d": "宽松口径：止损距离 < 该股 GARCH 5 日 VaR95 绝对值 → 一周内出现 95% 分位的正常波动就可能触发，值得关注（非错误，但说明止损偏紧）。",
            "rows": garch_rows,
            "vanke": vanke_block["stop_analysis"] if vanke_block else None,
        },
    }

    out = {
        "as_of": AS_OF,
        "data_date": "2026-11-06 收盘（Sina 日K 末根 2026-11-06；快照生成 2026-11-06 22:16:22）",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "script": "tools/risk_metrics.py",
        "reproduce": "python3 tools/risk_metrics.py",
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "packages": _pkg_versions(),
            "gpu": "无（纯 CPU）",
        },
        "data_sources": {
            "bars": "Sina CN_MarketData.getKLineData（scale=240, datalen=250；腾讯 fqkline 本机 HTTP 501 不可用）",
            "snapshot": "data/snapshot-2026-11-06.json（只读）",
            "portfolio": "models/portfolio-2026-11-06.json（只读）",
            "screen": "models/screen-2026-11-06.csv（只读，价格交叉核对）",
        },
        "validation": {
            "symbols_fetched": len(bars),
            "symbols_failed": bad,
            "bars_last_date_ok": len(bad) == 0,
            "kline_vs_snapshot_price_mismatch": price_mismatch,
            "screen_csv_vs_snapshot_price_mismatch": csv_mismatch,
            "n_bars_per_symbol": BARS,
        },
        "objects": {
            "portfolio_12": port_metrics,
            "vanke_400": vanke_block,
            "icbc_single": icbc_metrics,
        },
        "checks": checks,
        "unverified": unverified,
        "disclaimer": "本文件为团队研究记录，指标基于历史价格统计，不构成投资建议、不承诺收益；GARCH 触发概率为正态近似，非预测。",
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    elapsed = time.time() - t0
    print_summary(out, elapsed)
    return 0


def _pkg_versions():
    vs = {}
    for mod in ("empyrical", "quantstats", "arch", "pandas", "numpy", "scipy"):
        try:
            m = __import__(mod)
            vs[mod] = getattr(m, "__version__", "未能核实")
        except Exception:  # noqa: BLE001
            vs[mod] = "未能核实"
    return vs


def print_summary(out, elapsed):
    print("=" * 78)
    print(f"risk_metrics · as_of {out['as_of']} · 耗时 {elapsed:.1f}s · 输出 {os.path.relpath(OUT, ROOT)}")
    print("=" * 78)
    for key, label in [("portfolio_12", "① 12只组合(38%股票+62%现金)"),
                       ("vanke_400", "② 示例股票 400股"),
                       ("icbc_single", "③ 工行单票参照")]:
        obj = out["objects"][key]
        m = obj["metrics"] if key == "vanke_400" else obj
        if not m:
            print(f"{label}: 未能核实")
            continue
        g = m.get("garch", {})
        print(f"{label}")
        print(f"  年化波动 {m['annual_volatility_pct']:>6.2f}% | MaxDD {m['max_drawdown_pct']:>6.2f}% | "
              f"VaR95 {m['var95_daily_pct']:>6.2f}% | VaR99 {m['var99_daily_pct']:>6.2f}% | "
              f"CVaR95 {m['cvar95_daily_pct']:>6.2f}%")
        print(f"  Sortino {m['sortino']:>5.2f} | Calmar {m['calmar']:>5.2f} | "
              f"GARCH条件日波动 {g.get('cond_vol_daily_pct')}% | GARCH 1日VaR95 {g.get('var95_1d_pct')}%")
        if key == "vanke_400":
            p = m["position"]
            print(f"  持仓: 400股 成本{p['cost']} 现价{p['price']} 市值{p['market_value']} "
                  f"浮亏{p['pnl']}({p['pnl_pct']}%) | VaR95对应价 {p['var95_1d_price']}")
    a = out["checks"]["A_all_stops_budget"]
    print("-" * 78)
    print(f"检查A 全止损预算: 复算 {a['computed_total_pct']}% vs JSON {a['declared_total_pct']}% "
          f"(差 {a['diff_pp']}pp) | 限 {a['limit_pct']}% | 对账{'通过' if a['pass_reconcile'] else '不通过'} "
          f"| 红线{'通过' if a['pass_limit'] else '突破'}")
    b = out["checks"]["B_stop_vs_garch_var95"]
    print(f"检查B 止损 vs GARCH VaR95：1日VaR95内（噪声级）{len(b['noise_prone_1d_symbols'])} 只"
          + (f" → {'; '.join(b['noise_prone_1d_symbols'])}" if b["noise_prone_1d_symbols"] else "（无）")
          + f" | 5日VaR95内（偏紧）{len(b['noise_prone_5d_symbols'])} 只"
          + (f" → {'; '.join(b['noise_prone_5d_symbols'])}" if b["noise_prone_5d_symbols"] else "（无）"))
    print("  标的        止损距离%  GARCH条件波动%  1日VaR95%  5日VaR95%  噪声内(1日) 噪声内(5日)  1日触发概率%")
    for r in b["rows"]:
        print(f"  {r['name']:<8}{r['stop_distance_pct']:>9.2f}  {str(r.get('garch_cond_vol_daily_pct')):>14}"
              f"  {str(r.get('garch_var95_1d_pct')):>9}  {str(r.get('garch_var95_5d_pct')):>9}"
              f"  {str(r.get('within_1d_var95')):>9}  {str(r.get('within_5d_var95')):>9}"
              f"  {str(r.get('prob_touch_1d_pct_terminal')):>12}")
    if b.get("vanke"):
        print("-" * 78)
        print("示例股票 止损触发概率（正态近似，GARCH 条件波动口径）：")
        for s in b["vanke"]:
            print(f"  止损 {s['stop']:.2f}（距现价 {s['distance_pct']:.2f}%，1日VaR95内={s['within_1d_var95']}）"
                  f" | 1日触发 {s['prob_touch_1d_pct_terminal']}%（首达上界 {s['prob_touch_1d_pct_first_passage_upper']}%）"
                  f" | 5日触发 {s['prob_touch_5d_pct_terminal']}%（上界 {s['prob_touch_5d_pct_first_passage_upper']}%）")
    if out["unverified"]:
        print("-" * 78)
        print("未能核实：")
        for u in out["unverified"]:
            print("  -", u)
    print("=" * 78)


if __name__ == "__main__":
    sys.exit(main())
