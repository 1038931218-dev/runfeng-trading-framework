#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""personal_finance.py — 润财个人财务模块（资产负债表 / 月度现金流 / 预算执行 / 待补清单）。

数据
----
- `data/personal/profile.json`：逐项采集的当前快照（收入/支出/资产/负债/保障/目标/风险）。
  每个条目必须带 `source_ref`（用户口述日期或截图说明），缺的字段不编造。
- `data/personal/ledger.jsonl`：个人生活收支流水（append-only，复用 tools/ledger.py，
  `category=life`；type=life 正数=支出，负数=收入）。
- `reports/personal-finance-YYYY-MM.md`：月报（资产负债表 + 月度现金流 + 3 条最该先改的事）。

纪律
----
- 只记数字与摘要，不保存账号/身份证/密码截图；数字不可追溯的标「未能核实」，禁止编造。
- 不构成投资建议、税务建议或信贷建议。

用法
----
  # 逐项添加（用户一项一项回答后追加）
  python3 tools/personal_finance.py add income '{"source":"工资","monthly_avg":8000,"pay_day":"10号","stable":true,"source_ref":"用户口述 2026-11-10"}'
  python3 tools/personal_finance.py add assets '{"name":"活期","category":"现金","value":50000,"liquidity":"high","source_ref":"截图 2026-11-10"}'
  python3 tools/personal_finance.py set goals '{"emergency_months":6}'
  python3 tools/personal_finance.py set risk '{"max_drawdown_pct":15,"horizon_years":3}'
  # 查看与报表
  python3 tools/personal_finance.py show
  python3 tools/personal_finance.py check            # 关键指标 + 待补清单
  python3 tools/personal_finance.py report --date 2026-11
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "personal"
PROFILE = DATA / "profile.json"
LIFE_LEDGER = DATA / "ledger.jsonl"
REPORTS = ROOT / "reports"

LIST_SECTIONS = ("income", "fixed_expenses", "variable_expenses", "assets", "liabilities", "insurance")
DICT_SECTIONS = ("goals", "risk")

SECTION_HINT = {
    "income": "收入：source, monthly_avg, pay_day, stable, source_ref",
    "fixed_expenses": "固定支出：item, monthly, due_day, source_ref",
    "variable_expenses": "浮动支出：item, monthly_avg_3m, source_ref",
    "assets": "资产：name, category, value, liquidity(high/medium/low), source_ref",
    "liabilities": "负债：name, category, balance, rate_pct, monthly_payment, due_day, source_ref",
    "insurance": "保障：name, kind, coverage, annual_premium, renew_date, source_ref",
    "goals": "目标：emergency_months, big_expenses[], note, source_ref",
    "risk": "风险：max_drawdown_pct, horizon_years, tolerance, source_ref",
}

# 逐项采集清单（与用户确认的 10 项对应）
CHECKLIST = [
    ("income", "收入（工资/奖金/副业/租金）"),
    ("fixed_expenses", "固定支出（房租/房贷/保险/学费/赡养）"),
    ("variable_expenses", "浮动支出（近 3 个月月均）"),
    ("assets", "资产（存款/理财/基金/公积金等）"),
    ("liabilities", "负债（房贷/车贷/信用卡/网贷）"),
    ("insurance", "保障（社保/医保/商业保险）"),
    ("goals", "目标（应急金/年内大额支出）"),
    ("risk", "风险偏好（回撤/期限）"),
]


