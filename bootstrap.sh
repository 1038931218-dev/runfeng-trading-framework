#!/bin/sh
# 新设备一键恢复（2026-11-07）
set -e
cd "$(dirname "$0")"
echo "== 1/3 环境检查 =="
python3 --version || { echo "需要 python3"; exit 1; }
echo "== 2/3 自检 =="
python3 tools/selftest.py
echo "== 3/3 生成每日要点（示例数据）=="
python3 tools/daily_brief.py | head -8
cat <<'TXT'

下一步：
1) cp data/account-state.example.json data/account-state.json  # 填真实持仓（勿提交）
2) python3 tools/make_plan.py                                   # 生成盯盘计划
3) bash tools/watch_loop.sh data/plan-<日期>.json               # 启动监控
4) 让 AI 读 AGENT_GUIDE.md 与 HANDOFF.md 接管本系统
TXT
