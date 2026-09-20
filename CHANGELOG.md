# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

> **v1.0.0 为首个公开版本。**
> `v0.x.x` 均为内部开发记录，仅在本文件以文字存档供追溯；
> 公开仓库不携带 0.x.x 的任何历史提交。

## [v2.3.0] - 2026-09-20

### Added - 长期记忆：Agent 跨会话不再失忆

- **动机**：之前记忆只有两本账——SQLite 会话记录（删了就没）与上下文压缩摘要（只服务
  当前会话）；换个会话就是重开一局，以前拍板的约定、用户的偏好全清零。
- **remember / recall / forget 工具**：Agent 可以主动把值得跨会话保留的信息存档
  （`data/memory.json`，纯本地）；遇到「以前 / 上次 / 之前说过」先 `recall` 检索，
  别装作还记得；用户明确要求时 `forget` 删掉。
- **相关记忆自动注入**：每次提问按相关度（中文双字 + 英文单词命中打分，零依赖）匹配
  最近 3 条注入 system，普通对话与 Agent 对话都生效——即使模型忘了主动 `recall` 也能想起来。
- **设置 → 记忆标签页**：查看全部记忆（新的在前）、手动存档、单条删除、清空全部；
  新增像素「记忆」图标（brain）。
- **SYSTEM_PROMPT 补充**：长期记忆使用守则（该记的记、该查的查、该删的删，别拿当前
  对话的细枝末节凑数）。
- 版本号 `2.2.1 → 2.3.0`，`index.html` 全部资源缓存戳同步升级（记得强刷）。

### Added - 工作区文件工具（Agent 能干活了）

- **fs_list / fs_read / fs_write / fs_delete**：Agent 可以在受限工作区
  （`data/workspace/`）里列目录、读文件、写/追加、删单文件或空目录 —— 以前只能写
  知识库文本文件，现在能存产出物、整理笔记、落计划。
- **路径沙箱**：拒绝 `../`、绝对路径、盘符切换与符号链接逃逸；工作区根目录与
  非空目录删除被硬拦；写/删动作全部记入 `data/agent_changes.log` 留痕。

### Added - 高危工具审批（允许 / 拒绝）

- 写文件（fs_write / fs_delete）与自定义工具增删（tools_upsert / tools_delete）执行前，
  先在 SSE 里发 `tool_approval` 事件，前端弹出银狼式的授权窗（展示工具与参数），
  点「允许一次 / 拒绝」通过 `POST /api/agent/approval` 回传；**60 秒没点默认拒绝**，
  流程不会卡死。拒绝后不执行工具，把原因作为工具结果回给模型让它换路。

- **测试**：审批「允许→执行→写盘」与「拒绝→跳过程序→告知用户→注册表清理」两条链路
  已用假客户端全流程验证；`/api/agent/approval` 逻辑单测通过。

### Added - 任务规划（update_plan / show_plan + 进度清单）

- 复杂任务不再是黑箱：模型动手前先用 `update_plan` 列出步骤，每完成一步改一次状态
  （pending → running → done），回答气泡里实时渲染「任务清单」进度条。
- 计划存在本轮运行状态里（`_run_state`），SSE 新增 `plan` 事件，非流式 `/api/chat`
  把计划并入 meta，三端行为一致。
- 配合既有的 `max_steps` 收尾逻辑：步数用尽时强制以已有资料收尾作答。

### Added - 停止生成按钮

- Agent 长任务不再只能干等白烧 token：发送按钮在生成中变红色停止键
  （AbortController 取消流），已生成的内容保留、标注「已停止生成」。
- 停止时同步解除挂起的工具审批弹窗，不会两边卡死。

### Added - rag_import_url（网页一键入库）

- 看到好文章/文档页面，可以让 Agent `rag_import_url` 抓正文转 Markdown 存进知识库并
  自动向量化，以后 `rag_search` 就能检索到——把网络资料变成自己的本地资产。
- 复用 fetch_page 的正文抽取（Firecrawl / bs4），一轮对话最多导入 3 页；
  导入动作记入 `data/agent_changes.log`。

### Added - 计算器工具 + token 硬闸 + pytest 冒烟

- **calculate（安全计算器）**：AST 白名单求值，支持四则/幂/比较/逻辑与常用函数，
  任何属性访问、赋值、导入都被拒绝——模型心算不可靠（大数、小数、概率）时用它，
  零依赖零风险。
- **单轮累计 token 硬闸**：Agent 每次调工具都会重发上下文，多轮累加很快；
  累计 token 超过上限（默认 200K）就停止再调工具，强制用已有资料收尾，防止一次
  任务烧穿额度（`max_total_tokens` 参数，可在 runner 层调）。
- **pytest 冒烟**：新增 `tests/test_smoke.py`（12 个用例，`pytest tests/` 一键跑）——
  工具注册表、审批标记、记忆增删查、沙箱穿越拦截、计算器安全、计划状态机、
  审批允许/拒绝双链路（假客户端）、SSE 归一化；测试自建自清，不碰用户数据。
- `requirements.txt` 增加 `pytest==8.4.1`。

### Added - 会话导出 Markdown

- 侧边栏每个会话新增「导出」按钮：`GET /api/sessions/{id}/export` 把整段对话导出为
  Markdown 文件下载（委托人 / 银狼（模型名）分节，便于存档、分享或喂给其它工具）。
- 新增像素 download 图标。

## [v2.2.1] - 2026-09-18

### Fixed - v2.2.0 视觉精修：控件恢复圆润，像素感收敛到图标与字饰

- **动机**：v2.2.0 把「像素风」推进到了交互控件上（开关方滑钮、滑块方角、
  全站 4px 小圆角、按钮硬阴影），实机观感偏糙、显得廉价，用户反馈
  「开关按钮还是原来的样式比较好看」。
- **开关 / 滑块 / 滚动条**：撤掉方块化处理，恢复圆润造型——开关回到胶囊轨道
  + 圆形滑钮（并细加了轨道内阴影与滑钮柔光，checked 时带主题辉光）；
  上下文滑块回到圆头轨道与圆形滑钮；滚动条回到圆角；
  加载动画回到圆点弹跳（撤掉 8bit 步进方块）。
- **按钮与圆角**：撤掉街机硬阴影按键与全站圆角收紧，主按钮恢复原有的
  柔和辉光 + 悬停上浮；气泡 / 面板 / 输入框 / 头像恢复原有圆角（气泡 14px、
  头像圆形、设置面板 16px）。
- **状态点**：`px-dot` 像素方块改回小圆点 + 辉光（呼吸闪烁改为平滑缓动），
  顶栏模型徽标 / 提供商类型点恢复圆形。
- **保留的像素签名**：像素图标（icons.js）、设置面板/弹窗切角（`--cut` 10px → 8px）、
  扫描线底纹、顶栏主题光带、欢迎页像素网格与狼头、等宽字体点缀 —— 这些保留不动。
