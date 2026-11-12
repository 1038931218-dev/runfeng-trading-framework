#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易时段守护：每 60 秒检查，交易时段内保证 watch_intraday 在跑。
用途：定时任务因手机休眠未投递时，等 App/引擎恢复后自动补起监控（2026-11-10）。
用法: setsid nohup python3 tools/session_guard.py >> /tmp/session_guard.log 2>&1 &
"""
import datetime as dt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = "/tmp/session_guard.log"


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def watcher_running():
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            parts = [x for x in open(f"/proc/{pid}/cmdline", "rb").read().decode("utf8", "ignore").split("\0") if x]
        except Exception:
            continue
        if len(parts) >= 2 and parts[0] in ("python3", "bash") and "watch_" in parts[1]:
            return True
    return False


def trading_window(now):
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 910 <= hm <= 1505


def plan_path(now):
    return os.path.join(ROOT, "data", f"plan-{now:%Y-%m-%d}.json")


def dashboard_running():
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            parts=[x for x in open(f'/proc/{pid}/cmdline','rb').read().decode('utf8','ignore').split('\0') if x]
        except Exception:
            continue
        if len(parts)>=2 and parts[0]=='python3' and parts[1].endswith('tools/dashboard.py'):
            return True
    return False


def ensure_dashboard():
    if not dashboard_running():
        subprocess.Popen(['python3','tools/dashboard.py'], cwd=ROOT,
                         stdout=open('/tmp/dashboard.log','ab'), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        log('dashboard started')


def main():
    log("session_guard start")
    ensure_dashboard()
    while True:
        now = dt.datetime.now()
        if now.hour >= 15 and now.minute >= 10:
            log("after 15:10, guard exit")
            return 0
        if trading_window(now) and not watcher_running():
            p = plan_path(now)
            if os.path.exists(p):
                subprocess.Popen(["bash", os.path.join(ROOT, "tools", "watch_loop.sh"), p],
                                 cwd=ROOT, stdout=open("/tmp/watch_loop.log", "ab"),
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
                log(f"watcher missing -> started with {os.path.basename(p)}")
            else:
                log(f"plan missing: {p}")
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
