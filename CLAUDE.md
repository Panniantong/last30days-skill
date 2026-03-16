# last30days-skill (Agent Reach 改造版)

## 项目背景
这是 mvanhorn/last30days-skill 的 fork，目标是把付费数据源替换为 Agent Reach 的免费上游工具。

## 架构
- `scripts/last30days.py` — 主入口，编排所有数据源
- `scripts/lib/` — 每个数据源一个模块
- `SKILL.md` — Claude Code skill 定义

## 数据源替换计划

### 必须改的（付费 → 免费）：
1. **X/Twitter**: bird_x.py / xai_x.py / scrapecreators_x.py → 新建 `xreach_x.py`，用 `xreach` CLI
   - `xreach search "query" -n 20 --json` → 搜索
   - `xreach tweet URL --json` → 读推文
   - 输出是 JSON，需要 parse 成跟现有格式一致的 item list
2. **Reddit**: reddit.py / openai_reddit.py → 新建 `reddit_json.py`，用 Reddit JSON API
   - `https://www.reddit.com/search.json?q=QUERY&limit=25&t=month` 
   - User-Agent: `agent-reach/1.0`
   - 注意：服务器 IP 可能 403，加错误处理
3. **Web 搜索**: brave_search.py / parallel_search.py / openrouter_search.py → 新建 `exa_search.py`
   - 用 `mcporter call 'exa.web_search_exa(query: "...", numResults: 10)'` 
   - 或直接 HTTP 调 Exa 的 MCP endpoint

### 不需要改的（已经免费）：
- YouTube (yt-dlp) ✅
- Hacker News (Algolia) ✅  
- Polymarket (Gamma) ✅
- Bluesky (AT Protocol) ✅
- Truth Social (Mastodon API) ✅

### 暂时移除的（没有免费替代）：
- TikTok (ScrapeCreators) — 保留模块但标记为 optional
- Instagram (ScrapeCreators) — 保留模块但标记为 optional

## 关键文件
- `scripts/lib/env.py` — 检测可用数据源的逻辑（`get_x_source_status`, `get_web_search_source`）
- `scripts/last30days.py` 第 1541-1567 行 — 数据源选择逻辑
- `SKILL.md` — 需要更新依赖说明

## 编码规范
- 新模块的 parse 输出格式必须跟现有模块一致（参考 schema.py）
- 保留原有模块不删除，只改默认优先级
- 新数据源检测逻辑加到 env.py 的对应函数里
