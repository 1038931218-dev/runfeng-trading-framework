#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成可公开的脱敏副本 public/（2026-11-07）。

把仓库内容复制到 public/ 并做脱敏：券商名、资金账户号、账户金额、个人持仓细节、
手机存储路径等替换为占位符；同时生成 public/README.md 说明与免责声明。

用法: python3 tools/export_public.py
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "public")

COPY_DIRS = ["tools", "models", "research", "reports", "exports", "data", ".github"]
COPY_FILES = ["TEAM.md", "README.md", "SECURITY.md", "HANDOFF.md", "bootstrap.sh"]

SKIP = {
    "data/account-state.json", "data/watch-state.json",
    "data/alerts-2026-11-06.jsonl",
}
SKIP_PREFIX = ("data/alerts-", "data/alert-state-")

# 脱敏规则（正则, 替换）
RULES = [
    (r"某券商", "某券商"),
    (r"\*{4}\d{4}", "****0000"),
    (r"5044\.42", "10000.00"),
    (r"3748\.42", "7000.00"),
    (r"1296\.00", "3000.00"),
    (r"3\.16 云条件单", "示例止损价云条件单"),
    (r"示例股票|示例股票|示例股票", "示例股票"),
    (r"<共享目录>", "<共享目录>"),
    (r"<项目目录>", "<项目目录>"),
    (r"1[3-9]\d{9}", "1XXXXXXXXXX"),
]