- 版本号 `2.2.0 → 2.2.1`，`index.html` 全部资源缓存戳同步升级（记得强刷）。

## [v2.2.0] - 2026-09-18

### Changed - 前端视觉重做：像素街机风（图标不再用 emoji）

- **动机**：前端此前大量用 emoji / 颜文字充当图标（⚙️🔌🤖🧠📚🗑️📁✓✕…），
  观感廉价、和银狼的设定对不上。
- **新增像素图标系统**（`frontend/src/icons.js`）：27 个 16×16 自绘像素 SVG
  （设置 / 插件 / 机器人 / 芯片 / 画笔 / 书 / 服务器 / 垃圾桶 / 文件夹 / 显示器 /
  云 / 星标 / 勾 / 叉 / 加 / 铅笔 / 金币 / 扳手 / 地球 / 警告 / 回形针 / 发送 /
  人像 / 闪电 / 下拉箭头 / 像素狼头）。图标用字符网格定义、运行时转成 SVG path；
  主色跟随主题（`currentColor`），点缀色用青蓝；模板里不再出现任何一个 emoji 图标
  （`<option>` 放不了组件，类型下拉保留纯文本）。
- **配色校正为银狼官方基调**：底色 `#070413`、蓝紫主色 `#7C6BFF`（原为偏品紫
  `#A855F7`）、青蓝全息 `#3ED8F0`、新增品红点缀 `#F45FA8`。
  **已保存过自定义主题的用户不受影响**（变量名与 `sw_theme` 协议未变）。
- **街机 HUD 质感**：弹窗 / 模型菜单 / toast 左上+右下切角（clip-path）、
  主区域极淡扫描线 + 顶部光晕（CRT 质感）、顶栏主题光带；
  主按钮改硬阴影 + 按下沉 2px 的街机按键手感；全站圆角统一收紧。
- **8bit 动效与像素化**：状态点改像素方块（呼吸 / 闪烁）、加载动画改步进方块、
  开关 / 滑块 / 滚动条方角、思考过程（狼尊时刻）终端化、
  欢迎页像素网格 + 像素狼头 logo + 闪烁提示符；徽标 / 分节标题 / 统计数字用等宽字体。
- **文案轻量银狼化**：新开一局 / 还没有存档 / 读档中… / 出错了：xxx。
- **浏览器标签图标**换成像素狼头（设置过自定义头像的仍用头像）。
- 版本号 `2.1.2 → 2.2.0`，`index.html` 全部资源缓存戳同步升级（记得强刷）。

### 说明

- **零新增依赖**：图标全部是代码绘制内联 SVG，不引字体、图片与 CDN，离线优先不变；
- 不动任何后端逻辑与流式协议；`data/` 用户数据（会话 / 头像 / 提供商配置）零改动。

---

## [v2.1.2] - 2026-09-16

### Fixed - 回答变成"过程碎碎念"（重要）

- **现象**：问"最近 AI 公司的模型和价格有什么新消息"，整轮回答只有 71 个字符：
  「搜索结果全是导航站，没用。换几个精确的关键词。」——这是模型的过程话，不是回答；
  后续几轮同样只回「搜到料了，挖几篇细读」。
- **根因**：Agent 每一步流出的文本都会被累积成最终回答，而**带工具调用的那一步**
  输出的其实是"我准备怎么搜"的过程话；当工具轮数用尽时，流程直接结束，
  从来没有一次"用已有资料作答"的收尾，于是最后一句过程话就成了回答。
- **修复**：
  1. 新增 `demote` 事件：带工具调用的步骤，它的文本会**从回答里撤回**、
     转进「思考过程」（SSE 层、前端、非流式接口三处同步处理）；
  2. 工具轮数用尽时，**强制一次不带工具的收尾作答**，确保最终一定有结论；
  3. 兜底：万一整轮没产出任何答案，给一句明确交代，不会把空白消息存进历史。

### Changed - 联网搜索：次数收敛、结果筛选

- **一轮内不再重复劳动**：新增 `agent/tools/_run_state.py`（本轮状态盒子），
  同一个查询复用上次结果、同一个 URL 不重复抓取，并设上限
  **6 次搜索 / 6 次抓页**（实测修复前一轮出现过 10 次搜索 + 8 次抓页）。
- **结果条数收敛**：默认 3 条、最多 5 条（原先默认 5、上限 10）。
- **结果质量筛选**（`_rank_results`）：丢弃 AI 导航站/工具农场站，同域名最多留 2 条，
  官方来源（openai.com、api-docs.deepseek.com 等）置顶，百科/问答降权；
  万一全被过滤则保留原结果，绝不返回空。
- 工具说明里补上使用纪律：查询词要具体、一次搜准比搜多次重要、拿到资料直接给结论。
- `config_data.py` 的系统提示词补一条：**搜索是手段不是回答**，过程话留在思考里。

### 实测对比（同一条问法，真实云端模型）

| | 修复前 | 修复后 |
|---|---|---|
| 回答 | 71 字符过程话 | 3269 字符（含型号、价格、来源） |
| 搜索 | 10 次 web_search + 8 次 fetch_page | 4 次 + 3 次 |
| 单次结果 | 8~10 条 | ≤5 条 |
| 来源 | 全是 AI 导航站/百科 | openai.com、api-docs.deepseek.com 等一手页面 |

---

## [v2.1.1] - 2026-09-16

### Changed - 上下文默认值调大，新增两个「无限」开关

- 默认值对齐主流长上下文模型：上下文窗口 **32K → 200K**，
  保底最近轮数 **3 → 10 轮**，单次回复上限 **2048 → 8192 词元**。
- 新增「**无限轮数**」开关：打开后不再裁剪，一条历史都不丢
  （窗口数值只作展示参考）；界面上对应的滑块与输入框会置灰禁用。
- 新增「**无限长度**」开关：单次回复不设上限。实现上是**不发送 `max_tokens` 参数**，
  而不是发一个很大的数字 —— 有些服务商把 `null` 当非法值，直接不发最稳。
  普通对话、Agent 循环、云端失败回退本地、非流式四条链路按同一规则处理。
- 上下文面板重排为三张卡片（上下文窗口 / 记忆策略 / 长度限制），
  标题与当前值同行显示、数值用大号高亮字；开关打开时相关控件置灰，
  避免"拖不动"被当成 bug。
- 面板里的说明文案改成银狼口吻（原来说明偏说明书腔）。
- 版本号 2.1.0 → 2.1.1。

---

## [v2.1.0] - 2026-09-16

### Changed - 目录重构：agent/ 与 middleware/（行为不变）

