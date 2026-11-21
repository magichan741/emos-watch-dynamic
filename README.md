# EMOS 动态片单生成器（GitHub Actions + GitHub Pages）

按 [EMOS 官方规范](https://wiki.emos.best/api/watch) 生成动态片单 JSON，GitHub Actions 定时更新，GitHub Pages 托管，直接作为 `GET` 接口填进 EMOS 后台。

| 片单 | 接口文件 | 数据源 |
| --- | --- | --- |
| TMDB 每日热门 | `/watch/tmdb-daily.json` | 20 条，TMDB `/trending/all/day` |
| IMDb Top250 电影 | `/watch/imdb-top250.json` | 250 条，IMDb 电影榜 + TMDB `/find` 映射 |
| IMDb Top250 剧集 | `/watch/imdb-top250-tv.json` | 250 条，IMDb 剧集榜 + TMDB `/find` 映射 |
| 最新电影 | `/watch/latest-movies.json` | 400 条，上映日期倒序 + `vote_count.gte=5` |
| 最新剧集 | `/watch/latest-tv.json` | 40 条，近 45 天首播 + 按热度排序 |
| 最新国产电视剧 | `/watch/latest-cn-tv.json` | 50 条，`zh` + 排除动画/综艺 |
| 最新欧美电视剧 | `/watch/latest-western-tv.json` | 50 条，`en|es|fr|de|it|pt` 合并 |
| 最新日韩电视剧 | `/watch/latest-jp-kr-tv.json` | 50 条，`ja|ko` 合并 |
| 最新更新国漫 | `/watch/latest-cn-anime.json` | 50 条，`zh` 动画按首播日期倒序 |
| 最新日漫 | `/watch/latest-jp-anime.json` | 50 条，`ja` + `with_genres=16` |
| 最新综艺 | `/watch/latest-variety.json` | 50 条，`zh` + Reality/Talk |

接口地址形如：

```
https://<用户名>.github.io/emos-watch-dynamic/watch/tmdb-daily.json
```

返回结构完全遵循规范（只带 TMDB ID，不含播放地址）：

```json
{
  "name": "TMDB 每日热门",
  "cover": "https://image.tmdb.org/t/p/w780/xxx.jpg",
  "updated_at": "2026-11-19 07:30:12",
  "videos": [
    { "tmdb_id": 1024, "tmdb_type": "tv",   "title": "电视标题", "sort": 1 },
    { "tmdb_id": 2048, "tmdb_type": "movie","title": "电影标题", "sort": 2 }
  ]
}
```

> ⚠️ 关键点：`videos` 只有 TMDB ID，**对应影片必须已经在 EMOS 媒体库里**才会显示；没有入库就自动跳过。`sort` 规范要求 1–100（越小越靠前），超过 100 条的榜单自动截断排序值。

---

## 一、准备：TMDB API Key

1. 注册 [themoviedb.org](https://www.themoviedb.org/signup) 账号
2. 头像 → Settings → API → 申请 **Developer** key
3. 复制 **API Key (v3 auth)**（32 位字符串），或 API Read Access Token (v4)

---

## 二、部署（三种方式，任选一种）

### 方式 A：一键脚本（推荐，需 GitHub CLI）

```bash
# macOS: brew install gh ；Windows: winget install GitHub.cli ；Linux: 见官网
gh auth login

cd emos-watch-dynamic
bash push.sh emos-watch-dynamic public
```

脚本会自动完成：建仓库 → 推送 → 开启 Pages（Actions 模式）→ 写入 `TMDB_API_KEY` 密钥 → 触发首次构建，最后打印接口地址。

### 方式 B：纯网页操作（不用命令行）

1. 在 GitHub 新建仓库 `emos-watch-dynamic`，**Public**（私有仓库的 Pages 需要付费账号）
2. 把本目录所有文件上传（Add file → Upload files，注意要包含 `.github/workflows/update.yml`）
3. 仓库 → Settings → Secrets and variables → Actions → **New repository secret**
   - Name：`TMDB_API_KEY`
   - Value：你的 v3 Key
4. 仓库 → Settings → Pages → Build and deployment → Source 选 **GitHub Actions**
5. 仓库 → Actions → `Update EMOS Dynamic Lists` → **Run workflow**

### 方式 C：本地 git 推送

```bash
git init -b main
git add -A && git commit -m "init"
git remote add origin https://github.com/<你>/emos-watch-dynamic.git
git push -u origin main
```

之后按方式 B 的第 3–5 步配置密钥与 Pages。

---

## 三、验证接口

首次构建完成后：

```bash
curl https://<用户名>.github.io/emos-watch-dynamic/watch/tmdb-daily.json
```

或在浏览器打开 `https://<用户名>.github.io/emos-watch-dynamic/`，页面会列出 5 个片单和可复制的地址。

---

## 四、填进 EMOS

EMOS 后台 → 片单 → 新建「动态片单」→ 抓取地址分别填：

```
https://<用户名>.github.io/emos-watch-dynamic/watch/tmdb-daily.json
https://<用户名>.github.io/emos-watch-dynamic/watch/imdb-top250.json
https://<用户名>.github.io/emos-watch-dynamic/watch/weekly-picks.json
https://<用户名>.github.io/emos-watch-dynamic/watch/latest-movies.json
https://<用户名>.github.io/emos-watch-dynamic/watch/latest-tv.json
```

EMOS 每日 **08:00 / 17:00** 抓取；本工作流在北京时间 **07:30 / 16:30** 生成，正好赶在抓取前完成更新。

> 如果家里 EMOS 访问 `github.io` 不稳（移动宽带常见），可在软路由/OpenWrt 上给 `*.github.io` 做反代或走 IPv6，或把 JSON 镜像到自己的域名（见下节）。

---

## 五、自定义

`config/lists.json` 增加一条即可新增片单：

```json
{ "slug": "netflix-top", "name": "Netflix 热门", "source": "tmdb_trending_week", "max_items": 50 }
```

按语言/类型/平台筛片用 discover，`params` 直接透传 TMDB 参数：

```json
{
  "slug": "thriller",
  "name": "悬疑片精选",
  "source": "tmdb_discover_movie",
  "pages": 2,
  "max_items": 40,
  "params": { "with_genres": 53, "vote_average.gte": 7.5, "vote_count.gte": 200 }
}
```

可用 `source`：`tmdb_trending_day`、`tmdb_trending_week`、`tmdb_now_playing`、`tmdb_on_the_air`、`imdb_top250`、`imdb_top250_tv`、`tmdb_discover_movie`、`tmdb_discover_tv`、`tmdb_discover_multi`。

`tmdb_discover_multi` 用于多区域合并榜，靠 `languages` + `quota` 控制比例（`quota` 缺失时退化为纯热度排序，容易被单一语言霸榜）：

```json
{
  "slug": "hot-dramas",
  "name": "热门剧集",
  "source": "tmdb_discover_multi",
  "languages": ["zh", "ko", "en"],
  "pages": 4,
  "max_items": 50,
  "quota": { "zh": 20, "ko": 15, "en": 15 }
}
```

常用类型号：动作 28 / 喜剧 35 / 恐怖 27 / 动画 16 / 悬疑 53 / 科幻 878 / 纪录 99 / 爱情 10749；语言用 `zh`、`ko`、`ja`、`en`。

支持的环境变量（写进 workflow 的 `env:`）：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `TMDB_API_KEY` | v3 Key（Secret） | — |
| `TMDB_READ_TOKEN` | v4 Token，可替代 v3 Key | — |
| `TMDB_LANG` | 标题语言 | `zh-CN` |
| `TMDB_REGION` | `now_playing` 上映地区 | `CN` |
| `TMDB_IMAGE_BASE` | 图片域名，国内可换成自己的 TMDB 图片反代 | `https://image.tmdb.org/t/p` |
| `PUBLIC_BASE_URL` | 无海报时的兜底封面前缀 | 自动用 Pages 域名 |
| `FORCE_IMDB_SEED` | 设为 `1` 时不抓 IMDb 线上榜，直接用内置种子 | `0` |

其他可调：

- **条数**：`max_items`
- **封面**：条目里加 `"cover": "https://..."` 覆盖「用第一条海报当封面」的默认逻辑
- **调度**：改 `.github/workflows/update.yml` 里的 `cron`（UTC 时间）

---

## 六、目录结构

```
.
├── .github/workflows/update.yml   # 定时生成 + 校验 + 部署 Pages
├── config/lists.json              # 片单定义
├── data/imdb-top250.json          # IMDb 电影 Top250 种子（抓取失败时兜底）
├── data/imdb-top250-tv.json       # IMDb 剧集 Top250 种子
├── data/cache/imdb-map.json       # IMDb→TMDB 映射缓存（减少 API 调用）
├── data/cache/imdb-tv-map.json    # 剧集版映射缓存
├── scripts/generate.py            # 生成器（纯标准库，无需 pip）
├── scripts/validate.py            # 规范校验
├── docs/                          # Pages 输出目录
│   ├── index.html                 # 地址展示页
│   └── watch/*.json               # 五个片单接口
└── push.sh                        # 一键建仓 + 部署
```

`data/cache/imdb-map.json` 会被工作流自动提交回仓库：250 条 IMDb 映射首次构建约产生 250 次 TMDB 请求，之后每天只需增量更新。

---

## 七、常见问题

**1. Actions 报 `缺少 TMDB_API_KEY`**
Secret 没配或名字拼错。Settings → Secrets → Actions 里应存在 `TMDB_API_KEY`，然后手动 Run workflow。

**2. Pages 打开 404**
Settings → Pages → Source 必须是 **GitHub Actions**；首次部署需要 1–2 分钟；确认 `deploy` 这个 job 是绿色的。

**3. 某个片单一直是空的**
- 该榜单确实没数据（如 `now_playing` 在 CN 区很少）→ 把 `TMDB_REGION` 改成 `US` 或留空
- TMDB 配额/网络问题 → 看 Actions 日志里 `[fail]` 那行

**4. IMDb Top250 数量不足 250**
IMDb 线上榜有反爬，抓不到时会用仓库内置种子；个别片子在 TMDB 匹配不到（冷门/纪录片），会自动跳过。

**5. EMOS 里片单只有几十条**
正常。EMOS 只显示**媒体库中已有**的影片，ID 对不上的都被过滤掉了。想让榜单更满，先补齐片库。

**6. 想让接口更快/更稳（国内访问）**
把 `docs/watch/*.json` 用你 OpenWrt 上的 Nginx/Caddy 反代，或者用 Cloudflare 加速 `*.github.io`；也可以把 Pages 换成自己的域名。

---

## 声明

- 仅聚合公开的 TMDB / IMDb 榜单元数据，不提供、不转存任何影视内容与播放地址。
- TMDB 数据遵循 TMDB 使用条款，请保留其署名。
- IMDb Top250 种子数据来源于公开数据集，仅用于榜单排名兜底。
