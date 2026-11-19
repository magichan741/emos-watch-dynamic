#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 EMOS 规范校验 docs/watch/*.json，非 0 退出表示有文件不合规。"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH_DIR = os.path.join(ROOT, "docs", "watch")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

errors = []
checked = 0

for name in sorted(os.listdir(WATCH_DIR)):
    if not name.endswith(".json") or name == "index.json":
        continue
    path = os.path.join(WATCH_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    checked += 1

    nm = doc.get("name")
    if not isinstance(nm, str) or not (1 <= len(nm) <= 50):
        errors.append("%s: name 必须为 1-50 字符串" % name)
    cover = doc.get("cover")
    if not isinstance(cover, str) or not cover.startswith("http"):
        errors.append("%s: cover 必须是 http(s) 图片地址" % name)
    upd = doc.get("updated_at")
    if not isinstance(upd, str) or not TIME_RE.match(upd):
        errors.append("%s: updated_at 必须是 YYYY-MM-DD HH:MM:SS" % name)

    videos = doc.get("videos")
    if not isinstance(videos, list) or not videos:
        errors.append("%s: videos 不能为空" % name)
        continue
    seen = set()
    for i, v in enumerate(videos):
        if not isinstance(v.get("tmdb_id"), int) or v["tmdb_id"] <= 0:
            errors.append("%s: videos[%d] tmdb_id 必须为正整数" % (name, i))
        if v.get("tmdb_type") not in ("movie", "tv"):
            errors.append("%s: videos[%d] tmdb_type 只能是 movie/tv" % (name, i))
        t = v.get("title")
        if not isinstance(t, str) or not t.strip():
            errors.append("%s: videos[%d] title 为空" % (name, i))
        s = v.get("sort")
        if not isinstance(s, int) or not (1 <= s <= 100):
            errors.append("%s: videos[%d] sort 必须在 1-100" % (name, i))
        key = (v.get("tmdb_id"), v.get("tmdb_type"))
        if key in seen:
            errors.append("%s: videos[%d] 重复条目 %s" % (name, i, key))
        seen.add(key)

if errors:
    print("校验失败：")
    for e in errors[:40]:
        print("  -", e)
    sys.exit(1)

print("校验通过：%d 个片单文件全部符合 EMOS /api/watch 规范" % checked)