- 按职责重新分层，根目录只留主体功能与入口：

  | 位置 | 放什么 |
  |------|--------|
  | `agent/runner.py` | Agent 多步循环（模型 → 工具 → 模型） |
  | `agent/tools/` | 模型可以主动调用的工具，**一个功能一个文件** |
  | `middleware/` | 每个请求都要过的横切能力（上下文、token、流式归一化） |
  | 根目录 | `main.py`、`SilverWolf.py`、数据库、RAG、附件、配置等业务主体 |

- 工具注册改为**自动发现**：放进 `agent/tools/` 的模块导出 `TOOLS` 字典即自动注册，
  新增/删除工具不再需要改一个 460 行的注册表文件。下划线开头的模块（如 `_web.py`）不注册。
- 工具按功能拆分：
  - `web_search.py` 联网搜索（Tavily / Bing 爬虫后端）
  - `fetch_page.py` 打开网页读正文
  - `datetime_tool.py` 当前日期时间
  - `knowledge.py` RAG 检索与增删改（5 个工具同属一个功能，放同一文件）
  - `self_manage.py` 自定义工具增删改 + 自我修改留痕
  - `_web.py` 共用零件（浏览器 UA、限速、HTML 解析），**不注册成工具**
- 原 `agent_runner.py` / `agent_tools.py` / `context_manager.py` / `token_usage.py` /
  `stream_utils.py` 全部按职责迁移到新位置，依赖方向固定为
  `SilverWolf.py → agent/ → middleware/`，不允许反向 import。
- `AGENTS.md` 增补分层规则与新目录表。

### Added - 上下文窗口管理（4K ~ 1M）

- 设置里新增独立的「🧠 上下文」页签（原来只是 Agent 能力里的一张卡片），四个区块：
  **上下文窗口 / 记忆与裁剪 / 长度限制 / 压缩开关**。
- **窗口预算语义修正**：预算现在是「整个请求」的 token 上限（system 提示 + RAG 片段 +
  历史 + 当前提问），和用户看到的"128K 窗口"是同一个概念；此前只约束历史部分，
  实际发送量会超过设定值。
- 范围从 6000~32000 放宽到 **4K ~ 1M**（对齐主流长上下文模型），
  滑块按档位走（8K / 16K / 32K / 64K / 128K / 200K / 320K / 512K / 1M），
  也可以手动输入任意值，两者互相吸附。
- 新增「**保底最近轮数**」（1~20 轮，默认 3）：窗口再紧，最近几轮也不丢，
  避免把预算调小而让模型彻底断片。
- 放宽长度上限：工具结果 500~50000 字符（原 500~20000），
  单次回复 256~32768 词元（原 256~8192）。
- 所有越界值统一在 `_clamp_context_settings()` 里夹回合法范围，
  手改 `ui_state.json` 也不会把程序或钱包玩坏。

### Fixed - 模型输出上限不兼容导致的报错

- 单次回复上限设得比模型实际允许的输出上限还大时，服务端会直接拒绝请求；
  现在遇到这类报错会自动去掉 `max_tokens` 重试一次（普通对话与 Agent 两条链路都处理）。

### Changed - 模型选择菜单改成自绘

- 原生 `<select>` 展开后的列表字号由系统决定、无法美化（用户反馈"字体太小、格式太丑"），
  换成自绘菜单：13px 字号、按提供商分组（本地 / 云端）、当前项高亮 + 勾选、
  默认模型带标签，触发器显示「图标 + 模型名 + 提供商名」，点击菜单外自动收起。
- 版本号 2.0.4 → 2.1.0（前端资源 `?v=` 同步刷新）。

---

## [v2.0.4] - 2026-09-16

### Added - 上下文管理（省 token 的硬手段）+ 搜索开关

- 新增 `context_manager.py`：**对话上下文管理**，三件事各管一段
  - **裁剪（trim）**：给历史消息设 token 预算（默认 6000），超出就从最旧的开始丢；
    system 提示、助手开场、当前这条用户消息永远保留（它们是本轮的必要输入）。
    实测一段 3.5 万 token 的长历史，裁剪后压到 6676 token（含约 1000 token 的锚点），
    历史部分省掉约 90% 的输入量；
  - **压缩（compress，可选，默认开）**：被裁掉的旧消息交给模型压成 ≤200 字摘要，
    存进 `sessions.summary`（SQLite 新增列，幂等 ALTER），下次对话作为一条 system 消息带回。
    摘要是"滚动"的：新摘要会和旧摘要合并，不会越压越乱。压缩失败只丢历史，不影响对话；
  - **截断（truncate）**：工具返回内容写入上下文前按字符截断（默认 3000 字符）并注明"已截断"。
- 设置 → Agent 能力 新增「**上下文管理**」卡片，四项均可调并持久化到服务端：
  历史预算（1000–32000 tokens）/ 工具结果上限（500–20000 字符）/
  单次回复上限（256–8192 tokens）/ 上下文压缩开关（手改配置文件越界也会被夹回合法范围）。
- 设置 → Agent 能力 新增「**启用 Tavily**」开关：Tavily 消耗额度，关掉后
  `engine=auto` 直接走免 Key 的 Bing 爬虫，一个额度都不花；需要时再打开。
- `agent_runner`：`run_agent` / `run_agent_stream` 新增 `reply_max_tokens`（默认 2048）
  与 `tool_output_limit`（默认 3000），并把这些参数一路传到 `max_tokens`。
- 新增「单次回复上限」：模型一次最多吐多少 token，防止一条回答吃掉大量额度。

### Changed - 搜索后端只留能用的

- **移除百度爬虫后端**：实测无 Cookie 直连会拿到「百度安全验证」页
  （HTTP 200、正文仅 1.4KB），拿不到结果只增加噪音，已从代码、工具描述、
  前端文案与自检按钮里一并删除；搜索后端现在只有 **Tavily** 与 **Bing 爬虫**。
- `web_search` 的 `auto` 逻辑改为：**开关打开且配了 Key 才用 Tavily**，否则直接走 Bing 爬虫。
  仍支持显式 `engine=tavily` / `engine=bing` 强制指定（方便单独验证某一层）。
- 自检按钮从三个减为两个：**测试 Tavily** / **测试爬虫 · Bing**。
- 版本号 2.0.3 → 2.0.4（前端资源 `?v=` 同步刷新，避免浏览器拿着旧缓存）。

---

## [v2.0.3] - 2026-09-15

### Changed - 搜索收敛为 Tavily + 自研爬虫，新增当前时间工具

- 搜索后端只保留两种：**Tavily**（主，配 Key）与**自研爬虫兜底**（免 Key）。
  移除 SearxNG 配置项与 `ddgs` 依赖（`requirements.txt` 同步移除，保留 beautifulsoup4）
- 自研爬虫：直接抓取搜索结果页 HTML 并解析 —— **Bing 优先**（结构稳定、实测可用），
  百度次之；内置浏览器 UA、最小请求间隔 1.5s + 随机抖动防封；
  百度会返回「安全验证」页，这种情况会明确报出原因而不是笼统的"无结果"
