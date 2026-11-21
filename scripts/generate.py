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
SEED_MOVIE = os.path.join(ROOT, "data", "imdb-top250.json")
SEED_TV = os.path.join(ROOT, "data", "imdb-top250-tv.json")
CACHE_MOVIE = os.path.join(ROOT, "data", "cache", "imdb-map.json")
CACHE_TV = os.path.join(ROOT, "data", "cache", "imdb-tv-map.json")
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

IMDB_CHARTS = {
    "movie": "https://www.imdb.com/chart/top/",
    "tv": "https://www.imdb.com/chart/toptv/",
}


def fetch_imdb_ids_live(kind="movie"):
    """尝试在线抓取 IMDb Top250 榜单 HTML（可能被反爬拦截）。"""
    if FORCE_IMDB_SEED:
        return []
    url = IMDB_CHARTS.get(kind, IMDB_CHARTS["movie"])
    try:
        status, body = http_get(url, {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        if status != 200:
            print("[imdb-%s] 在线榜单返回 %s，改用内置种子" % (kind, status))
            return []
        html = body.decode("utf-8", "replace")
        ids, seen = [], set()
        for m in IMDB_ID_RE.finditer(html):
            tid = m.group(0)
            if tid not in seen:
                seen.add(tid)
                ids.append(tid)
        if len(ids) < 100:
            print("[imdb-%s] 在线榜单仅解析到 %d 条，改用内置种子" % (kind, len(ids)))
            return []
        print("[imdb-%s] 在线榜单解析成功：%d 条" % (kind, len(ids)))
        return ids
    except Exception as e:
        print("[imdb-%s] 在线抓取失败（%s），改用内置种子" % (kind, e))
        return []


def load_seed_ids(path):
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    for r in rows:
        if not r.get("id"):
            r["id"] = r.get("titleid")
    rows.sort(key=lambda r: r.get("rank", 10 ** 6))
    return [((r.get("id") or ""), (r.get("title_zh") or r.get("title") or ""))
            for r in rows if (r.get("id") or "").startswith("tt")]


def load_cache(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(path, cache):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)


def imdb_chart(client, kind="movie"):
    """IMDb Top250：电影 / 剧集。优先在线榜，失败用内置种子。"""
    seed_path = SEED_TV if kind == "tv" else SEED_MOVIE
    cache_path = CACHE_TV if kind == "tv" else CACHE_MOVIE

    ids = fetch_imdb_ids_live(kind)
    if not ids:
        seed = load_seed_ids(seed_path)
        ids = [t for t, _ in seed]
        seed_title = {t: n for t, n in seed}
    else:
        seed_title = {}

    cache = load_cache(cache_path)
    items, misses = [], 0
    for tid in ids[:250]:
        hit = cache.get(tid)
        if hit and hit.get("tmdb_id"):
            items.append(hit)
            continue
        try:
            data = client.get("/find/%s" % tid, external_source="imdb_id", language=TMDB_LANG)
        except Exception as e:
            print("[imdb-%s] %s 匹配失败：%s" % (kind, tid, e))
            misses += 1
            continue
        movie_res = (data.get("movie_results") or [None])[0]
        tv_res = (data.get("tv_results") or [None])[0]
        if kind == "tv":
            res, real_kind = (tv_res, "tv") if tv_res else (movie_res, "movie")
        else:
            res, real_kind = (movie_res, "movie") if movie_res else (tv_res, "tv")
        if not res:
            misses += 1
            continue
        item = norm_item(res, real_kind)
        if not item:
            misses += 1
            continue
        cache[tid] = item
        items.append(item)
        time.sleep(0.03)

    save_cache(cache_path, cache)
    print("[imdb-%s] 命中 %d 条，未匹配 %d" % (kind, len(items), misses))
    return dedupe(items)


DATE_TOKEN_RE = re.compile(r"\{today(?:-([0-9]+))?\}")


def resolve_date(value):
    """把参数里的 {today} / {today-30} 替换成当天或 N 天前的日期。"""
    def repl(m):
        offset = int(m.group(1) or 0)
        return (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()
    return DATE_TOKEN_RE.sub(repl, value)


def discover(client, media, params, pages=2):
    """TMDB discover：按原创语言、类型、平台等条件筛片。"""
    items = []
    for page in range(1, max(1, pages) + 1):
        q = {"language": TMDB_LANG, "page": page, "include_adult": "false",
             "sort_by": "popularity.desc"}
        q.update(params or {})
        q = {k: (resolve_date(v) if isinstance(v, str) else v) for k, v in q.items()}
        data = client.get("/discover/%s" % media, **q)
        for r in data.get("results", []):
            items.append(norm_item(r, media))
        if page >= data.get("total_pages", 1):
            break
    return dedupe(items)

def discover_multi(client, cfg=None):
    """多语言合并：国产剧 + 韩剧 + 欧美剧。

    有 quota 时：各语言先取各自热度前 N 条，再按「名次」交叉混排，
    避免某一语言因为 popularity 绝对值高而把其他语言挤出榜单。
    """
    cfg = cfg or {}
    langs = cfg.get("languages") or ["zh", "ko", "en"]
    pages = int(cfg.get("pages", 3))
    total = int(cfg.get("max_items", 50))
    quota = cfg.get("quota") or {}
    base = dict(cfg.get("params") or {})

    pools = {}
    for lang in langs:
        q = dict(base)
        q["with_original_language"] = lang
        pools[lang] = discover(client, "tv", q, pages)

    if quota:
        slots = []
        for li, lang in enumerate(langs):
            n = int(quota.get(lang, 0))
            for rank, it in enumerate(pools.get(lang, [])[:n]):
                slots.append((rank, li, it))
        slots.sort(key=lambda t: (t[0], t[1]))          # 名次优先，其次语言顺序
        ordered = dedupe([t[2] for t in slots])

        if len(ordered) < total:
            used = {(x["tmdb_id"], x["tmdb_type"]) for x in ordered}
            rest = dedupe([x for lang in langs for x in pools.get(lang, [])])
            rest = [x for x in rest if (x["tmdb_id"], x["tmdb_type"]) not in used]
            rest.sort(key=lambda x: x.get("popularity", 0), reverse=True)
            ordered += rest
        return ordered[:total]

    merged = dedupe([x for lang in langs for x in pools.get(lang, [])])
    merged.sort(key=lambda x: x.get("popularity", 0), reverse=True)
    return merged[:total]



# ---------------- B 站国创/番剧榜单 ----------------

BILI_RANK = "https://api.bilibili.com/pgc/web/rank/list"
BILI_CACHE = os.path.join(ROOT, "data", "cache", "bili-map.json")
DOUBAN_SEED = os.path.join(ROOT, "data", "douban-classic.json")
DOUBAN_CACHE = os.path.join(ROOT, "data", "cache", "douban-map.json")


def norm_title(s):
    s = s or ""
    s = re.sub(r"[（(][^)）]*[)）]", "", s)          # 去掉（中配）之类
    s = re.sub(r"第[一二三四五六七八九十0-9]+季", "", s)
    s = re.sub(r"season\s*\d+", "", s, flags=re.I)
    s = re.sub(r"[\s·:：\-—_,，。!！?？'\"“”《》]+", "", s)
    return s.lower()


def bili_rank_rows(season_type=4, day=3):
    url = "%s?day=%s&season_type=%s" % (BILI_RANK, day, season_type)
    status, body = http_get(url, {
        "Referer": "https://www.bilibili.com",
        "Accept": "application/json",
    })
    if status != 200:
        raise RuntimeError("B站榜单返回 %s" % status)
    data = json.loads(body.decode("utf-8", "replace"))
    if data.get("code") not in (0, None):
        raise RuntimeError("B站榜单 code=%s" % data.get("code"))
    return (data.get("result") or {}).get("list") or []


# ---------------- 热播综艺（多平台汇总） ----------------

VARIETY_CACHE = os.path.join(ROOT, "data", "cache", "variety-platform.json")


VARIETY_DETAIL_TTL = 86400


def variety_detail(client, tmdb_id, cache):
    """取一档综艺的播出平台、最近更新日期、状态（带缓存）。"""
    key = str(tmdb_id)
    hit = cache.get(key)
    if isinstance(hit, dict):
        return hit
    info = {"platform": "", "last_air": "", "status": ""}
    try:
        data = client.get("/tv/%d" % tmdb_id, language=TMDB_LANG)
        nets = data.get("networks") or []
        if nets:
            info["platform"] = (nets[0].get("name") or "").strip()
        info["last_air"] = data.get("last_air_date") or ""
        info["status"] = data.get("status") or ""
    except Exception as e:
        print("[variety] 取详情失败 %s：%s" % (tmdb_id, e))
    cache[key] = info
    return info


def hot_variety(client, cfg=None):
    """热播综艺：中文在播综艺按热度排序，再按播出平台做配额混排。

    腾讯/爱奇艺/优酷/芒果的官方榜单都需私有签名，无法定时抓取，
    这里用 TMDB 热度 + networks 平台信息做「多平台汇总」。
    用 last_air_date 判断「还在播」，避免把首播于多年前的长寿综艺漏掉。
    """
    cfg = cfg or {}
    total = int(cfg.get("max_items", 50))
    pages = int(cfg.get("pages", 6))
    per_platform = int(cfg.get("per_platform", 18))
    air_days = int(cfg.get("air_days", 60))

    params = {
        "with_original_language": "zh",
        "with_genres": "10764|10767",
        "vote_count.gte": 1,
        "sort_by": "popularity.desc",
    }
    params.update(cfg.get("params") or {})
    raw = discover(client, "tv", params, pages)
    if not raw:
        return []

    cutoff = (datetime.date.today() - datetime.timedelta(days=air_days)).isoformat()
    cache = load_cache(VARIETY_CACHE)
    items = []
    for it in raw:
        info = variety_detail(client, it["tmdb_id"], cache)
        last = info.get("last_air") or ""
        if not last or last < cutoff:
            continue
        it["platform"] = info.get("platform") or ""
        it["last_air"] = last
        items.append(it)
        time.sleep(0.02)
    save_cache(VARIETY_CACHE, cache)
    print("[variety] 候选 %d 部，近 %d 天仍在更新 %d 部" % (len(raw), air_days, len(items)))

    counts, picked, rest = {}, [], []
    for it in items:
        p = it.get("platform") or "其他"
        if counts.get(p, 0) < per_platform and len(picked) < total:
            counts[p] = counts.get(p, 0) + 1
            picked.append(it)
        else:
            rest.append(it)
    if len(picked) < total:
        picked += rest[:total - len(picked)]

    stat = {}
    for it in picked:
        p = it.get("platform") or "其他"
        stat[p] = stat.get(p, 0) + 1
    print("[variety] 入选 %d，平台分布：%s"
          % (len(picked), "、".join("%s×%d" % (k, v) for k, v in stat.items())))
    return picked


def douban_classic(client):
    """豆瓣高分国漫口碑池：固定顺序的 20 部经典，按标题匹配到 TMDB。"""
    cache = load_cache(DOUBAN_CACHE)
    try:
        with open(DOUBAN_SEED, "r", encoding="utf-8") as f:
            rows = json.load(f).get("items") or []
    except Exception as e:
        print("[douban] 读取口碑池失败：%s" % e)
        return []

    items, misses, miss_names = [], 0, []
    for row in rows:
        title = (row.get("title") or "").strip()
        kind = row.get("type") or "tv"
        if not title:
            continue
            continue
        hit = cache.get(title)
        if hit and hit.get("tmdb_id"):
            items.append(hit)
            continue

        q = norm_title(title)
        best, best_score = None, 0
        try:
            data = client.get("/search/%s" % kind, query=title, language=TMDB_LANG,
                              include_adult="false", page=1)
            for c in (data.get("results") or [])[:10]:
                for n in (c.get("name"), c.get("original_name"),
                          c.get("title"), c.get("original_title")):
                    nn = norm_title(n)
                    if not nn:
                        continue
                    if nn == q:
                        sc = 3
                    elif q and (q in nn or nn in q):
                        sc = 2
                    else:
                        sc = 0
                    if sc > best_score:
                        best, best_score = c, sc
        except Exception as e:
            print("[douban] 搜索「%s」失败：%s" % (title, e))

        if not best or best_score < 2:
            misses += 1
            miss_names.append(title)
            continue
        item = norm_item(best, kind)
        if not item:
            misses += 1
            miss_names.append(title)
            continue
        cache[title] = item
        items.append(item)
        print("[douban] %s -> TMDB %s (%s)" % (title, item["tmdb_id"], item["title"]))
        time.sleep(0.05)

    save_cache(DOUBAN_CACHE, cache)
    print("[douban] 口碑池 %d 部，命中 %d，未匹配 %d %s"
          % (len(rows), len(items), misses, miss_names or ""))
    return items


def bili_cn_anime(client, cfg=None):
    """国创榜单：顺序来自 B 站，条目按标题匹配到 TMDB。"""
    cfg = cfg or {}
    season_type = int(cfg.get("season_type", 4))
    day = int(cfg.get("day", 3))
    total = int(cfg.get("max_items", 50))

    rows = bili_rank_rows(season_type, day)
    cache = load_cache(BILI_CACHE)
    items, misses = [], 0

    for row in rows:
        sid = str(row.get("season_id") or "")
        hit = cache.get(sid)
        if hit and hit.get("tmdb_id"):
            items.append(hit)
            continue

        title = row.get("title") or ""
        if not title:
            misses += 1
            continue
        q = norm_title(title)
        best, best_score = None, 0
        try:
            data = client.get("/search/tv", query=title, language=TMDB_LANG,
                              include_adult="false", page=1)
            for c in (data.get("results") or [])[:10]:
                for n in (c.get("name"), c.get("original_name")):
                    nn = norm_title(n)
                    if not nn:
                        continue
                    if nn == q:
                        sc = 3
                    elif q and (q in nn or nn in q):
                        sc = 2
                    else:
                        sc = 0
                    if sc > best_score:
                        best, best_score = c, sc
        except Exception as e:
            print("[bili] 搜索「%s」失败：%s" % (title, e))

        if not best or best_score < 2:
            misses += 1
            continue
        item = norm_item(best, "tv")
        if not item:
            misses += 1
            continue
        cache[sid] = item
        items.append(item)
        time.sleep(0.05)

    save_cache(BILI_CACHE, cache)
    items = dedupe(items)   # 同一部番的多季会指向同一条 TMDB 记录
    bili_top = int(cfg.get("bili_top", 0) or 0)
    if bili_top and len(items) > bili_top:
        print("[bili] 只取榜单前 %d 条（另有 %d 条排名靠后，略过）" % (bili_top, len(items) - bili_top))
        items = items[:bili_top]
    print("[bili] 榜单 %d 条，映射成功 %d，未匹配 %d" % (len(rows), len(items), misses))

    # 豆瓣口碑池：B 站热榜之后补一段常青高分国漫
    if cfg.get("pool"):
        used = {(x["tmdb_id"], x["tmdb_type"]) for x in items}
        pool = [x for x in douban_classic(client)
                if (x["tmdb_id"], x["tmdb_type"]) not in used]
        if pool:
            items += pool[:int(cfg.get("pool_max", 20))]
            print("[douban] 口碑池补充 %d 条" % len(pool[:int(cfg.get("pool_max", 20))]))

    if len(items) < total:
        extra = discover(client, "tv", {
            "with_original_language": "zh",
            "with_genres": 16,
            "without_genres": "10762",
            "vote_count.gte": 1,
            "sort_by": "first_air_date.desc",
            "first_air_date.lte": datetime.date.today().isoformat(),
        }, 5)
        used = {(x["tmdb_id"], x["tmdb_type"]) for x in items}
        items += [x for x in extra if (x["tmdb_id"], x["tmdb_type"]) not in used]

    return items[:total]


def imdb_top250(client, cfg=None):
    return imdb_chart(client, "movie")


def imdb_top250_tv(client, cfg=None):
    return imdb_chart(client, "tv")


def discover_tv(client, cfg=None):
    return discover(client, "tv", (cfg or {}).get("params"), int((cfg or {}).get("pages", 2)))


def discover_movie(client, cfg=None):
    return discover(client, "movie", (cfg or {}).get("params"), int((cfg or {}).get("pages", 2)))


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
    "imdb_top250_tv": lambda c, cfg: imdb_top250_tv(c),
    "tmdb_discover_tv": lambda c, cfg: discover_tv(c, cfg),
    "tmdb_discover_movie": lambda c, cfg: discover_movie(c, cfg),
    "tmdb_discover_multi": lambda c, cfg: discover_multi(c, cfg),
    "bili_cn_anime": lambda c, cfg: bili_cn_anime(c, cfg),
    "hot_variety": lambda c, cfg: hot_variety(c, cfg),
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