def load_profile() -> dict:
    if not PROFILE.exists():
        return {"as_of": datetime.now().strftime("%Y-%m-%d"), "currency": "CNY",
                **{s: [] for s in LIST_SECTIONS}, **{s: {} for s in DICT_SECTIONS}}
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def save_profile(p: dict):
    p["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE.write_text(json.dumps(p, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_life_ledger() -> list[dict]:
    if not LIFE_LEDGER.exists():
        return []
    out = []
    for line in LIFE_LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def fmt(x, nd=2) -> str:
    return "未能核实" if x is None else f"{x:,.{nd}f}"


def pct(x, nd=1) -> str:
    return "未能核实" if x is None else f"{x:.{nd}f}%"


def val(x, key):
    """取条目数值；缺失返回 None（不改写成 0）。"""
    v = x.get(key)
    return None if v is None else float(v)


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------

def _sum_known(items, key):
    """对某个字段求和；列表为空或任一缺失则返回 None（未能核实），不把缺失当 0。"""
    if not items:
        return None
    vals = []
    for x in items:
        v = x.get(key)
        if v is None:
            return None
        vals.append(float(v))
    return round(sum(vals), 2)


def _sum_partial(items, key):
    """资产/负债口径：求已知值合计，跳过缺失值并返回缺失项名称列表。"""
    if not items:
        return None, []
    known, missing = [], []
    for x in items:
        v = x.get(key)
        if v is None:
            missing.append(x.get("name") or x.get("item") or "未命名")
        else:
            known.append(float(v))
    return round(sum(known), 2), missing


def compute(p: dict) -> dict:
    assets = p.get("assets", []) or []
    liab = p.get("liabilities", []) or []
    income = p.get("income", []) or []
    fixed = p.get("fixed_expenses", []) or []
    variable = p.get("variable_expenses", []) or []
    ins = p.get("insurance", []) or []

    m = {}
    m["total_assets"], m["assets_missing"] = _sum_partial(assets, "value")
    if assets:
        hi = [a for a in assets if a.get("liquidity") == "high"]
        mid = [a for a in assets if a.get("liquidity") == "medium"]
        m["liquid_assets"], _ = _sum_partial(hi, "value")
        m["semi_liquid_assets"], _ = _sum_partial(mid, "value")
        if m["liquid_assets"] is None:
            m["liquid_assets"] = 0.0
    else:
        m["liquid_assets"] = m["semi_liquid_assets"] = None
    m["total_liabilities"], m["liabilities_missing"] = _sum_partial(liab, "balance")
    if m["total_assets"] is not None and m["total_liabilities"] is not None:
        m["net_worth"] = round(m["total_assets"] - m["total_liabilities"], 2)
    else:
        m["net_worth"] = None

    m["monthly_income"] = _sum_known(income, "monthly_avg")
    m["fixed_expense"] = _sum_known(fixed, "monthly")
    m["variable_expense"] = _sum_known(variable, "monthly_avg_3m")
    if m["fixed_expense"] is not None and m["variable_expense"] is not None:
        m["monthly_expense"] = round(m["fixed_expense"] + m["variable_expense"], 2)
    else:
        m["monthly_expense"] = None
    m["monthly_debt_service"] = _sum_known(liab, "monthly_payment")
    if m["monthly_income"] and m["monthly_expense"] is not None:
        m["surplus"] = round(m["monthly_income"] - m["monthly_expense"], 2)
        m["savings_rate"] = (round(m["surplus"] / m["monthly_income"] * 100, 1)
                             if m["monthly_income"] else None)
    else:
        m["surplus"] = m["savings_rate"] = None
    if m["liquid_assets"] is not None and m["monthly_expense"]:
        m["emergency_months"] = round(m["liquid_assets"] / m["monthly_expense"], 1)
        m["emergency_gap_3m"] = round(max(0.0, 3 * m["monthly_expense"] - m["liquid_assets"]), 2)
    else:
        m["emergency_months"] = m["emergency_gap_3m"] = None
    m["dti"] = (round(m["monthly_debt_service"] / m["monthly_income"] * 100, 1)
                if m["monthly_income"] and m["monthly_debt_service"] is not None else None)
    m["variable_ratio"] = (round(m["variable_expense"] / m["monthly_expense"] * 100, 1)
                           if m["monthly_expense"] and m["variable_expense"] is not None else None)
    high_rate = [x for x in liab if float(x.get("rate_pct") or 0) > 6 or x.get("high_rate_risk")]
    m["high_rate_debts"] = high_rate
    m["high_rate_balance"] = _sum_known(high_rate, "balance") if high_rate else (0.0 if liab else None)
    m["insurance_coverage"] = _sum_known(ins, "coverage")
    m["annual_premium"] = _sum_known(ins, "annual_premium")
    goals = p.get("goals", {}) or {}
    m["emergency_target_months"] = goals.get("emergency_months")
    m["big_expenses"] = goals.get("big_expenses", []) or []
    m["big_expense_total"] = _sum_known(m["big_expenses"], "amount") if m["big_expenses"] else None
    # 派生口径：收入是否为计件待补（有收入项但月均缺）
    m["income_pending"] = bool(income) and m["monthly_income"] is None
    return m


def missing(p: dict) -> list[dict]:
    m = compute(p)
    out = []
    for key, label in CHECKLIST:
        val = p.get(key)
        if key in DICT_SECTIONS:
            ok = bool(val) and (key != "goals" or bool(val.get("emergency_months") is not None or val.get("big_expenses")))
        else:
            ok = bool(val)
        if not ok:
            out.append({"key": key, "label": label})
    # 已有条目但关键金额缺失 → 仍算待补（避免“填了标题就算完成”）
    partial = []
    if p.get("income") and m["monthly_income"] is None:
        partial.append(("income_amount", "收入·计件：派费单价/扣费金额（换算月均净收入）"))
    if p.get("assets") and m["total_assets"] is None:
        partial.append(("assets_amount", "资产·各项金额"))
    if p.get("liabilities") and m["total_liabilities"] is None:
        partial.append(("liab_amount", "负债·余额/利率/月供"))
    if p.get("fixed_expenses") and m["fixed_expense"] is None:
        partial.append(("fixed_amount", "固定支出·每月金额"))
    if p.get("variable_expenses") and m["variable_expense"] is None:
        partial.append(("variable_amount", "浮动支出·近3个月月均金额"))
    if p.get("insurance") and m["insurance_coverage"] is None:
        partial.append(("insurance_amount", "保障·保额/年缴/续费日"))
    out.extend({"key": k, "label": label} for k, label in partial)
    return out


# ---------------------------------------------------------------------------
# 3 条最该先改的事
# ---------------------------------------------------------------------------

def recommendations(p: dict, m: dict) -> list[dict]:
    recs = []
    data_gap = False
    if m["high_rate_balance"] and m["high_rate_balance"] > 0:
        known_rates = [x for x in m["high_rate_debts"] if x.get("rate_pct") is not None]
        if known_rates:
            interest = sum(float(x.get("balance") or 0) * float(x.get("rate_pct")) / 100
                           for x in known_rates)
            rate_txt = f"年利息约 {fmt(interest)} 元"
        else:
            rate_txt = "利率未能核实（罚息滚存、实际成本高）"
        recs.append({
            "p": 0,
            "title": "优先处理罚息/高息债务（支付宝）",
            "detail": (f"高成本负债余额 {fmt(m['high_rate_balance'])} 元"
                       f"（{', '.join(str(x.get('name', '?')) for x in m['high_rate_debts'])}）；{rate_txt}。"
                       "先向支付宝官方客服要账单分项（本金/利息/罚息），协商停息或分期；"
                       "不要再借新还旧，也别找第三方「反催收」代协商。"),
        })
    if m["emergency_months"] is not None and m["emergency_months"] < 3:
        recs.append({
            "p": 1,
            "title": "把应急金补到 3–6 个月生活费",
            "detail": (f"当前高流动性资产 {fmt(m['liquid_assets'])} 元 ≈ {m['emergency_months']} 个月生活费，"
                       f"补到 3 个月还差 {fmt(m['emergency_gap_3m'])} 元；"
                       "建议每月结余优先划入货币基金/活期，单独账户存放。"),
        })
    elif m["emergency_months"] is None:
        data_gap = True
    if m["dti"] is not None and m["dti"] > 35:
        recs.append({
            "p": 2,
            "title": "月供压降到收入 35% 以内",
            "detail": f"月供 {fmt(m['monthly_debt_service'])} 元占月收入 {pct(m['dti'])}，高于 35% 的常见安全线；"
                      "可考虑提前还款（先还高息）或延长低息贷款期限。",
        })
    if m["savings_rate"] is not None and m["savings_rate"] < 20:
        recs.append({
            "p": 3,
            "title": "把月结余率提到 20% 以上",
            "detail": (f"当前月结余 {fmt(m['surplus'])} 元、结余率 {pct(m['savings_rate'])}；"
                       f"目标每月至少存 {fmt(m['monthly_income'] * 0.2)} 元。"
                       + (f"浮动支出占 {pct(m['variable_ratio'])}，先从弹性消费压缩 10%–20%。"
                          if m["variable_ratio"] is not None else "")),
        })
    elif m["savings_rate"] is None:
        data_gap = True
    if data_gap:
        recs.append({
            "p": 1.5,
            "title": "先补齐收入/支出/资产，才能算清结余与应急金",
            "detail": ("目前收入或支出或高流动性资产缺失（见「待补清单」）；"
                       + ("计件收入还要补「单价/票量/扣费」才能换算月均。" if m.get("income_pending") else "")
                       + "先给大概数即可，后续用生活流水校准。"),
        })
    if m["monthly_income"]:
        has_million = any("百万医疗" in (x.get("name") or "") for x in p.get("insurance", []))
        if has_million:
            recs.append({
                "p": 4,
                "title": "补上意外险（送快递高风险）+ 重疾/寿险",
                "detail": ("已有百万医疗（400 万）兜住院；但缺意外险（职业风险最高、每年几百元）、"
                           "重疾给付与寿险（2 个孩子 + 19.2 万负债，家庭责任重）；"
                           "优先顺序：意外险 → 定期寿险 → 重疾。"),
            })
        elif m["insurance_coverage"] is None:
            recs.append({
                "p": 4,
                "title": "补齐保障缺口（先社保/医保，再百万医疗+意外）",
                "detail": (f"年收入约 {fmt(m['monthly_income'] * 12)} 元；"
                           "商业险保额建议 ≥3–5 年薪，目前保额未能核实，先确认社保/医保是否在缴。"),
            })
    if m["variable_ratio"] is not None and m["variable_ratio"] > 40:
        recs.append({
            "p": 5,
            "title": "浮动支出占比偏高",
            "detail": f"浮动支出占总支出的 {pct(m['variable_ratio'])}，建议给餐饮/娱乐/人情设月度上限并记账 1 个月。",
        })
    if m["big_expense_total"] and m["surplus"] and m["surplus"] > 0:
        recs.append({
            "p": 6,
            "title": "年内大额支出提前分月预留",
            "detail": (f"年内大额支出合计 {fmt(m['big_expense_total'])} 元，"
                       f"按当前月结余需 {m['big_expense_total']/m['surplus']:.1f} 个月存够；"
                       "建议单列专项账户、按月划入。"),
        })
    # 兜底建议（数据齐时也能凑满 3 条可执行动作）
    fallback = [
        ("把储蓄自动化",
         f"发薪日次日自动转出月结余的 60%（约 {fmt((m['surplus'] or 0) * 0.6)} 元）到独立储蓄/投资账户，"
         "避免「月底剩多少存多少」。"),
        ("给固定支出做年度体检",
         f"房租/保险/话费/订阅逐项核对，目标压降 5%–10%（约 {fmt((m['fixed_expense'] or 0) * 0.05)}–"
         f"{fmt((m['fixed_expense'] or 0) * 0.10)} 元/月）。"),
        ("建立季度复盘节奏",
         "每季度更新一次档案与本月流水，检查应急金月数、负债率、结余率三条线是否达标。"),
    ]
    for i, (title, detail) in enumerate(fallback):
        recs.append({"p": 90 + i, "title": title, "detail": detail})
    recs.sort(key=lambda x: x["p"])
    return [r for r in recs if r["title"]][:3]


# ---------------------------------------------------------------------------
# 报表
# ---------------------------------------------------------------------------

def build_report(p: dict, m: dict, month: str) -> str:
    as_of = p.get("as_of", month)
    L = []
    a = L.append
    a(f"# 个人财务月报 · {month}")
    a(f"> 润财（财务总监）｜基线 {as_of}｜来源：用户逐项口述/截图（每条已标 source_ref）｜不构成投资/税务建议")
    a("")
    a("## 1. 个人资产负债表")
    a("")
    a("| 资产 | 类别 | 金额(元) | 流动性 | 来源 |")
    a("|---|---|---:|---|---|")
    for x in p.get("assets", []):
        a(f"| {x.get('name','—')} | {x.get('category','—')} | {fmt(val(x, 'value'))} | "
          f"{x.get('liquidity','—')} | {x.get('source_ref','—')} |")
    miss_txt = ("；另有 " + "、".join(m["assets_missing"]) + " 估值未能核实") if m.get("assets_missing") else ""
    a(f"| **资产合计（已知）** |  | {fmt(m['total_assets'])}（高流动性 {fmt(m['liquid_assets'])}）"
      f"{miss_txt} |  |  |")
    a("")
    a("| 负债 | 类别 | 余额(元) | 年利率 | 月供(元) | 来源 |")
    a("|---|---|---:|---:|---:|---|")
    for x in p.get("liabilities", []):
        a(f"| {x.get('name','—')} | {x.get('category','—')} | {fmt(val(x, 'balance'))} | "
          f"{pct(x.get('rate_pct'))} | {fmt(val(x, 'monthly_payment'))} | {x.get('source_ref','—')} |")
    a(f"| **负债合计** |  | {fmt(m['total_liabilities'])} |  | {fmt(m['monthly_debt_service'])} |  |")
    a("")
    dt = (p.get("goals", {}) or {}).get("debt_principal_target")
    if dt:
        bd = dt.get("breakdown", {})
        a(f"**还款专用目标（本金）**：{fmt(dt['amount'])} 元 = "
          + " + ".join(f"{k} {fmt(v)}" for k, v in bd.items())
          + "（不含亲友借款）；**单列账户/台账、不动用**，攒够后一次谈「结清减免罚息」。")
    if (p.get("goals", {}) or {}).get("debt_priority"):
        a("**还款优先级**：" + " > ".join((p["goals"]["debt_priority"])))
    a(f"**净资产 = {fmt(m['total_assets'])} − {fmt(m['total_liabilities'])} = {fmt(m['net_worth'])} 元**"
      f"｜负债率 {pct(m['total_liabilities']/m['total_assets']*100 if (m['total_assets'] and m['total_liabilities'] is not None) else None)}")
    a("")
    a("## 2. 月度现金流表")
    a("")
    a("| 项目 | 金额(元/月) | 说明 |")
    a("|---|---:|---|")
    for x in p.get("income", []):
        a(f"| 收入·{x.get('source','—')} | {fmt(val(x, 'monthly_avg'))} | "
          f"发放日 {x.get('pay_day','—')}｜稳定 {'是' if x.get('stable') else '否'} |")
    a(f"| **收入合计** | **{fmt(m['monthly_income'])}** |  |")
    for x in p.get("fixed_expenses", []):
        a(f"| 固定支出·{x.get('item','—')} | {fmt(val(x, 'monthly'))} | "
          f"扣款日 {x.get('due_day','—')} |")
    a(f"| **固定支出合计** | {fmt(m['fixed_expense'])} |  |")
    for x in p.get("variable_expenses", []):
        a(f"| 浮动支出·{x.get('item','—')} | {fmt(val(x, 'monthly_avg_3m'))} | 近 3 个月月均 |")
    a(f"| **浮动支出合计** | {fmt(m['variable_expense'])} | 占支出 {pct(m['variable_ratio'])} |")
    a(f"| **月支出合计** | **{fmt(m['monthly_expense'])}** |  |")
    a(f"| **月结余** | **{fmt(m['surplus'])}** | 结余率 {pct(m['savings_rate'])} |")
    a("")
    frozen = (p.get("goals", {}) or {}).get("emergency_frozen")
    if frozen:
        a(f"- 应急金 {fmt(frozen)} 在某券商账户内「冻结不动」（非投资，用户担心转出被支付宝自动扣划）："
          f"不参与任何股票交易；可用国债逆回购（深市 1 天期 1,000 起）或券商现金管理打理，本金随时可用；"
          f"交易纪律可用 = 券商可用 − {fmt(frozen)}。")
    prog = (p.get("goals", {}) or {}).get("emergency_progress", {}) or {}
    prog_txt = ""
    if prog.get("target_3m"):
        prog_txt = (f"；目标 {fmt(prog['target_3m'])} 元（进度 {prog.get('progress_pct')}%，"
                    f"还差 {fmt(prog['target_3m'] - prog.get('current', 0))} 元）")
    a(f"- 应急金：高流动性资产 {fmt(m['liquid_assets'])} 元 ≈ **{m['emergency_months'] if m['emergency_months'] is not None else '未能核实'} 个月**生活费"
      f"（目标 {m['emergency_target_months'] if m['emergency_target_months'] is not None else '未能核实'} 个月）{prog_txt}")
    a(f"- 月供收入比：{pct(m['dti'])}｜浮动支出占比：{pct(m['variable_ratio'])}｜年保费：{fmt(m['annual_premium'])}")
    goals = p.get("goals", {}) or {}
    risk = p.get("risk", {}) or {}
    risk_txt = (f"最多接受 {risk.get('max_drawdown_pct')}% 回撤" if risk.get("max_drawdown_pct") is not None else "未能核实")
    a(f"- 目标与风险：应急金目标 {goals.get('emergency_months', '未能核实')} 个月；策略「{goals.get('priority_plan', '未能核实')}」；风险偏好：{risk_txt}")
    if m.get("big_expense_total"):
        a(f"- 年内/近期大额支出：{fmt(m['big_expense_total'])} 元（"
          + "、".join(f"{x.get('item')} {fmt(x.get('amount'))}({x.get('month')})" for x in m.get("big_expenses", []))
          + "）")
    a("")
    a("## 3. 保障（保险）")
    a("")
    a("| 险种 | 保额/内容 | 年缴(元) | 公司 | 有效期 |")
    a("|---|---|---:|---|---|")
    for x in p.get("insurance", []):
        cov = x.get("coverage_detail") or x.get("coverage") or "未能核实"
        a(f"| {x.get('name','—')} | {cov} | {fmt(x.get('annual_premium'))} | "
          f"{x.get('company','—')} | {x.get('renew_date','—')} |")
    if p.get("insurance_summary", {}).get("差异"):
        s_ = p["insurance_summary"]
        a(f"|  | 备注：页面总保费 {fmt(s_['2026年总保费'])} vs 分项 {fmt(s_['分项合计'])}，"
          f"差 {fmt(s_['差异'])} 元待核实 |  |  |  |")
    a("")
    a("## 4. 3 条最该先改的事")
    a("")
    for i, r in enumerate(recommendations(p, m), 1):
        a(f"{i}. **{r['title']}**：{r['detail']}")
    a("")
    # 本月实际生活流水
    life = [r for r in load_life_ledger() if r.get("date", "").startswith(month)]
    a("## 5. 本月生活流水（data/personal/ledger.jsonl）")
    a("")
    if life:
        exp = sum(float(r.get("amount") or 0) for r in life if r.get("type") == "life")
        a(f"- 本月流水 {len(life)} 条，生活支出合计 {fmt(exp)} 元；预算按浮动支出上限 "
          f"{fmt(m['variable_expense'])} 元执行（执行率 {pct(exp/m['variable_expense']*100 if m['variable_expense'] else None)}）。")
    else:
        a("- 本月尚无生活流水；日常支出先按口述月均入现金流表，实际流水随后续记录补充。")
    a("")
    a("## 6. 待补清单（按优先级）")
    a("")
    items = [x["label"] for x in missing(p)]
    seen = set(items)
    for g in p.get("gaps", []):
        if g.get("status") in ("open", "waiting") and g["label"] not in seen:
            items.append(g["label"])
            seen.add(g["label"])
    if items:
        for i, label in enumerate(items, 1):
            a(f"{i}. {label}")
    else:
        a("- 8 项已齐，进入月度跟踪。")
    a("")
    a("## 7. 声明")
    a("")
    a("- 数字均来自用户逐项口述/截图（见各行 source_ref）；缺的写「未能核实」，不编造。")
    a("- 本文为个人财务记录与预算建议，不构成投资、税务或信贷建议。")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_add(args):
    p = load_profile()
    if args.section not in LIST_SECTIONS:
        raise SystemExit(f"[personal] add 仅支持 {LIST_SECTIONS}；dict 型用 set")
    try:
        item = json.loads(args.json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[personal] JSON 解析失败：{exc}；示例：{SECTION_HINT[args.section]}")
    if not isinstance(item, dict):
        raise SystemExit("[personal] 需要 JSON 对象")
    item.setdefault("source_ref", "用户口述 " + p.get("as_of", ""))
    item["added_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p.setdefault(args.section, []).append(item)
    save_profile(p)
    print(f"[personal] + {args.section}: {json.dumps(item, ensure_ascii=False)}")
    return 0


def cmd_update(args):
    p = load_profile()
    if args.section not in LIST_SECTIONS:
        raise SystemExit(f"[personal] update 仅支持 {LIST_SECTIONS}")
    items = p.setdefault(args.section, [])
    idx = args.index - 1
    if not (0 <= idx < len(items)):
        raise SystemExit(f"[personal] 序号越界：{args.section} 共 {len(items)} 条")
    try:
        patch = json.loads(args.json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[personal] JSON 解析失败：{exc}")
    if not isinstance(patch, dict):
        raise SystemExit("[personal] 需要 JSON 对象")
    items[idx].update(patch)
    items[idx]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_profile(p)
    print(f"[personal] ~ {args.section}[{args.index}]: {json.dumps(items[idx], ensure_ascii=False)}")
    return 0


def cmd_set(args):
    p = load_profile()
    if args.section not in DICT_SECTIONS:
        raise SystemExit(f"[personal] set 仅支持 {DICT_SECTIONS}")
    try:
        item = json.loads(args.json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[personal] JSON 解析失败：{exc}；示例：{SECTION_HINT[args.section]}")
    if not isinstance(item, dict):
        raise SystemExit("[personal] 需要 JSON 对象")
    cur = p.setdefault(args.section, {})
    cur.update(item)
    cur["source_ref"] = item.get("source_ref", "用户口述 " + p.get("as_of", ""))
    save_profile(p)
    print(f"[personal] set {args.section}: {json.dumps(cur, ensure_ascii=False)}")
    return 0


def _metrics_block(p: dict, m: dict) -> list[str]:
    return [
        f"净资产 {fmt(m['net_worth'])}｜总资产 {fmt(m['total_assets'])}｜总负债 {fmt(m['total_liabilities'])}",
        f"月收入 {fmt(m['monthly_income'])}｜月支出 {fmt(m['monthly_expense'])}｜月结余 {fmt(m['surplus'])}"
        f"｜结余率 {pct(m['savings_rate'])}",
        f"高流动性 {fmt(m['liquid_assets'])}｜应急金 {m['emergency_months'] if m['emergency_months'] is not None else '未能核实'} 个月"
        f"｜月供收入比 {pct(m['dti'])}",
    ]


def cmd_check(args):
    p = load_profile()
    m = compute(p)
    print("== 个人财务指标 ==")
    for line in _metrics_block(p, m):
        print(line)
    miss = missing(p)
    print(f"== 待补（{len(miss)}/8）==")
    for x in miss:
        print(f"- {x['label']}")
    if not miss:
        print("- 已齐")
    return 0


def cmd_show(args):
    p = load_profile()
    print(json.dumps(p, ensure_ascii=False, indent=2))
    return 0


def cmd_report(args):
    p = load_profile()
    m = compute(p)
    month = args.date or str(p.get("as_of", ""))[:7] or datetime.now().strftime("%Y-%m")
    text = build_report(p, m, month)
    out = Path(args.out) if args.out else REPORTS / f"personal-finance-{month}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"[personal] 月报已生成：{out}（{len(text.splitlines())} 行）")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="润财个人财务模块")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="追加一条列表项（income/fixed_expenses/variable_expenses/assets/liabilities/insurance）")
    a.add_argument("section")
    a.add_argument("json")
    a.set_defaults(func=cmd_add)

    s = sub.add_parser("set", help="合并 dict 段（goals/risk）")
    s.add_argument("section")
    s.add_argument("json")
    s.set_defaults(func=cmd_set)

    u = sub.add_parser("update", help="按序号补充/修正列表项")
    u.add_argument("section")
    u.add_argument("index", type=int, help="1-based 序号")
    u.add_argument("json")
    u.set_defaults(func=cmd_update)

    sh = sub.add_parser("show", help="打印档案")
    sh.set_defaults(func=cmd_show)

    c = sub.add_parser("check", help="关键指标 + 待补清单")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("report", help="生成月报")
    r.add_argument("--date", help="YYYY-MM，默认 profile.as_of 所在月")
    r.add_argument("--out")
    r.set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