- `web_search` 工具新增可选 `engine` 参数：`auto`（默认）/ `tavily` / `bing` / `baidu`，
  便于在 Tavily 额度用尽或质量不佳时强制走免 Key 爬虫
- 新增 **`get_current_time` 工具**：返回本地日期时间、星期、时区、Unix 时间戳与 UTC 时间。
  解决"模型不知道今天几号"的问题（此前它只能回答"我看不到你系统里的今天"）
- 设置面板：「联网搜索后端」只保留 Tavily Key 与可选的 Firecrawl Key，
  并提供三个自检按钮：测试 Tavily / 测试爬虫(Bing) / 测试爬虫(百度)

---

## [v2.0.2] - 2026-09-14

### Changed - 联网搜索与网页正文抓取升级

- 搜索后端改为四级回退：**Tavily → SearxNG → ddgs 库 → DuckDuckGo HTML**；
  新增 `ddgs`（免费无 Key，适合本地 demo；国内网络可能不可达）替代原来手写的结果页正则解析
- 网页正文抓取升级：可用 **Firecrawl**（可选 Key，处理 JS 渲染站点，质量最好），
  否则用 **BeautifulSoup** 本地抽取（优先 `article` / `main`，剔除导航/脚本/页脚等噪声），
  bs4 缺失时退回正则去标签
- 「设置 → Agent 能力 → 联网搜索后端」新增 Firecrawl Key 输入（可选，留空保留原值）
- `requirements.txt` 新增依赖：`ddgs==9.16.0`、`beautifulsoup4==4.15.0`（含传递依赖 primp / soupsieve）

---

## [v2.0.1] - 2026-09-14

### Fixed

- **重启后「狼尊时刻」（思考过程）消失**：原因是思考内容只在流式里发给前端、没有落库；
  现在 Agent 模式与普通模式都会把 reasoning 累加后写入消息 meta（`messages.meta.reasoning`），
  前端加载历史消息时再还原到气泡，重启服务/重新打开会话都能展开查看
- 版本号修正：v2.0.0 时 `config_data.py` 与前端缓存参数漏改（补丁被整体拒绝导致未生效），
  本次统一到 2.0.1 并强制刷新前端资源

### Docs

- AGENTS.md 第 11 条升级为「错误与修复要写透」：每条必须包含
  报错原文 / 触发代码 / 根因 / 修复代码 / 原理 / 延伸与自检
- `changecode.md` 新增第 6 节「深度复盘」，含 7 个带原始报错的完整案例

---

## [v2.0.0] - 2026-09-14

### Added - 从「聊天」升级为「Agent」

**Agent 主循环（`agent_runner.py`）**

- ReAct 思路：模型判断是否需要工具 → 执行工具 → 结果喂回模型 → 输出最终回答，
  最多往返 `max_steps` 轮（默认 4，可在设置里调 1-8）
- 兼容兜底：模型不支持 function calling 时自动降级为普通流式对话并提示；
  云端连接失败时自动回退本地 Ollama
- 统一 SSE 事件：content / reasoning / tool_call / tool_result / usage / warning / meta

**工具系统（`agent_tools.py`）**

- 内置工具：联网（web_search / fetch_page）、RAG（rag_search / rag_list_files /
  rag_write_file / rag_delete_file / rag_list_knowledge_bases）、自我维护
  （tools_list / tools_upsert / tools_delete）
- 自定义工具是「声明式 HTTP 工具」（方法 + URL 模板 + 参数），不执行任意代码，
  由 Agent 自行增删改，定义保存在 `data/tools.json`
- 自我修改边界：只允许写知识库文本文件与工具定义，全部改动记录到 `data/agent_changes.log`

**联网搜索（`web_search.py`）**

- 三种后端按优先级回退：Tavily（TAVILY_API_KEY）→ SearxNG（SEARXNG_URL）→
  DuckDuckGo HTML 兜底（无需 Key，国内可能不可用）
- 搜索后端可在「设置 → Agent 能力」填写并「测试搜索」，无需改 .env

**Token 统计与「本轮总结」（前端）**

- 每条回复气泡底部显示：输入 / 输出 / 合计 token（优先服务商实测值，缺失时估算）、
  工具调用轮数、调用过的工具标签、联网来源（前 3 条 + 展开完整 URL）、RAG 命中
- 这些信息随消息保存（messages.meta），刷新或重开会话后仍可见

**设置面板**

- 新增「Agent 能力」页：Agent 模式开关、联网开关、最大工具轮数、当前工具清单、
  最近自我修改记录、搜索后端配置与测试

---

## [v1.0.0] - 2026-09-02

### Release - 首个公开版本（Silver Wolf 工作台 1.0）

- 完整工作台式功能：多模型提供商管理（Ollama / 任意 OpenAI 兼容接口）、输入框切换模型、
  流式输出、会话记忆与附件清理、CTF 附件静态分析、Markdown 回复、RAG 本地知识库
- 界面：银狼命名优先、外观自定义（主题色 / 背景图 / 头像，服务端持久化）、
  「狼尊时刻」思考展示、左下角设置弹窗
- 开源净化：MIT 许可证、无版权素材（背景/头像由用户自备）、无私人密钥与工作空间地址、
  本地 / Docker 部署文档
- 发布方式：以**单提交全新仓库**公开，0.x.x 历史不对外发布
- 首次运行**不预置任何模型**（默认空列表），由用户自行添加；也允许删除到 0 个回到未配置状态
- 默认**无背景图**（纯色），上传背景后自动加深色遮罩保证可读性
- 一键安装脚本 `install.bat` / `install.sh`：合并 Python（缺失时 Windows 自动 winget 安装）
  与 Vue 前端运行时的下载，一次跑完所有依赖

---

## [v0.9.5] - 2026-09-02

### Added - 银狼头像贯穿主界面

- 设置了银狼头像后：欢迎页大 “S” 自动换成**圆形头像**（无头像时仍显示 S 字标）
- 浏览器标签图标（favicon）同步：有头像时显示头像，无头像时回退渐变 S
- `start.bat` 是控制台脚本，本身不能显示图片；如需图标效果，指的就是上面浏览器标签图标

---

## [v0.9.4] - 2026-09-02

### Changed - 狼尊时刻拉条重设计 + 测试数据安全规矩

- 「狼尊时刻」折叠拉条全新样式：改为霓虹胶囊按钮（渐变底 + 发光边框 + 自定义旋转箭头），
  展开内容带左侧霓虹边线 + 入场动画 + 细滚动条，观感更干净
- 开发协作守则 新增数据安全铁律：测试**不得覆盖/删除用户数据**（`data/avatars/` 头像、
  `providers.json`、`ui_state.json`、聊天库等）；确需测试先备份、测完原样恢复，
  绝不把用户数据当测试残留清理

