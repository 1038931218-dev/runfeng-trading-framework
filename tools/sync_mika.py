#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同步交接包到手机存储（给 Mika 等外部执行器读）· 2026-11-07。

把关键文件复制到 <共享目录>/（Mika 可读），并刷新两份运行输出。
用法: python3 tools/sync_mika.py [--quiet]
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = os.environ.get("MIKA_SHARE_DIR", "<共享目录>")

FILES = [
    "TEAM.md", "README.md",
    "research/05-account-execution.md", "research/07-trade-sop.md",
    "research/08-intraday-watch.md", "research/10-external-agent-brief.md",
    "exports/deepseek-agent-kit.md", "exports/deepseek-system-prompt.txt",
    "data/watchlist.json", "data/account-state.json",
    "data/index-tech-crosscheck.json",
]


def dynamic_files():
    """按日期滚动的最新文件（避免硬编码日期导致次日脱节）。"""
    import glob
    out = []
    for pattern in ("data/brief-*.json", "data/risk-*.json", "data/plan-*.json",
                    "models/portfolio-*.json", "models/screen-*.csv"):
        for fp in sorted(glob.glob(os.path.join(ROOT, pattern))):
            out.append(os.path.relpath(fp, ROOT))
    return out


def main():
    quiet = "--quiet" in sys.argv
    if not os.path.isdir(os.path.dirname(DST)):
        print(f"SKIP 手机存储不可用: {DST}")
        return 0
    os.makedirs(DST, exist_ok=True)
    n = 0
    for rel in FILES + dynamic_files():
        src = os.path.join(ROOT, rel)
        dst = os.path.join(DST, rel)
        if not os.path.exists(src):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    # 工具源码
    tools_dst = os.path.join(DST, "tools")
    os.makedirs(tools_dst, exist_ok=True)
    for fn in os.listdir(os.path.join(ROOT, "tools")):
        if fn.endswith(".py"):
            shutil.copy2(os.path.join(ROOT, "tools", fn), os.path.join(tools_dst, fn))
            n += 1
    # 运行输出
    for script, out in (("daily_brief.py", "daily_brief_output.txt"),
                        ("selftest.py", "selftest_output.txt")):
        try:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", script)],
                               capture_output=True, text=True, cwd=ROOT, timeout=180)
            with open(os.path.join(DST, out), "w", encoding="utf-8") as f:
                f.write(r.stdout + r.stderr)
            n += 1
        except Exception as e:  # noqa
            print(f"WARN {script}: {e}")
    if not quiet:
        print(f"SYNC OK {n} 个文件 → {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
