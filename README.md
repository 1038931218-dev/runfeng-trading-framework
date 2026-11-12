# 润锋 A 股研究交易系统（公开示例版）

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