---

## [v0.9.3] - 2026-09-02

### Fixed - 命名与缓存策略

- **名字以「银狼」为先**：人设提示词新增名字规则——自称、旁白、队友称呼一律用「银狼」，
  「Silver Wolf」只是暗网代号/英文名，不主动出现（此前卡芙卡台词里会出现 “Silver Wolf，你变了”）
- 界面标题顺序调整：顶栏 Logo 改为「银狼」，欢迎页标题同步；英文只保留在次要徽标
- 前端静态资源加 `Cache-Control: no-cache`：浏览器每次都会向后端校验，
  杜绝旧 JS/CSS 长期缓存导致“改代码不生效”（日志里的 304 是正常缓存命中，不是错误）
- 开发协作守则：明确**禁止用无头浏览器充当 UI 测试**，没有真实浏览器能力时须请用户手动验证

---

## [v0.9.2] - 2026-09-02

### Fixed - 重启不丢设置 + 界面细节 + 人设平衡

- **头像持久化**：银狼/我的头像改由服务端保存（`data/avatars/`，新增上传/删除/读取接口），
  重启服务甚至换浏览器都不再丢失（此前仅存浏览器 localStorage）
- **知识库与 RAG 开关持久化**：上次使用的知识库与 RAG 状态存入 `data/ui_state.json`，
  重启后自动恢复，不再跳回默认
- 设置弹窗滚动条美化（渐变细滚动条）
- 主界面欢迎图标与浏览器标签 favicon 去掉 🐺，改为渐变 “S” 字标
- 思考折叠块更名「狼尊时刻」并换成闪电图标
- 人设提示词重写：**工作优先、人设做佐料**——回答要完整自洽不“不知所云”；
  接到任务先正面完成；做不到的事明说能力边界并给替代方案，禁止假装做到或整段玩梗

---

## [v0.9.1] - 2026-09-02

### Added - 头像自定义 + 输入框/深度思考 UI 优化

- **头像自定义**：设置 → 外观 新增「银狼头像 / 我的头像」上传（≤ 2MB，仅存本机浏览器
  localStorage，不上传服务器也不入库）；去掉消息气泡里默认的 🐺 emoji 头像，
  默认改为渐变色 “S” 字标
- 输入框占位符简化为「输入消息，按 Enter 键发送」
- 「深度思考」折叠块样式重做：去掉原生箭头、改为渐变卡片 + 可旋转箭头，展开更顺眼
- 实测：界面配置的 DeepSeek / 阿里云 / Ollama 提供商均可直接对话，`.env` 完全可选
  （仅首次播种用；文档与 `.env.example` 已注明）

---

## [v0.9.0] - 2026-09-02

### Added - 外观自定义 + 开源净化

**外观自定义（背景图 / 主题色）**

- 设置弹窗新增「外观」：6 套主题色预设（银狼紫 / 霓虹青 / 赛博绿 / 熔岩橙 / 幽灵蓝 / 绯红）
  + 自定义主色取色器，点缀色自动搭配
- 背景图支持本地上传（≤ 3MB），仅存本机浏览器（localStorage），不上传服务器；
  主题与背景设置持久化，重启不丢
- CSS 全面变量化：主色 / 霓虹色 / 用户气泡色的硬编码 RGB 改为主题变量，换肤不再留死角

**开源净化（为公开仓库做准备）**

- `static/background.jpg`、`static/avatar.jpg`（角色版权素材）移出 git 追踪并加入 gitignore，
  仓库改用渐变占位背景与 emoji 头像，用户可自行上传替换
- `CLOUD_BASE_URL` 默认值从私人百炼工作空间地址改为公共 DashScope 兼容端点
  （`.env.example` 与 `config_data.py` 同步）
- 新增 MIT `LICENSE`；README 重写：本地 / Docker 部署指南、安全与隐私说明、
  「从当前成熟版开始公开」的推荐做法（单提交新仓库 + 轮换 API Key）
- 开发协作守则 新增铁律：版权素材一律不入库

---

## [v0.8.3] - 2026-09-02

### Fixed - 历史消息 Markdown 渲染 / 附件重启丢失 / start.bat 无法启动

**历史消息 Markdown 不渲染（离开会话再回来就变纯文本）**

- 根因：实时对话的消息 role 是 `bot`，但从数据库加载历史消息时 role 是 `assistant`，
  气泡只认 `bot` 才走 Markdown 渲染，于是回来就退化
- 修复：加载会话时统一把 `assistant` 归一化为 `bot`，历史消息的标题/代码块/列表/表格
  重新正常渲染，气泡样式也恢复

**上传的附件重启服务后消失**

- 根因排查：文件并不是被重启删掉的——删掉的“其他对话”中包含上传该文件的会话，
  按「删会话连带清理附件」规则，文件随会话一起被清理（符合设计）
- 保险加固：移除启动时的 24 小时孤儿附件自动清扫；今后附件文件**只**在所属会话被删除时
  清理，重启服务不会再动任何上传文件

**start.bat / download_deps.bat 无法正常使用**

- 根因：批处理文件被写成 LF 换行 + UTF-8 编码，Windows cmd 按 GBK/CRLF 解析，
  中文与括号块全部错乱（报 `'lama'`、`'ano'`、`'.venv\Scripts\python.exe" ('` 等错误）
- 修复：两个 .bat 转回 GBK 编码 + CRLF 换行；start.bat 增加 `cd /d "%~dp0"`
  （从任意路径启动都能找到 .venv）和端口占用检测提示（8000 被占用时明确警告）

---

## [v0.8.2] - 2026-09-01

### Changed - 回复 Markdown 渲染 + 附件文案银狼化 + 图标 UI 打磨

**Markdown 渲染**

- 新增零依赖离线 Markdown 渲染器（`frontend/src/markdown.js`）：银狼回复里的标题、粗斜体、
  行内代码/代码块、列表、引用、表格、链接都会正确排版，不再是一坨纯文本
- 先整体 HTML 转义再渲染，防 XSS；流式输出过程中同步实时渲染

**附件读取文案银狼化**

- 附件内容读不出来时，不再生硬显示「无法提取文字」，改为银狼口吻
  （如「内容像是被加密/加壳了，读不出来；SHA256 已核对」）
- 附件上下文中明确要求模型：每个附件 SHA256 已核对；内容读不出时用银狼口吻说明，
  例如「哈希我签到了，内容像是被加密/加壳了，啧，读不出来」
- docx 提取失败时新增 zip/XML 兜底解析；.doc 老格式与 PDF 失败时尝试提取可打印字符串

**图标与 UI 打磨**

- 顶栏 RAG、发送、附件、设置、用户头像等 emoji 图标替换为干净的线性 SVG
- 模型徽标改为「霓虹圆点 + 文字」样式，去掉 emoji

---

