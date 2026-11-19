#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMOS 动态片单生成器
按 EMOS 官方规范（https://wiki.emos.best/api/watch）输出 JSON：

{
  "name": "片单标题(1-50字)",
  "cover": "封面图地址",
  "updated_at": "YYYY-MM-DD HH:MM:SS",
  "videos": [{"tmdb_id": 278, "tmdb_type": "movie", "title": "肖申克的救赎", "sort": 1}]
}

只用 Python 标准库，无需 pip install。
"""

import json
import os
import re
import sys
import time
import datetime
import urllib.parse
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "lists.json")
SEED_PATH = os.path.join(ROOT, "data", "imdb-top250.json")
CACHE_PATH = os.path.join(ROOT, "data", "cache", "imdb-map.json")
OUT_DIR = os.path.join(ROOT, "docs", "watch")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TMDB_BASE = os.environ.get("TMDB_API_BASE", "https://api.themoviedb.org/3").rstrip("/")
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()
TMDB_TOKEN = os.environ.get("TMDB_READ_TOKEN", "").strip()
TMDB_LANG = os.environ.get("TMDB_LANG", "zh-CN").strip() or "zh-CN"
TMDB_REGION = os.environ.get("TMDB_REGION", "CN").strip()
TMDB_IMAGE_BASE = os.environ.get("TMDB_IMAGE_BASE", "https://image.tmdb.org/t/p").rstrip("/")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
FORCE_IMDB_SEED = os.environ.get("FORCE_IMDB_SEED", "0") == "1"

RETRIES = 3
TIMEOUT = 25


# ---------------- HTTP ----------------

def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA}, **(headers or {})))
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            code = e.code
            if code == 429 and attempt < RETRIES - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return code, e.read()
        except Exception as e:  # 网络异常 / 超时
            if attempt < RETRIES - 1:
                time.sleep(1 + attempt)
                continue
            raise RuntimeError("请求失败: %s (%s)" % (url, e))
    raise RuntimeError("请求失败: %s" % url)


class TMDB:
    def __init__(self):
        if not TMDB_KEY and not TMDB_TOKEN:
            raise RuntimeError(
                "缺少 TMDB_API_KEY（或 TMDB_READ_TOKEN）。请在仓库 Secrets 中配置后重跑。"
            )
        self.calls = 0

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v not in (None, "")}
        url = "%s%s?%s" % (TMDB_BASE, path, urllib.parse.urlencode(params))
        headers = {"Accept": "application/json"}
        if TMDB_TOKEN:
            headers["Authorization"] = "Bearer %s" % TMDB_TOKEN
        else:
            url += "&api_key=%s" % TMDB_KEY
        status, body = http_get(url, headers)
        self.calls += 1
        if status != 200:
            raise RuntimeError("TMDB %s 返回 %s: %s" % (path, status, body[:200]))
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except Exception:
            raise RuntimeError("TMDB %s 返回非 JSON" % path)


# ---------------- 数据清洗 ----------------

def image_url(path, size="w780"):
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return "%s/%s%s" % (TMDB_IMAGE_BASE, size, path)


def norm_item(raw, kind):
    """把 TMDB 原始对象统一成内部结构。"""
    if not raw:
        return None
    tmdb_id = raw.get("id")
    if not isinstance(tmdb_id, int):
        return None
    title = (raw.get("title") or raw.get("name") or raw.get("original_title")
             or raw.get("original_name") or "").strip()
    if not title:
        return None
    return {
        "tmdb_id": tmdb_id,
        "tmdb_type": kind,
        "title": title,
        "poster": image_url(raw.get("poster_path") or raw.get("backdrop_path")),
        "popularity": raw.get("popularity") or 0,
    }


def dedupe(items):
    seen, out = set(), []
    for it in items:
        if not it:
            continue
        key = (it["tmdb_id"], it["tmdb_type"])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def trending(client, window):
    data = client.get("/trending/all/%s" % window, language=TMDB_LANG)
    items = []
    for r in data.get("results", []):
        mt = r.get("media_type")
        if mt not in ("movie", "tv"):
            continue
        items.append(norm_item(r, mt))
    return dedupe(items)


def now_playing(client):
    items = []
    for page in (1, 2):
        data = client.get("/movie/now_playing", language=TMDB_LANG,
                          region=TMDB_REGION or None, page=page)
        for r in data.get("results", []):
            items.append(norm_item(r, "movie"))
        if page >= data.get("total_pages", 1):
            break
    return dedupe(items)


def on_the_air(client):
    items = []
    data = client.get("/tv/on_the_air", language=TMDB_LANG, page=1)
    for r in data.get("results", []):
        items.append(norm_item(r, "tv"))
    return dedupe(items)


# ---------------- IMDb Top250 ----------------

IMDB_ID_RE = re.compile(r"tt\d{7,8}")


def fetch_imdb_ids_live():
    """尝试在线抓取 IMDb Top250 榜单 HTML（可能被反爬拦截）。"""
    if FORCE_IMDB_SEED:
        return []
    url = "https://www.imdb.com/chart/top/"
    try:
        status, body = http_get(url, {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        if status != 200:
            print("[imdb] 在线榜单返回 %s，改用内置种子" % status)
            return []
        html = body.decode("utf-8", "replace")
        ids = []
        seen = set()
        for m in IMDB_ID_RE.finditer(html):
            tid = m.group(0)
            if tid not in seen:
                seen.add(tid)
                ids.append(tid)
        if len(ids) < 100:
            print("[imdb] 在线榜单仅解析到 %d 条，改用内置种子" % len(ids))
            return []
        print("[imdb] 在线榜单解析成功：%d 条" % len(ids))
        return ids
    except Exception as e:
        print("[imdb] 在线抓取失败（%s），改用内置种子" % e)
        return []


def load_seed_ids():
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        rows = json.load(f)
    for r in rows:
        # 兼容不同来源字段：id / titleid
        if not r.get("id"):
            r["id"] = r.get("titleid")
    rows.sort(key=lambda r: r.get("rank", 10 ** 6))
    return [((r.get("id") or ""), (r.get("title_zh") or r.get("title") or ""))
            for r in rows if (r.get("id") or "").startswith("tt")]


def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)


def imdb_top250(client):
    ids = fetch_imdb_ids_live()
    if not ids:
        seed = load_seed_ids()
        ids = [t for t, _ in seed]
        seed_title = {t: n for t, n in seed}
    else:
        seed_title = {}

    cache = load_cache()
    items, misses = [], 0
    for tid in ids[:250]:
        hit = cache.get(tid)
        if hit and hit.get("tmdb_id"):
            items.append(hit)
            continue
        try:
            data = client.get("/find/%s" % tid, external_source="imdb_id", language=TMDB_LANG)
        except Exception as e:
            print("[imdb] %s 匹配失败：%s" % (tid, e))
            misses += 1
            continue
        res = (data.get("movie_results") or [None])[0]
        if res is None:
            res = (data.get("tv_results") or [None])[0]
        kind = "movie" if data.get("movie_results") else "tv"
        item = norm_item(res, kind) if res else None
        if not item:
            misses += 1
            continue
        if not item["title"] and seed_title.get(tid):
            item["title"] = seed_title[tid]
        cache[tid] = item
        items.append(item)
        time.sleep(0.03)

    save_cache(cache)
    print("[imdb] 命中 %d 条，未匹配 %d 条" % (len(items), misses))
    return dedupe(items)


# ---------------- 输出 ----------------

def build_payload(name, items, max_items, cover_override, now_str):
    trimmed = items[:max_items]
    videos = []
    for idx, it in enumerate(trimmed):
        videos.append({
            "tmdb_id": int(it["tmdb_id"]),
            "tmdb_type": it["tmdb_type"],
            "title": it["title"][:120],
            "sort": min(idx + 1, 100),   # 规范要求 1-100，越小越靠前
        })
    if cover_override:
        cover = cover_override
    elif trimmed and trimmed[0].get("poster"):
        cover = trimmed[0]["poster"]
    else:
        cover = "%s/assets/cover-default.svg" % PUBLIC_BASE_URL if PUBLIC_BASE_URL else ""
    return {
        "name": name[:50],
        "cover": cover,
        "updated_at": now_str,
        "videos": videos,
    }


SOURCES = {
    "tmdb_trending_day": lambda c, cfg: trending(c, "day"),
    "tmdb_trending_week": lambda c, cfg: trending(c, "week"),
    "tmdb_now_playing": lambda c, cfg: now_playing(c),
    "tmdb_on_the_air": lambda c, cfg: on_the_air(c),
    "imdb_top250": lambda c, cfg: imdb_top250(c),
}


def main():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    os.makedirs(OUT_DIR, exist_ok=True)
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    now_str = now_str.strftime("%Y-%m-%d %H:%M:%S")

    try:
        client = TMDB()
    except RuntimeError as e:
        print("[fatal] %s" % e)
        return 2

    manifest, failed, ok = [], [], 0
    for entry in cfg["lists"]:
        slug = entry["slug"]
        name = entry["name"]
        source = entry["source"]
        max_items = int(entry.get("max_items", cfg.get("default_max_items", 100)))
        out_path = os.path.join(OUT_DIR, slug + ".json")
        try:
            fn = SOURCES[source]
        except KeyError:
            print("[skip] 未知数据源：%s" % source)
            failed.append(slug)
            continue
        try:
            items = fn(client, entry)
        except Exception as e:
            print("[fail] %s：%s" % (slug, e))
            failed.append(slug)
            items = []

        if not items:
            # 失败不覆盖旧文件，保证线上接口始终可用
            if os.path.exists(out_path):
                print("[keep] %s 生成失败，保留上一版数据" % slug)
                with open(out_path, "r", encoding="utf-8") as f:
                    old = json.load(f)
                manifest.append({"slug": slug, "name": old.get("name", name),
                                 "file": "watch/%s.json" % slug,
                                 "videos": len(old.get("videos", [])),
                                 "updated_at": old.get("updated_at", "")})
            else:
                print("[warn] %s 无数据（缺少密钥或接口异常）" % slug)
            continue

        payload = build_payload(name, items, max_items, entry.get("cover", ""), now_str)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        ok += 1
        print("[ok] %s：%d 条 -> %s" % (slug, len(payload["videos"]), out_path))
        manifest.append({"slug": slug, "name": payload["name"],
                         "file": "watch/%s.json" % slug,
                         "videos": len(payload["videos"]),
                         "updated_at": payload["updated_at"]})

    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"updated_at": now_str, "lists": manifest}, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("[done] 成功 %d / 共 %d，TMDB 调用 %d 次" % (ok, len(cfg["lists"]), client.calls))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
