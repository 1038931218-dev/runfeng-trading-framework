#!/bin/bash
# 盘中监控守护（2026-11-07）：
#   反复运行 watch_intraday.py；非告警退出（异常/收盘）就 5 秒后重启；
#   收到通知/告警退出（exit 10）则结束，交给值守 Agent 处理。
# 用法：bash tools/watch_loop.sh [plan文件]
cd "$(dirname "$0")/.." || exit 1
PLAN="${1:-data/plan-$(date +%F).json}"
END=$(date +%s); END=$((END + 21600));
while [ "$(date +%s)" -lt "$END" ]; do
  PYTHONUNBUFFERED=1 python3 -u tools/watch_intraday.py --interval 180 --max-runtime 21600 --keep-alive --plan "$PLAN"
  rc=$?
  if [ "$rc" -eq 10 ]; then
    echo "[loop] 有告警，停止守护，等待值守处理"
    exit 10
  fi
  # 收盘（15:00 后）watch_intraday 会返回 0，这里也结束
  hhmm=$(date +%H%M)
  if [ "$hhmm" -ge 1501 ]; then
    echo "[loop] 已收盘，结束"
    exit 0
  fi
  echo "[loop] watch 退出 rc=$rc，5 秒后重启"
  sleep 5
done
echo "[loop] 到达最长运行时间，结束"
exit 0