## [v0.8.1] - 2026-09-01

### Fixed - 删除会话时清理附件文件

- 新增 `attachments` 表记录「会话 ↔ 附件」关联；发送带附件消息时自动登记
- **删除会话时**：其引用的附件文件（`data/uploads/`）一并删除；
  同一文件仍被其它会话引用时保留，全部会话删除后才清理物理文件
- 启动时自动清扫「未被任何会话引用且超过 24 小时」的孤儿附件
  （上传后没发送、发送失败或旧版本残留）
- 附件路径继续做防穿越校验，清理只作用于 `data/uploads/` 内

---

## [v0.8.0] - 2026-09-01

### Added - 聊天附件（CTF 题目文件）

**输入框 📎 上传附件，随消息一起发给模型分析**

- 支持上传：压缩包（zip/rar/7z/tar…）、可执行文件（exe/dll…）、Python/各类代码、
  Word/PDF 文档、图片等任意文件
- 输入框新增 📎 按钮，选中的文件以 chip 形式展示在输入框上方，可移除；
  只带附件不发文字也可以（自动按「请分析这些附件」发送）
- 用户消息气泡展示附件 chips（文件名 + 大小 + SHA256 悬停）

**后端附件分析（只做静态分析，绝不执行上传文件）**

- 新增 `POST /api/chat/attachments`：上传保存到 `data/uploads/<日期>/`（随 data/ gitignore），
  返回文件引用（path / sha256 / 大小 / 类型）
- 按类型提取上下文注入给模型：
  - 文本/代码：直接读内容（utf-8/gbk 自动解码）
  - zip：列出包内目录结构与文件大小，并预览少量小文本文件（CTF 题目包常见做法）
  - exe/dll 等二进制：SHA256 + 提取可打印字符串片段（strings）
  - docx/pdf：提取正文文字
  - rar/7z 等：提示手动解压后上传内部文件
- 限制：单文件 ≤ 100MB、单次 ≤ 20 个、总量 ≤ 300MB；附件路径做防穿越校验

---

## [v0.7.1] - 2026-09-01

### Changed - 设置入口移到左下角 + 人设定位澄清

- 设置按钮从右下角移到**左下角**（悬浮 ⚙️，参照 VS Code 等工作台的左下角设置习惯），
  弹窗布局不变
- 开发协作守则 澄清：银狼人设的权威定义是 `config_data.py` 的 `SYSTEM_PROMPT`
  （运行时发给模型）；开发协作守则 第五节只是界面文案的风格参考，两者作用不同

---

## [v0.7.0] - 2026-09-01

### Added - 工作台式设置弹窗 + 用户自维护模型列表

**设置改为右下角弹窗（工作台式）**

- 新增右下角悬浮 ⚙️ 按钮，点击弹出居中设置弹窗（不铺满全屏，约 880×600 上限）
- 弹窗采用「左侧导航 + 右侧内容」双栏布局：
  - 左侧：模型提供商列表（名称 / 本地或云端标识 / API Key 状态 / 默认星标）、
    知识库 / RAG、系统状态，以及「＋ 添加提供商」
  - 右侧：当前选中项的编辑内容 —— 提供商表单（名称 / 类型 / 接口地址 / 默认模型 /
    API Key / 设为默认）、已添加模型管理、知识库管理与上传、系统状态卡片
- 顶栏不再显示设置按钮，RAG 开关保留在顶栏，与输入框模型下拉互不干扰

**模型列表只显示用户添加的**

- 输入框模型下拉不再自动拉取接口的模型列表（此前阿里云等聚合接口会灌入数百个模型）；
  改为只显示用户在设置里**显式添加**的模型，默认空列表
- 提供商表单新增「已添加模型」管理：输入模型名添加、chip 形式展示、可随时移除
- 旧配置自动迁移：已有提供商保留其默认模型作为首个已添加模型，新提供商从空列表开始
- 未添加模型时输入框下拉显示「（未添加模型，点右下角 ⚙️ 添加）」，发送会提示先添加模型

**RAG / 知识库观感优化**

- 功能保持不变，视觉升级：RAG 开关改为霓虹开关控件、知识库卡片化、上传区图标化、
  当前使用中的知识库高亮、删除/使用按钮分色

**协作规范**

- 新增 `开发协作守则`：约定每次改动完成后必须 git commit、改动必须测试通过、交付前同步
  CHANGELOG / README、文案必须符合银狼人设等守则

---

## [v0.6.1] - 2026-08-30

### Fixed - 模型名显示为 undefined

- **根因**：升级 v0.6.0 后浏览器仍缓存了旧版 `app.js`，旧前端读取新后端已移除的
  `local_model / cloud_model` 字段，导致本地/云端模型名显示为 “(undefined)”
- 前端资源 URL 增加版本号（`?v=0.6.1`），强制浏览器加载最新脚本，杜绝新旧混用
- 前端对缺失字段做防御：提供商名/模型名缺失时显示占位文案，不再出现 “undefined”
- 云端聚合接口（如阿里云百炼）会返回数百个模型，输入框下拉改为最多展示 50 个
  （当前使用的模型始终保留），并提示其余模型可在设置中查看；本地 Ollama 模型仍自动加载

---

## [v0.6.0] - 2026-08-30

### Added - 工作台式改造：多模型提供商 + 输入框模型切换

**模型提供商体系（任意 OpenAI 兼容接口）**

- 新增 `provider_config.py`：把「本地 Ollama」与任意云端 OpenAI 兼容接口（OpenAI / DeepSeek /
  阿里云百炼 / Moonshot / 硅基流动等）统一抽象为 Provider，配置持久化到 `data/providers.json`
  （自动创建、已 gitignore，API Key 明文存储仅限本机使用）
- 首次启动自动从 `.env` 播种默认提供商，之后完全由界面管理，无需改 `.env`
- 新增提供商管理 API：`GET/POST /api/providers`、`DELETE /api/providers/{id}`、
  `POST /api/providers/{id}/default`、`POST /api/providers/{id}/test`（测试连接）、
  `GET /api/providers/{id}/models`（拉取可用模型列表）；API Key 一律脱敏返回
- 聊天接口支持 `provider_id` / `model` 字段，逐条消息指定模型；旧 `use_local` 参数保持兼容
- 设置面板新增「模型提供商」管理：增删改、测试连接、刷新模型、设为默认

**输入框内切换模型（像 Codex 一样）**

- 输入框上方新增模型下拉：按提供商分组列出可用模型（本地 Ollama 自动拉取 `/v1/models`），
  随时切换，无需进设置；旁置 ⟳ 一键刷新模型列表
- 顶栏实时显示当前提供商与模型徽标
- 每条回复底部标注实际生成它的模型名；自动回退后同步更新

### Fixed - 流式输出不显示 / 刷新后才显示

