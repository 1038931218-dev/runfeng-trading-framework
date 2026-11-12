#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把报表导出到手机可打开目录（/sdcard/Download/DSH-Go/财务/）并生成 index.html。"""
import glob
import html
import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = "/sdcard/Download/DSH-Go/财务"


def md_to_html(md):
    lines, out, in_list, in_code = md.splitlines(), [], False, False
    for ln in lines:
        if ln.startswith("```"):
            in_code = not in_code
            out.append("<pre>" if in_code else "</pre>")
            continue
        if in_code:
            out.append(html.escape(ln))
            continue
        if re.match(r"^#{1,6}\s", ln):
            n = len(ln) - len(ln.lstrip("#"))
            out.append(f"<h{n}>{html.escape(ln[n:].strip())}</h{n}>")
        elif ln.strip().startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{html.escape(ln.strip()[2:])}</li>")
        elif ln.strip().startswith("|"):
            out.append(f"<div class=row>{html.escape(ln)}</div>")
        elif not ln.strip():
            if in_list:
                out.append("</ul>"); in_list = False
            out.append("<br>")
        else:
            out.append(f"<p>{html.escape(ln)}</p>")
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
    body = re.sub(r"`(.+?)`", r"<code>\1</code>", body)
    body = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', body)
    return body


def main():
    os.makedirs(OUT, exist_ok=True)
    items = []
    for pat in ("reports/*.md", "reports/*.xlsx"):
        for p in sorted(glob.glob(os.path.join(ROOT, pat))):
            name = os.path.basename(p)
            if name.endswith(".md"):
                with open(p, encoding="utf-8") as f:
                    body = md_to_html(f.read())
                dst = os.path.join(OUT, name[:-3] + ".html")
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(f"<!doctype html><meta charset=utf-8><title>{name}</title>"
                            f"<style>body{{font:16px/1.6 -apple-system,sans-serif;padding:14px;"
                            f"background:#111;color:#eee}}b{{color:#f5c542}}code{{background:#222}}"
                            f".row{{font-family:monospace;font-size:12px}}a{{color:#6cf}}"
                            f"h1,h2,h3{{color:#f5c542}}</style>" + body)
                items.append((name[:-3] + ".html", name))
            else:
                shutil.copy2(p, os.path.join(OUT, name))
                items.append((name, name))
    rows = "\n".join(
        f'<li><a href="{html.escape(u)}">{html.escape(t)}</a></li>' for u, t in items)
    index = f"""<!doctype html><meta charset=utf-8><title>润锋财务/交易报表</title>
<style>body{{font:17px/1.7 -apple-system,sans-serif;background:#111;color:#eee;padding:18px}}
h1{{color:#f5c542}}a{{color:#6cf;text-decoration:none}}li{{margin:10px 0}}</style>
<h1>润锋报表中心</h1><p>点击查看；更新于 {__import__('datetime').datetime.now():%Y-%m-%d %H:%M}</p><ul>{rows}</ul>"""
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(index)
    # 单文件版：所有 md 报表内嵌，放下载根目录，方便一键打开
    parts = []
    for p in sorted(glob.glob(os.path.join(ROOT, "reports/*.md"))):
        name = os.path.basename(p)
        with open(p, encoding="utf-8") as f:
            parts.append(f"<details><summary><b>{html.escape(name)}</b></summary>{md_to_html(f.read())}</details>")
    single = f"""<!doctype html><meta charset=utf-8><title>润锋报表中心</title>
<style>body{{font:16px/1.6 -apple-system,sans-serif;background:#111;color:#eee;padding:14px}}
summary{{padding:10px 0;border-bottom:1px solid #333}}b{{color:#f5c542}}
h1,h2,h3{{color:#f5c542}}a{{color:#6cf}}code{{background:#222}}</style>
<h1>润锋报表中心（单文件版）</h1><p>更新 {__import__('datetime').datetime.now():%Y-%m-%d %H:%M}｜共 {len(parts)} 份报表，点标题展开</p>
""" + "\n".join(parts)
    with open("/sdcard/Download/润锋报表中心.html", "w", encoding="utf-8") as f:
        f.write(single)
    print(f"EXPORT {len(items)} 个文件 → {OUT}/index.html；单文件 → /sdcard/Download/润锋报表中心.html")


if __name__ == "__main__":
    main()
