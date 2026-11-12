#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mika 桥接守护（文件队列 RPC）· 2026-11-07。

让手机上的外部执行器（Mika）通过写请求文件的方式，调用本团队的全部工具
（行情/K线/brief/selftest/风控/计划/guard/监控启停），效果等价于「它自己会跑脚本」。
纯 Python 常驻、不调用 LLM，零 token。

目录（默认 <共享目录>）：
  inbox/<id>.json   ← Mika 写入请求 {id,cmd,args,timeout}
  outbox/<id>.json  ← 守护写入结果 {id,rc,stdout,stderr,ms}
  outbox/last.json  ← 最近一次结果（方便 Mika 直接读）
  outbox/heartbeat.json ← 心跳（每 10 秒）

用法:
  python3 tools/mika_bridge.py            # 前台
  nohup python3 tools/mika_bridge.py >/dev/null 2>&1 &   # 常驻
  python3 tools/mika_bridge.py --once     # 处理一轮后退出（调试）
"""
import json
import os
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("MIKA_SHARE_DIR", "<共享目录>")
INBOX = os.path.join(BASE, "inbox")
OUTBOX = os.path.join(BASE, "outbox")

# 只允许白名单命令；args 会作为列表参数传给对应脚本，绝不经过 shell 拼接
WHITELIST = {
    "quote":   [sys.executable, "tools/market.py", "quote"],
    "index":   [sys.executable, "tools/market.py", "index"],
    "kline":   [sys.executable, "tools/market.py", "kline"],
    "snapshot": [sys.executable, "tools/market.py", "snapshot"],
    "brief":   [sys.executable, "tools/daily_brief.py"],
    "selftest": [sys.executable, "tools/selftest.py"],
    "risk":    [sys.executable, "tools/risk_metrics.py"],
    "plan":    [sys.executable, "tools/make_plan.py"],
    "guard":   [sys.executable, "tools/daily_ops.py", "guard"],
    "sync":    [sys.executable, "tools/sync_mika.py"],
}
# ops 子命令需要二次白名单
OPS = {"open", "check", "ack", "close"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_request(req):
    req_id = str(req.get("id") or f"req-{int(time.time()*1000)}")
    cmd = str(req.get("cmd") or "").strip().lower()
    args = req.get("args") or []
    timeout = int(req.get("timeout") or 300)
    if not isinstance(args, list):
        args = [str(args)]
    args = [str(a) for a in args]
    started = time.time()
    try:
        if cmd == "ops":
            sub = (args[0] if args else "").lower()
            if sub not in OPS:
                raise ValueError(f"ops 子命令不允许: {sub}")
            base = [sys.executable, "tools/daily_ops.py", sub] + args[1:]
        elif cmd in ("watch_start", "watch_stop", "watch_status"):
            base = handle_watch(cmd, args)
            if base is None:
                return dict(id=req_id, rc=0, stdout="watch ok", stderr="", ms=0)
        elif cmd == "read":
            return handle_read(req_id, args, started)
        elif cmd in WHITELIST:
            base = WHITELIST[cmd] + args
        else:
            raise ValueError(f"命令不在白名单: {cmd}")
        p = subprocess.run(base, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        out = dict(id=req_id, cmd=cmd, rc=p.returncode,
                   stdout=p.stdout[-20000:], stderr=p.stderr[-4000:],
                   ms=int((time.time() - started) * 1000))
    except subprocess.TimeoutExpired:
        out = dict(id=req_id, cmd=cmd, rc=124, stdout="", stderr=f"timeout>{timeout}s",
                   ms=int((time.time() - started) * 1000))
    except Exception as e:  # noqa
        out = dict(id=req_id, cmd=cmd, rc=1, stdout="", stderr=f"{type(e).__name__}: {e}",
                   ms=int((time.time() - started) * 1000))
    return out


def handle_watch(cmd, args):
    """监控启停：用 pkill/nohup 控制 watch_loop.sh。"""
    if cmd == "watch_start":
        plan = args[0] if args else f"data/plan-{time.strftime('%Y-%m-%d')}.json"
        subprocess.Popen(["bash", "tools/watch_loop.sh", plan], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        time.sleep(1)
        return None
    if cmd == "watch_stop":
        subprocess.run(["pkill", "-f", "watch_loop.sh"], capture_output=True)
        subprocess.run(["pkill", "-f", "watch_intraday.py"], capture_output=True)
        return None
    # status
    p = subprocess.run(["pgrep", "-af", "[w]atch_(intraday|loop)"], capture_output=True, text=True)
    return None


def handle_read(req_id, args, started):
    rel = args[0] if args else ""
    path = os.path.abspath(os.path.join(ROOT, rel))
    if not path.startswith(ROOT + os.sep):
        raise ValueError("禁止读取项目外文件")
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    return dict(id=req_id, cmd="read", rc=0, stdout=txt[-30000:], stderr="",
                ms=int((time.time() - started) * 1000))


def write_result(res):
    os.makedirs(OUTBOX, exist_ok=True)
    p = os.path.join(OUTBOX, f"{res['id']}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUTBOX, "last.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)


def heartbeat(interval=10):
    now = time.time()
    if now - heartbeat.last < interval:
        return
    heartbeat.last = now
    os.makedirs(OUTBOX, exist_ok=True)
    with open(os.path.join(OUTBOX, "heartbeat.json"), "w", encoding="utf-8") as f:
        json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(),
                   "inbox": INBOX, "root": ROOT}, f, ensure_ascii=False)
    # 清理超过 3 天的结果
    try:
        for fn in os.listdir(OUTBOX):
            if fn.endswith(".json") and fn not in ("last.json", "heartbeat.json"):
                fp = os.path.join(OUTBOX, fn)
                if now - os.path.getmtime(fp) > 259200:
                    os.remove(fp)
    except OSError:
        pass


heartbeat.last = 0


def main():
    once = "--once" in sys.argv
    os.makedirs(INBOX, exist_ok=True)
    os.makedirs(OUTBOX, exist_ok=True)
    heartbeat()
    log(f"mika_bridge started: inbox={INBOX}")
    while True:
        try:
            names = sorted(n for n in os.listdir(INBOX) if n.endswith(".json"))
            for n in names:
                fp = os.path.join(INBOX, n)
                try:
                    with open(fp, encoding="utf-8") as f:
                        req = json.load(f)
                    os.remove(fp)  # 先取走，避免重复执行
                    res = run_request(req)
                    write_result(res)
                    log(f"done {res.get('id')} cmd={res.get('cmd')} rc={res.get('rc')} {res.get('ms')}ms")
                except Exception as e:  # noqa
                    log(f"bad request {n}: {e}")
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
            heartbeat()
            if once:
                return 0
            time.sleep(2)
        except KeyboardInterrupt:
            return 0
        except Exception:  # noqa
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