- **后端**：兼容流式 chunk 的异常结构 —— 部分云端接口（如阿里云百炼兼容模式）会发送
  `choices` 为空的帧，旧代码 `chunk.choices[0]` 直接抛 `IndexError`，导致流式中断；
  现在空 choices / 缺失 delta / content 为列表的格式均被安全处理，并支持
  `reasoning_content`（DeepSeek 等推理模型的思考过程）
- **后端**：流式元信息帧补发 `session_id`，即使首帧即失败，前端也能正确关联新会话
- **前端（根因）**：Vue 3 响应式陷阱 —— `addMessage()` 之前返回 push 前的原始对象，
  直接修改 `msg.content` 不会触发视图更新，所以逐字输出不渲染、只有刷新（重新加载为
  响应式对象）才显示；现改为返回数组中的响应式代理
- **前端**：展示后端流式错误事件，不再静默显示占位符
- **修复**：Ollama 健康检测超时过短（1.5s），本地模型偶发被误判离线；超时放宽到 3s

### Changed - 项目整理与文档

- 删除被 Vue 3 前端取代的旧版 `static/index.html`（git 历史可追溯）
- `.gitignore` / `.dockerignore` 补充 `data/`、`.trae/`
- `start.bat` 版本号更新；README / 功能说明书 / `.env.example` 同步新配置方式

---

## [v0.5.0] - 2026-08-24

### Changed - 前端升级 Vue 3 + 银狼人设丰富 + Markdown 上传

**前端升级 Vue 3**

- 前端运行时由 Vue 2.7 升级到 **Vue 3.4.27**（`frontend/vendor/vue.global.prod.js`，完整版含模板编译器）
- `app.js` 改为 `Vue.createApp(...) + app.component(...)` 的 Vue 3 挂载方式，组件模板语法保持不变
- 因开发环境无外网、本机无 Vue 3 副本，Vue 3 运行时（约 130KB）未随仓库内置；
  新增 `download_deps.bat` 一键下载脚本（jsdelivr / unpkg / npmmirror 多源回退）
- `index.html / stores.js / app.js` 增加缺失 Vue 3 时的友好提示，避免白屏
- 运行期仍完全离线、零 CDN 依赖

**银狼人设大幅丰富（系统提示词）**

- 基于《崩坏：星穹铁道》官方设定重构 `SYSTEM_PROMPT`（1038 字符）：
  - 新增背景身世：星核猎手、追随艾利欧剧本、以太编辑能力、暗网传奇代号
  - 强化性格：慵懒冷淡毒舌傲娇 + 重度游戏宅（世界=游戏、漏洞=BUG、入侵=卡BUG通关）
  - 明确说话风格：短句、动辄“啧”、黑客+游戏双线比喻、傲娇嘴硬
  - 补充队友互动：卡芙卡 / 刃 / 艾利欧 的随口提及
  - 明确能力领域与回复要求

**Markdown 上传**

- 明确支持 `.md` 上传，并新增 `.markdown` 别名（知识库可上传 Markdown 文档）
- 修复：上传后知识库元数据的 `document_count` 不再更新 —— 现在每次上传后同步文档数与更新时间

---

## [v0.4.0] - 2026-08-24

### Breaking - 完全离线化重构（Vue + FastAPI）

**前端依赖本地化（不再联网）**

- 移除 esm.sh CDN 依赖：Vue 运行时本地化到 `frontend/vendor/vue.js`（Vue 2.7.15 UMD 完整版，含模板编译器）
- 移除 Pinia：改用基于 `Vue.reactive` 的轻量响应式 store（`frontend/src/stores.js`）
- 前端重写为 Vue 2.7 模板组件（`frontend/src/app.js`），无构建步骤、零外网请求
- 修复：此前页面依赖 CDN，离线环境下 Vue/Pinia 无法加载、前端完全不可用

**后端修复与增强**

- 修复：`init_db()` 从未被调用，首次运行无会话表导致接口报错 —— 启动时自动建表
- 修复：RAG 相似度换算错误 —— 新建 collection 使用 cosine 距离空间，按 `hnsw:space` 自动换算（此前余弦距离被当作 L2 除以 2，导致负例误触发检索）
- 修复：embedding 完全本地化 —— 内置离线 hash embedding（1024 维 n-gram 特征）兜底，不再触发 Chroma 默认 ONNX 模型联网下载
- 修复：文档解析自动剥离 BOM、按 utf-8-sig → gbk 顺序解码，避免乱码入库
- 修复：上传文件后知识库元数据缺失，列表中不显示 —— 上传成功自动同步元数据
- 新增：默认使用本地模型（`USE_LOCAL_DEFAULT=true`），离线优先；云端保留为可选并支持自动回退
- 新增：`GET /api/config`、`GET /api/health` 系统状态接口（模型名 / Ollama 状态 / embedding 后端）
- 新增：本地模型不可用时返回清晰中文错误提示
- 新增：相似度阈值随 embedding 后端自适应（hash=0.2，transformer=0.5）
- 新增：`README.md`、`docs/USER_MANUAL.md`（功能说明书）、`start.bat` 一键启动
- 修复：Dockerfile 只拷贝 main.py 导致容器缺模块 —— 改为拷贝整个项目
- 依赖：requirements.txt 锁定版本，保证离线可复现安装

---

## [v0.3.0] - 2026-08-14

### Changed - Silver Wolf Theme Overhaul

- **Complete visual redesign** matching the Silver Wolf character from Honkai: Star Rail
  - Purple/cyberpunk color palette: primary `#A855F7` violet with neon cyan `#22D3EE` accents
  - New CSS variables system with layered transparency
  - Sidebar: `0.55` opacity (most transparent, floating feel)
  - Header & Input area: `0.68` opacity (medium transparency)
  - Bot bubble: `0.78` opacity (readable but still semi-transparent)
  - Modal & Toast: `0.85` opacity (most opaque for focus)
  - Input textarea: `0.65` opacity (lightest, focus-friendly)

- **Background image optimization**
  - Repositioned to `top center` alignment so character artwork is centered
  - Gradient overlay reduced from `0.35→0.65→0.85` to `0.2→0.4→0.6`
  - Adds CRT scanline texture overlay for hacker aesthetic
  - Background now clearly visible through all panels

- **Neon glow & animation effects**
  - Avatar border with `avatarPulse` breathing glow animation
  - Active session item with left-side glowing indicator bar
  - Gradient text effects on logo, title, modal headers
  - Neon underline dividers on header, sidebar, input area
  - Message entry animation with scale + translate

- **UI text updates**
  - Logo changed to `SILVER WOLF` (uppercase, letter-spaced)
  - Badge updated to `星核猎手 · Star Hunter`
  - Welcome screen with wolf icon + gradient title
  - All modal text switched to Chinese (重命名对话, 删除对话, etc.)

---

## [v0.2.0] - 2026-08-14

