# 给 AI 的入口：如何接管这套框架

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
