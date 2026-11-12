#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开机自检：确保看板服务 / Mika 桥接 / 交易时段守护 在跑（引擎或 App 启动时调用）。"""
import datetime as dt
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def alive(marker):
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            parts = [x for x in open(f'/proc/{pid}/cmdline', 'rb').read().decode('utf8', 'ignore').split('\0') if x]
        except Exception:
            continue
        if any(x.endswith(marker) for x in parts):
            return True
    return False


def start(script):
    try:
        subprocess.Popen(['python3', os.path.join(ROOT, script)], cwd=ROOT,
                         stdout=open(f'/tmp/{os.path.basename(script)}.log', 'ab'),
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except Exception:
        return False


def main():
    out = []
    for script, marker in [('tools/dashboard.py', 'dashboard.py'),
                           ('tools/mika_bridge.py', 'mika_bridge.py')]:
        if alive(marker):
            out.append(f"{script}=running")
        else:
            out.append(f"{script}={'started' if start(script) else 'fail'}")
    now = dt.datetime.now()
    hm = now.hour * 100 + now.minute
    if now.weekday() < 5 and 900 <= hm <= 1510:
        if alive('session_guard.py'):
            out.append("session_guard=running")
        else:
            out.append(f"session_guard={'started' if start('tools/session_guard.py') else 'fail'}")
    print("BOOT_CHECK " + "；".join(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