### Added - Session Memory & Conversation Management

- **SQLite-based persistence layer** (`chat_history.db`)
  - `sessions` table: stores conversation metadata (id, title, timestamps)
  - `messages` table: stores all chat messages per session with role tracking
  - Foreign key constraint with cascade delete for data integrity

- **Session management API** (`/sessions`)
  - `POST /sessions` - Create new conversation
  - `GET /sessions` - List all sessions (ordered by latest activity)
  - `GET /sessions/{id}` - Get session with full message history
  - `PUT /sessions/{id}` - Rename conversation
  - `DELETE /sessions/{id}` - Delete conversation (cascades messages)

- **Conversation memory in chat endpoints**
  - `ChatRequest` model now accepts optional `session_id`
  - When no session_id provided, auto-creates new session with title derived from first message
  - `build_messages()` retrieves last 20 messages from history for context
  - Messages automatically saved after each exchange (user + assistant)
  - Session ID returned in streaming response for frontend tracking

- **Frontend sidebar** (`static/index.html`)
  - Left sidebar with session list, sorted by update time
  - "New Chat" button to start fresh conversation
  - Click any session to load full chat history
  - Hover actions: rename (pencil icon) and delete (trash icon)
  - Active session highlighted with border
  - Rename modal dialog with input field
  - Delete confirmation modal to prevent accidental loss
  - Toast notifications for operation feedback
  - Glassmorphism style sidebar matching existing dark theme

---

## [v0.1.0] - 2026-08-06

### Added - Initial Prototype

- **FastAPI backend** (`main.py`)
  - Dual model source support: cloud (DashScope qwen3.7-max) + local (Ollama deepseek-r1:8b)
  - Non-streaming chat endpoint: `POST /Silver_Wolf`
  - Streaming chat endpoint: `POST /Silver_Wolf/stream` using SSE protocol
  - Persona system prompt for Silver Wolf character (黑客/冷感/毒舌)
  - Auto-generated title from first user message
  - `.env`-based configuration via `python-dotenv`

- **Web frontend** (`static/index.html`)
  - Dark theme UI with Silver Wolf branding
  - Model source toggle (Cloud/Local) in header
  - Streaming message rendering with blinking cursor animation
  - Auto-resizing textarea with Enter-to-send / Shift+Enter-for-newline
  - User/assistant message bubbles with avatar images
  - Welcome screen with character greeting
  - HTML escaping for XSS prevention

- **Deployment**
  - `Dockerfile` for container deployment
  - `requirements.txt` with all Python dependencies
  - `.gitignore` excluding `.env`, `.venv/`, `__pycache__/`

- **Documentation**
  - `article.md` - Technical blog post documenting the full implementation

---

## Version Legend

| Version | Status | Description |
|---------|--------|-------------|
| v2.1.2 | Current | 修复"回答只剩过程碎碎念"（demote + 强制收尾）；搜索次数收敛、结果质量筛选 |
| v2.1.1 | Superseded | 上下文默认 200K/保底 10 轮/回复 8192；新增「无限轮数」「无限长度」开关；文案换银狼口吻 |
| v2.1.0 | Superseded | 目录重构（agent/ + middleware/，工具一功能一文件）；上下文窗口 4K~1M 档位滑块；模型菜单自绘 |
| v2.0.4 | Superseded | 上下文管理（裁剪/压缩/长度上限，历史省约 90% token）+ Tavily 启用开关；移除百度爬虫 |
| v2.0.3 | Superseded | 搜索收敛为 Tavily + 自研爬虫（Bing/百度）；新增当前时间工具；移除 ddgs/SearxNG |
| v2.0.2 | Superseded | 搜索升级（ddgs 库 + BeautifulSoup 正文抽取 + 可选 Firecrawl）四级回退 |
| v2.0.1 | Superseded | 修复重启后思考过程丢失（reasoning 落库）；版本号修正；changecode 深度复盘规范 |
| v2.0.0 | Superseded | Agent 化：工具调用循环、联网搜索、RAG/工具自我维护、token 统计与本轮总结 |
| v1.0.0 | Superseded | 首个公开版本：工作台式功能集 + 开源净化，单提交发布 |
| v0.9.5 | Superseded | 银狼头像贯穿主界面（欢迎页圆形头像 + 标签图标） |
| v0.9.4 | Superseded | 狼尊时刻拉条重设计（霓虹胶囊+展开动画）、开发协作守则 测试数据安全规矩 |
| v0.9.3 | Superseded | 银狼命名优先（英文不主动出现）、前端 no-cache 防旧缓存、开发协作守则 禁无头 UI 测试 |
| v0.9.2 | Superseded | 头像/知识库/RAG 服务端持久化（重启不丢）、滚动条与图标美化、狼尊时刻、人设平衡（工作优先） |
| v0.9.1 | Superseded | 头像自定义（银狼/我的）、默认头像改渐变色 S 字标、深度思考拉条美化、输入框占位符简化 |
| v0.9.0 | Superseded | 外观自定义（主题色/背景图）、开源净化（移除版权素材与敏感默认值）、Docker 文档、MIT 许可证 |
| v0.8.3 | Superseded | 修复历史消息 Markdown 不渲染、附件重启丢失（移除孤儿清扫）、start.bat 编码/换行/端口检测 |
| v0.8.2 | Superseded | Markdown 渲染、附件读不出时银狼口吻文案（哈希已核对）、SVG 图标打磨 |
| v0.8.1 | Superseded | 删会话自动清理附件文件（attachments 关联表 + 孤儿附件 24h 清扫） |
| v0.8.0 | Superseded | 聊天附件（CTF 题目：zip/exe/代码/文档），静态分析上下文注入，data/uploads 存储 |
| v0.7.1 | Superseded | 设置入口移到左下角、开发协作守则 澄清人设定位（SYSTEM_PROMPT 与协作守则分工） |
| v0.7.0 | Superseded | 工作台式设置弹窗（右下角入口 + 双栏布局）、模型列表用户自维护（默认空）、RAG 观感优化、开发协作守则 |
| v0.6.1 | Superseded | 修复模型名显示 undefined（缓存问题）、云端模型列表截断展示、字段防御 |
| v0.6.0 | Superseded | 工作台式：多模型提供商管理、输入框切换模型、流式输出修复、思考过程展示、模型标注 |
| v0.5.0 | Superseded | 前端升级 Vue 3、银狼人设丰富、支持 Markdown 上传 |
| v0.4.0 | Superseded | 完全离线化重构：前端依赖本地化、默认本地模型、修复会话库/RAG 距离换算、embedding 离线回退 |
| v0.3.0 | Superseded | Silver Wolf themed UI, layered transparency, neon effects |
| v0.2.0 | Superseded | Session memory, sidebar conversation management |
| v0.1.0 | Prototype | Initial working chatbot with dual model + streaming |