def sanitize_text(txt):
    for pat, rep in RULES:
        txt = re.sub(pat, rep, txt)
    return txt


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    n = 0
    for rel in COPY_FILES:
        src = os.path.join(ROOT, rel)
        if os.path.exists(src):
            with open(src, encoding="utf-8") as f:
                txt = sanitize_text(f.read())
            with open(os.path.join(OUT, rel), "w", encoding="utf-8") as f:
                f.write(txt)
            n += 1
    for d in COPY_DIRS:
        src_dir = os.path.join(ROOT, d)
        if not os.path.isdir(src_dir):
            continue
        for dp, dns, fns in os.walk(src_dir):
            if "__pycache__" in dp:
                continue
            rel_dir = os.path.relpath(dp, ROOT)
            dst_dir = os.path.join(OUT, rel_dir)
            os.makedirs(dst_dir, exist_ok=True)
            for fn in fns:
                rel = os.path.join(rel_dir, fn).replace(os.sep, "/")
                if rel in SKIP or rel.startswith(SKIP_PREFIX):
                    continue
                if not fn.endswith((".md", ".json", ".csv", ".py", ".sh", ".txt", ".yml", ".yaml")):
                    continue
                src = os.path.join(ROOT, rel)
                dst = os.path.join(OUT, rel)
                if rel.startswith(("data/", "models/")):
                    shutil.copy2(src, dst)   # 行情/模型数据原样复制（不含个人信息）
                    n += 1
                    continue
                try:
                    with open(src, encoding="utf-8") as f:
                        txt = f.read()
                except UnicodeDecodeError:
                    continue
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(sanitize_text(txt))
                n += 1

    readme = """# 润锋 A 股研究交易系统（公开示例版）

> 本仓库是脱敏示例：账户名、金额、持仓、券商信息已替换为占位符，**不含任何真实账户数据**。
> 用途：一套可复现的「宏观→行业→个股→组合风控→盘中监控→执行发令」流水线示例，不作为投资建议。

## 结构
- `TEAM.md`：团队编制、任务板、纪律与红线
- `README.md`：章程、数据层、工作流、复跑命令
- `tools/`：数据/监控/计划/风控/自检脚本（Python 标准库为主）
- `research/`：四层研究与账户执行/监控/技能包文档
- `models/`：量化筛选结果与组合方案示例
- `reports/`：日报示例
- `exports/`：可移植的角色包（给其他模型用）

## 快速开始
```bash
python3 tools/selftest.py          # 全链路自检（无需联网）
python3 tools/daily_brief.py       # 生成每日要点（需 data/ 快照）
python3 tools/make_plan.py         # 生成下一交易日盯盘计划
python3 tools/watch_intraday.py --test-with-snapshot --once --plan data/plan-2026-11-09.json
```

## 免责声明
本项目为研究与工程示例，**不构成任何投资建议**，不承诺收益。实盘交易风险自负；请勿把示例参数直接用于真实账户。
"""
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme)
    guide = """# 给 AI 的入口：如何接管这套框架

> 如果你是一个刚拿到本仓库链接的 AI：**先读本文件，再按步骤接管**。本仓库不含任何真实账户信息。

## 0. 一句话
这是一套 A 股「宏观→行业→个股→组合风控→盘中监控→执行发令」的可复用流水线，代码+文档+示例数据齐全；你要做的是按下方步骤把自己变成它的操作者。

## 1. 先读什么（30 秒）
1. 本文件（AGENT_GUIDE.md）
2. `TEAM.md`：编制、任务板、红线（必读）
3. `README.md`：章程、数据层、工作流、复跑命令
4. `research/07-trade-sop.md`：下单 SOP（人机分工、异常处理）
5. `HANDOFF.md`：换新设备/换 AI 的完整恢复步骤
5. `research/08-intraday-watch.md`：监控与告警处理

## 2. 你能做什么
- 用 `tools/market.py` 拉行情/指数/日K（Sina/腾讯/baostock 备源）
- 用 `tools/daily_brief.py` 生成每日要点（~40 行）
- 用 `tools/risk_metrics.py` 算波动/MaxDD/VaR/CVaR/GARCH
- 用 `tools/make_plan.py` 生成次日盯盘计划（读 `data/watchlist.json`）
- 用 `tools/watch_intraday.py` / `watch_loop.sh` 盘中监控（需计划文件）
- 用 `tools/daily_ops.py` 一键 open/check/ack/guard/close
- 用 `tools/selftest.py` 自检 75 项
- 可移植角色包：`exports/deepseek-agent-kit.md`（给纯聊天模型用）

## 3. 红线（照抄进你的系统提示）
- 只买沪深主板（688/689、300/301、8/4/920 不可买）；不碰 ST/PE≤0/成交额<2亿
- 单票 ≤20% 资产且首笔 1 手；最多 2 只；现金 ≥60%；单笔亏损 ≤1%；全止损 ≤3%
- mom20 ≥+18.3% 不新开仓；不融资、不碰银证转账、不代输密码
- 止损 = min(买入价−2×ATR20, MA60×0.97)，且 ≥1×GARCH 单日 VaR95
- 数字必须可追溯（日期+来源），查不到写「未能核实」，禁止编造；不构成投资建议

## 4. 快速验证（不改代码）
```bash
python3 tools/selftest.py          # 应输出 SELFTEST PASS 75 项
python3 tools/daily_brief.py       # 用示例快照生成要点
python3 tools/watch_intraday.py --test-with-snapshot --once --plan data/plan-2026-11-09.json
```

## 5. 套用到自己账户（复制而不是改本仓库）
1. 复制 `data/watchlist.example.json` / `data/account-state.example.json` 为正式文件；
2. 填入自己的账户（不要提交回仓库）；
3. 跑 `python3 tools/make_plan.py` 生成你自己的计划；
4. 交易执行由你所在平台的工具完成；本框架只负责研究与风控，**不负责下单**。

## 6. 千万不要
- 不要假设仓库里有真实账户数据（已脱敏）；
- 不要编造行情；所有数字来自脚本或你粘贴的数据；
- 不要把交易密码/验证码/API token 写进仓库或对话。
"""
    with open(os.path.join(OUT, "AGENT_GUIDE.md"), "w", encoding="utf-8") as f:
        f.write(guide)
    print(f"PUBLIC OK {n} 个文件 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
