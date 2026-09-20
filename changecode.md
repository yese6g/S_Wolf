# changecode.md - Silver Wolf 开发复盘（找 bug / 改功能实录）

> 这是我在 Silver Wolf（FastAPI + Vue3 的 AI 聊天工作台原型）从 v0.5.0 迭代到 v1.0.0
> 过程中，真实遇到并解决的问题记录。每个案例都按「任务与问题 → 旧代码 → 新代码 →
> 原因与经验」整理，适合学习 AI Agent 相关的工程与产品开发。

---

## 0. 项目一句话

一个本地优先的 AI 聊天助手：多模型提供商（Ollama + 任意 OpenAI 兼容 API）、流式对话、
会话记忆、RAG 知识库、文件附件分析、Markdown 渲染与自定义外观。

技术栈：FastAPI + SQLite + ChromaDB + OpenAI SDK；Vue 3 本地运行时前端（无构建、无 CDN）。

---

## 1. 时间线与各版本解决的问题（简历可用）

| 版本 | 做的事 | 体现的能力 |
|------|--------|-----------|
| v0.5.x | 基线：Vue3 前端 + 离线依赖 | 存量代码理解 |
| v0.6.x | 流式输出不显示双根因修复；多提供商体系 | 响应式原理、SSE 兼容、配置架构设计 |
| v0.7.x | 工作台式设置弹窗；模型列表用户自维护 | 产品形态、状态管理、UI 重构 |
| v0.8.x | 附件静态分析；删会话清理附件；Markdown 渲染 | 文件安全、级联清理、自研渲染器、XSS |
| v0.9.x | 服务端持久化；人设提示词平衡；界面打磨 | 持久化设计、提示词工程、细节品控 |
| v1.0.0 | 开源发布准备（许可证、密钥审计、单提交快照） | 工程发布、安全意识 |
| v2.0.x | Agent 化（工具循环、联网搜索、自我维护）；自研爬虫；上下文管理与 token 优化 | Agent 架构、成本控制、爬虫与反爬、真实浏览器测试 |
| v2.1.0 | 目录重构（agent/ + middleware/，工具自动注册）；上下文窗口 4K~1M 档位滑块；模型菜单自绘 | 模块化设计、插件式注册、设置项语义对齐、UI 细节品控 |
| v2.1.x | 上下文默认 200K + 无限开关；修复"回答只剩过程话"（demote + 强制收尾）；搜索次数与质量收敛 | 流式协议设计、Agent 过程/结论分流、工具限流与结果分级、测试收尾纪律 |

---

## 2. 案例集

### 案例 1：流式输出“不显示，刷新后才出现”（双根因）

【任务与问题】用户反馈：对话时回复不逐字出现，刷新页面后整段内容才显示。

【根因 A - 前端 Vue 3 响应式陷阱】

旧代码（v0.5.0）：

```js
addMessage(role, content, extra = {}) {
    const msg = Object.assign({ id: Date.now() + Math.random(), role, content }, extra);
    this.messages.push(msg);
    return msg;          // ❌ 返回的是 push 前的“原始对象”
},
```

流式解析时直接改 `botMsg.content = fullText`，但改的是原始对象，绕过了 Vue 3
reactive 代理，**不会触发视图更新**，所以界面不渲染。

新代码：

```js
addMessage(role, content, extra = {}) {
    const msg = Object.assign({ id: Date.now() + Math.random(), role, content }, extra);
    this.messages.push(msg);
    // ✅ 必须返回数组里的响应式代理，修改它才会触发更新
    return this.messages[this.messages.length - 1];
},
```

【根因 B - 后端 SSE 帧结构兼容】

旧代码（v0.5.0）：

```python
for chunk in stream:
    if chunk.choices[0].delta.content is not None:   # ❌ 某些厂商会发 choices 为空的帧
        content = chunk.choices[0].delta.content
```

阿里云百炼等兼容接口会在流中发送 `choices: []` 的收尾帧，`chunk.choices[0]`
直接抛 `IndexError`，整段流中断。

新代码（抽取解析函数，空帧安全跳过，并兼容 content 为 list、reasoning_content）：

```python
def _extract_stream_delta(chunk):
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None, None
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return None, None
    content = _normalize_delta_text(getattr(delta, "content", None))
    reasoning = _normalize_delta_text(getattr(delta, "reasoning_content", None))
    return content, reasoning
```

【经验】

- “刷新后正常”是典型信号：数据最终一致，但**中间状态的更新没有触发渲染**。
- 用框架要先理解它的响应式机制；`reactive` 返回代理，持有并修改原始对象是不生效的。
- 对接第三方 API 永远要假设数据形状“不标准”，解析层要做防御。

---

### 案例 2：缓存导致旧前端 + 新后端 = “undefined”

【任务与问题】升级后界面里模型名全部显示成 undefined。

【定位】浏览器缓存了旧版 `app.js`，旧代码读取新后端已删除的 `local_model/cloud_model`
字段，拼出 “(undefined)”。

【旧方案】静态资源 URL 不带版本号，浏览器长期缓存。

【新方案一】资源 URL 带版本号，改版必改参数：

```html
<script src="/frontend/src/app.js?v=1.0.0"></script>
```

【新方案二】后端对前端资源统一加 `Cache-Control: no-cache`，保证每次都会向后端校验：

```python
@app.middleware("http")
async def frontend_no_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/frontend/", "/static/")):
        response.headers["Cache-Control"] = "no-cache"
    return response
```

【经验】

- “明明改了却不生效/显示异常”，先怀疑缓存；日志里的 304 是正常缓存命中。
- 前后端独立演进时，接口返回结构变化是破坏性变更，前端要有兼容或缓存失效手段。

---

### 案例 3：历史消息 Markdown 不渲染（角色归一化）

【任务与问题】实时对话回复能渲染 Markdown；离开会话再进来，历史消息变纯文本。

【定位】数据库里消息 role 存的是 `assistant`，前端气泡只对 `bot` 走 Markdown 渲染。

【新代码】加载会话时统一归一化：

```js
async loadSession(id) {
    const session = await SWApi.session.get(id);
    this.currentSession = {
        ...session,
        messages: (session.messages || []).map(m => ({
            ...m,
            role: m.role === 'assistant' ? 'bot' : m.role,
        })),
    };
}
```

【经验】前端展示层与存储层的数据模型不一致是常见坑：**存储用规范名，展示用约定名，
在边界（加载处）做一次映射**，而不是到处判断。

---

### 案例 4：start.bat 乱码报错（换行与编码）

【任务与问题】双击 start.bat 报 `'lama' 不是内部或外部命令`、`'ano' ...` 等奇怪错误。

【定位】批处理文件被编辑器写成了 **LF 换行 + UTF-8 编码**，而 Windows cmd 按
**CRLF + GBK(代码页 936)** 解析，多行块与中文全部错乱。

【修复】

- 把 `.bat` 转回 CRLF 换行 + GBK 编码；
- 去掉 `chcp 65001`，避免输出编码二次错乱；
- 脚本开头加 `cd /d "%~dp0"`（从任意路径启动都能定位到 .venv）；
- 加端口占用检测，冲突时给出明确提示再退出。

【经验】

- “文件内容看起来正常”不等于“目标运行时解析正常”；批处理、命令行工具对编码与换行极敏感。
- 任何跨平台/跨运行时文件，先确认它被谁用什么编码读取。

---

### 案例 5：删会话清理附件，顺序错了文件删不掉

【任务与问题】实现“删除会话时连带删除其上传附件”。

【第一次实现（有 bug）】

```python
success = delete_session(session_id)          # ① 先删会话
removed = cleanup_session_attachments(session_id)  # ② 再查路径 → 查不到了！
```

因为会话与附件的关联是外键 `ON DELETE CASCADE`，先删会话会把关联记录一起删掉，
第二步就查不到该清理哪些文件。

【正确顺序】先取路径，再删会话，再按“是否还被其它会话引用”决定是否删物理文件：

```python
paths = get_attachment_paths(session_id)          # ① 先取路径
success = delete_session(session_id)              # ② 删会话（级联删关联）
if success and paths:
    remaining = referenced_attachment_paths(paths) # ③ 看还有谁引用
    for rel in paths:
        if rel in remaining:
            continue                               # 别的会话还在用，保留
        (UPLOAD_ROOT / rel).unlink(missing_ok=True)
```

【经验】

- 级联删除会“顺手”带走你以为还在的数据；**删除类逻辑先读后删**。
- 物理资源（文件）与逻辑记录（DB 行）生命周期要分开管理，多个持有者时用“引用计数”思路。

---

### 案例 6：状态持久化——从浏览器 localStorage 到服务端

【任务与问题】用户重开项目后，头像、选中的知识库、RAG 开关都丢失。

【演进】先存浏览器 localStorage（换浏览器/换端口就丢）→ 改为存服务端 `data/` 目录：

- 头像：`POST /api/avatar/{bot|user}` 存 `data/avatars/`，`GET` 读取；
- 界面状态：`GET/POST /api/ui/state` 读写 `data/ui_state.json`（知识库、RAG 开关）。

前端启动时拉一次并应用：

```js
const st = await SWApi.ui.state();
this.kbSlug = st.kb_slug;
this.useRag = st.use_rag;
this.avatars.bot = st.avatar_bot ? '/api/avatar/bot?t=' + Date.now() : '';
```

【经验】

- “本地单机应用”也分浏览器态与服务端态：要跨浏览器、跨重启稳定，就放服务端；
- 用户可编辑的二进制资源（头像/背景）优先“文件 + 接口”，而不是塞进 localStorage。

---

### 案例 7：配置体系演进——从 .env 到 UI 可管理的 Provider

【任务与问题】原来模型配置写死在 `.env`，用户想在工作台里自己加 API Key 和模型。

【演进】

1. 新增 `data/providers.json`，结构：

```json
{
  "id": "local",
  "name": "本地 Ollama",
  "kind": "local",
  "base_url": "http://localhost:11434/v1",
  "model": "deepseek-r1:8b",
  "models": ["deepseek-r1:8b"],
  "is_default": true
}
```

2. `.env` 只在文件不存在时播种默认值，之后完全由界面管理，API Key 永不入库、接口返回脱敏。

【经验】

- 把“少数固定配置”升级成“用户可增删的多实体”，要同时设计存储、校验、默认值迁移和 UI。
- 配置里带密钥时：明文本地可接受，但必须 gitignore + 接口脱敏 + 提示用户自行保管。

---

### 案例 8：测试误删用户数据的教训

【任务与问题】做头像上传测试时，测试用 1px 图片覆盖了 `data/avatars/bot.png`，
清理测试数据时把用户的真实头像一起删了。

【教训与改进】把规则写进 AGENTS.md，强制后续所有协作者遵守：

- 测试产生的数据要能自证（带标记或记录原文件）；
- 测试若必须动用户数据目录：先备份，测完原样恢复；
- “清理测试数据”不等于“清理 data/ 目录里所有东西”。

【经验】自动化测试的清理逻辑要“精确到我自己创建的文件”，不能按目录一刀切；
涉及用户数据的操作要可逆。

---

### 案例 9：提示词工程——人设与“干活”的平衡

【任务与问题】角色扮演类助手太爱玩梗：让它“扫目录/更新知识库”时会打哈哈、甚至编造
“已同步到云端”；回复有时太短、不知所云；角色名混乱（英文名出现在台词里）。

【旧提示词要点】“能三句说清绝不说十句”“短句为主”——导致过度压缩。

【新提示词要点】

- 工作优先，人设只做佐料：接到具体任务先正面完成，禁止整段玩梗糊弄；
- 做不到的事明说能力边界并给替代方案，**禁止假装做到、不许编造结果**；
- 回答要完整自洽，长度由问题复杂度决定，不为了简短牺牲信息；
- 名字规则：自称与队友称呼一律用「银狼」，英文名不主动出现。

【经验】给 LLM 的约束要写“正反两面”：既说“要完整”，也说“禁止假装做到”；
产品人格与可用性冲突时，把可执行性放在第一位。

---

### 案例 10：开源发布安全审计

【任务与问题】准备把项目公开到 GitHub。

【审计发现并处理】

- 旧历史提交里带过 `silverwolf.db`（会话数据）→ 采用“单提交全新仓库”发布，0.x.x 只在
  CHANGELOG 留文字记录；
- 代码里写死私人百炼工作空间地址 → 换成公共端点；
- 仓库含角色版权素材图 → 移出 git 并加 gitignore，改为用户自上传；
- 新增 MIT LICENSE、Docker/本地部署文档、README 收敛语气。

【经验】发布前检查三件事：密钥与隐私（历史也算）、版权素材、依赖与部署说明；
历史泄露比工作区泄露更难处理，宁可新建单提交仓库。

---

### 案例 11：测试方法论——真实浏览器与自建校验

【任务与问题】UI 改动只用“看代码 + 无头浏览器”，被用户批评不真实。

【沉淀规则（写入 AGENTS.md）】

- UI 交互测试用真实浏览器（Edge/Chrome）实际点击、输入、上传；
- 无头浏览器只允许在 CI 且显式说明，不允许“无头冒充已测试”；
- 每完成一个逻辑改动必须 commit（可回滚）、测试通过才能交付；
- 交付前清理测试数据，且不得碰用户数据。

【经验】质量规则只有写进团队/仓库约束（AGENTS.md）才会被“下一个协作者”遵守；
测试手段要随环境诚实声明，而不是挑好看的写。

---

## 3. 沉淀下来的通用工作法

- 定位 bug：先复现 → 看数据流（谁写入、谁渲染）→ 找“边界假设”被打破的地方。
- 改动纪律：一个逻辑一次 commit；先代码后文档；每步可 `git revert`。
- 兼容第三方：解析层永远假设形状不标准（空 choices、content 是 list、reasoning 字段）。
- 数据安全：本地文件也要防误删；删除类逻辑先读后删；测试清理精确到自建文件。
- 提示词：正反面都写；人格与可用性冲突时可用性优先。
- 发布：密钥审计（含历史）、版权素材、单提交快照、LICENSE、部署文档。

---

## 4. 面试怎么讲（AI Agent 开发实习生岗）

可以这样组织 1-2 分钟自我介绍：

1. 独立把一个 AI 聊天原型从能用维护到 v1.0.0，覆盖了前端、后端、部署与发布全链路；
2. 修过一个“流式输出不显示”的复合 bug——同时涉及 Vue 3 响应式原理和第三方
   SSE 协议兼容，说明我会用数据流视角定位问题；
3. 设计过“多模型提供商”配置体系与文件附件分析功能，理解 API 兼容、安全与持久化；
4. 写提示词时知道怎么平衡人设与可用性，也明白“模型做不到的要明说”；
5. 有发布安全意识：密钥审计、单提交快照、版权处理，并且把工程纪律沉淀成了
   AGENTS.md 团队守则。

准备问题时的关键词：Vue 响应式 / SSE 流式 / 缓存失效 / 级联清理 / XSS 转义 /
提示词工程 / git 提交纪律 / 数据安全 / Docker 与本地部署 / 开源发布。

---

## 5. Agent 化改造（v2.0.0）与近期复盘

### 案例 12：把「聊天」升级成「Agent」——架构适配而不是照抄框架

【任务与问题】参考一个 LangChain + ReAct + RAG 的框架文档，把聊天助手升级成能自动调用工具的 Agent。

【取舍】参考框架是 `app.py + agent/react_agent.py + model/factory.py + rag/` 的典型结构；
我们的项目是 FastAPI（`main.py` + `SilverWolf.py`）+ Vue3 + 自研 RAG。直接套会推翻现有代码，
而且引入 LangChain 会破坏「离线优先、零多余依赖」的承诺。

【做法】保留我们的架构，吸收框架的思想：

```
agent_runner.py   # ReAct 循环（模型 → 工具 → 再模型），对应参考里的 ReactAgent
agent_tools.py    # 工具注册表 + 内置工具，对应参考里的 agent_tools / middleware
web_search.py     # 联网搜索后端（Tavily → SearxNG → DuckDuckGo 回退）
token_usage.py    # token 统计（优先真实 usage，缺失时估算）
stream_utils.py   # 共享流式解析（避免 SilverWolf ↔ agent_runner 循环依赖）
```

【关键设计】模型只会「说」要调用哪个工具，真正的执行在 `agent_tools.execute_tool()`，
所以权限控制集中在一处：只允许写知识库文本文件与 `data/tools.json`（声明式 HTTP 工具，
不执行任意代码），全部改动写进 `data/agent_changes.log` 供审计。

【经验】看架构图要学「分层与职责」，不是学文件名；对外部框架要说清引入成本。

---

### 案例 13：改完没生效——新接口 404 的教训

【现象】给后端加了 `/api/agent/search_test`，重启前的服务上调用直接 404。

【根因】我先把服务跑起来，之后才改的代码；uvicorn 没开 `--reload` 时不会自动加载新代码。

【修复】重启服务再验证（并且把这条写进 AGENTS.md 铁律：**改动必须核对真的生效**，
包括重启服务、刷新缓存、真实调用接口或页面）。

【经验】「写完代码」≠「功能生效」。交付前必须有一个**可观察的验证动作**：
接口返回、页面元素、日志输出，三者至少看到一个。

---

### 案例 14：字段命名冲突（布尔量被列表覆盖）

【现象】Agent 的最终 meta 里既有普通聊天的 `rag_used`（布尔：本次是否用了 RAG），
又有 Agent 的 RAG 命中列表，两者同名，`meta.update()` 后布尔值被列表覆盖，前端判断逻辑会错。

【修复】把 Agent 的命中列表改名为 `rag_hits`，布尔语义保留给 `rag_used`；
前端分别读取 `message.rag_used`（是否命中）与 `message.rag_hits`（命中了什么）。

【经验】同一个名字不要承载两种类型/语义；跨层传递数据时，先定义好字段契约。

---

### 案例 15：品牌改名与回滚（版权取舍）

【背景】项目名与游戏角色直接撞名，担心版权联想，于是把品牌改成 `S_Wolf`。

【过程】改名涉及 APP_NAME、页面标题、Logo、欢迎页、bat 脚本、README 与说明书；
提交为两个 commit（代码 + 文档）。

【转折】用户最终决定「内容不动，只改 GitHub 仓库名」→ 用 `git revert` 依次回滚两个提交，
保留完整历史，而不是 `reset --hard` 抹掉记录。

【经验】

- 品牌/命名类改动属于「影响面广但价值主观」的改动，先确认范围再动手；
- 回滚优先 `git revert`（可追溯、可再回滚），不要用破坏性命令；
- 单提交快照的好处在这里体现：仓库名之外的东西可以随手回滚，公开内容始终可控。

---

### 案例 16：Word 简历读不到正文——文本框排版的结构陷阱

【现象】用 python-docx 打开用户简历，`doc.paragraphs` 和 `doc.tables` 都是 0 条，但文件有 132KB。

【定位】解包 docx 发现 `word/document.xml` 里有 81 个 `<w:p>` 与 `txbxContent`：
简历内容全部放在**文本框（floating text box）**里，python-docx 的常规 API 只遍历正文顶层段落，
所以读不到。

【处理】改用「解包 + 按 XML 顺序提取所有 `<w:t>`」的方式拿到了完整内容；
重排副本时也采用新建文档而不是硬改这些绝对定位的文本框（后者极易错位，且本环境无 LibreOffice
无法做逐页渲染校验）。

【经验】文档类任务先探测结构（段落数、表格数、是否文本框/图形），再决定编辑策略；
「文件能打开」不代表「结构可编辑」。

---

### 案例 17：测试数据纪律（tmp/ 归置 + 只删自建文件）

【背景】做头像功能测试时，测试图覆盖了 `data/avatars/bot.png`，清理时把用户真实头像一起删了。

【修复与规矩】

- 明确「用户数据」边界：`data/avatars/`、`data/providers.json`、`data/ui_state.json`、
  `chat_history.db` 一律不当作测试残留；要测就先备份、测完原样恢复；
- 与项目功能无关的文件（简历、临时脚本、渲染产物）统一放 `tmp/`（已 gitignore），收尾清空；
- 测试写入的知识库文件、会话、日志，用**精确路径/精确 ID**删除，不做目录级 `rm`。

【经验】自动化清理最危险的不是「没清干净」，而是「清多了」。能自证归属的测试数据才好删。

---

## 6. 深度复盘：报错原文 → 触发代码 → 根因 → 修复 → 原理

> 这一节按 AGENTS.md 第 11 条的要求写：把真实报错、触发它的代码、根因和原理都摊开，
> 方便照着复盘，而不是只记住「改了哪一行」。

### 深度案例 1：`list index out of range` —— 流式输出突然断掉

**① 现象与报错原文**（服务端日志，v0.5.0 时期，用云端模型时必现）

```
2026-08-30 13:02:16,444 [INFO] httpx: HTTP Request: POST https://ws-...aliyuncs.com/compatible-mode/v1/chat/completions "HTTP/1.1 200 OK"
2026-08-30 13:02:19,658 [ERROR] SilverWolf: 流式输出异常: list index out of range
```

前端表现：回复空白，刷新页面后才看到内容（内容其实已落库，只是流被中断）。

**② 触发代码**（旧 `SilverWolf.py` 的流式生成器）

```python
def generate():
    full_content = ""
    try:
        for chunk in stream:
            if chunk.choices[0].delta.content is not None:   # ← 崩在这里
                content = chunk.choices[0].delta.content
                full_content += content
                yield f"data: {json.dumps({'content': content})}\n\n"
```

**③ 根因**：OpenAI 的流式协议里 chunk 理论上都带 `choices`，但兼容实现并不保证：
阿里云百炼等网关在**最后一帧**会发 `choices: []`（有时只带 usage 统计）。
`chunk.choices[0]` 于是抛 `IndexError: list index out of range`；
异常被外层 `except` 捕获 → 流被标记结束，前端收不到后续内容。

**④ 修复代码**（抽到共享模块 `stream_utils.py`，`SilverWolf.py` 与 `agent_runner.py` 共用）

```python
def normalize_delta_text(value):
    """兼容 str 与 list[dict] 两种 content 形态。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in value) or None
    return str(value) if value else None

def extract_stream_delta(chunk):
    """空 choices / 无 delta / 列表 content / reasoning_content 全部安全处理。"""
    choices = getattr(chunk, "choices", None)
    if not choices:                     # ← 关键：先判空，再取 [0]
        return None, None
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return None, None
    return (normalize_delta_text(getattr(delta, "content", None)),
            normalize_delta_text(getattr(delta, "reasoning_content", None)))
```

**⑤ 原理**：对接第三方 API 时，**协议文档 ≠ 实现行为**。
凡是「数组 + 下标」的取值，都要先问「这个数组可能是空的吗」；凡是外部输入，
都要假设类型不标准。防御式解析应集中在**一个函数**里，而不是散落在每个调用点。

**⑥ 延伸与自检**：用真实云端模型跑一次流式，日志里不应再出现 `流式输出异常`；
再单测三种构造 chunk（空 choices / list content / reasoning）应返回预期值。
注意判断要写 `if content:`——写成 `if content is not None:` 会把 `None` 拼进字符串。

---

### 深度案例 2：界面不刷新 —— Vue 3 响应式只在「代理」上生效

**① 现象**：流式对话时气泡一直空白，刷新页面后整段回复出现。没有报错，属于静默失效。

**② 触发代码**（`frontend/src/stores.js`，v0.5.0）

```js
addMessage(role, content, extra = {}) {
    const msg = Object.assign({ id: Date.now() + Math.random(), role, content }, extra);
    this.messages.push(msg);
    return msg;        // ← 返回「原始对象」，不是数组里的响应式代理
},
```

调用处：

```js
const botMsg = this.addMessage('bot', '', { streaming: true });
botMsg.content = fullText;   // ← 直接改原始对象，Vue 收不到通知
```

**③ 根因**：Vue 3 的 `reactive()` 返回 `Proxy`，读写都要经过代理的 `get`/`set` 才会被追踪。
对象塞进响应式数组后，数组里存的是代理，而函数返回的仍是**原始对象**；
对原始对象的写操作绕过代理，视图自然不更新。刷新后能看到，是因为重新从后端加载的数据被重新包装成代理。

**④ 修复代码**

```js
addMessage(role, content, extra = {}) {
    const msg = Object.assign({ id: Date.now() + Math.random(), role, content }, extra);
    this.messages.push(msg);
    // 必须返回数组中的响应式代理，后续 msg.content = ... 才会触发渲染
    return this.messages[this.messages.length - 1];
},
```

**⑤ 原理**：Vue 2 用 `Object.defineProperty` 改写属性，原始对象也"带着"响应式；
Vue 3 的响应式是**代理层**，谁拿到代理谁才有响应式。口诀：**push 完要用数组里的那个对象**。

**⑥ 延伸与自检**：改完立刻看界面，或用 `Vue.watchEffect` 断言副作用被触发（测试脚本里就是这么验的）。
同类坑：解构 `const { a } = reactiveObj` 会丢响应式；`shallowRef` 里嵌套对象不响应。

---

### 深度案例 3：`'lama' 不是内部或外部命令` —— 批处理的换行与编码

**① 现象与报错原文**（双击 `start.bat` 时 cmd 刷出一串怪错误）

```
'lama' 不是内部或外部命令，也不是可运行的程序或批处理文件。
'.venv\Scripts\python.exe" (' 不是内部或外部命令，也不是可运行的程序或批处理文件。
'ano' 不是内部或外部命令，也不是可运行的程序或批处理文件。
```

**② 触发代码/文件状态**

- `start.bat` 里有中文与框线字符（`echo 本地模型依赖 Ollama ...`）；
- 编辑器把它存成了 **UTF-8 + LF**；
- 而 Windows cmd 按 **GBK（代码页 936）+ CRLF** 解析批处理文件。

**③ 根因**：cmd 逐字节解析 `.bat`：UTF-8 的中文按 GBK 解就成了乱码；
LF-only 换行又让 `if (...)` 这类跨行块解析错位——于是 `uvicorn` 被拆成
`uvi` + `corn`、`netstat -ano` 被拆成 `netstat -` + `ano`（这就是 `'lama'`、`'ano'` 的由来）。

**④ 修复代码**（把 `.bat` 转成 GBK + CRLF）

```powershell
$gbk = [System.Text.Encoding]::GetEncoding(936)
$text = [System.IO.File]::ReadAllText('start.bat', [System.Text.Encoding]::UTF8)
$text = $text -replace "`r?`n", "`r`n"                     # 统一 CRLF
[System.IO.File]::WriteAllText('start.bat', $text, $gbk)   # 以 GBK 落盘
```

同时首行加 `cd /d "%~dp0"`（任意路径双击都能找到 `.venv`），并加端口占用检测。

**⑤ 原理**：**「文件内容正确」≠「目标运行时能正确解析」**。批处理/shell/配置都有编码与换行约定；
跨工具链编辑时先确认"谁读它、用什么编码读"。
血泪教训：不要用"按 UTF-8 读取"去处理已经是 GBK 的文件——我这样把两个 `.bat` 二次编码成乱码，
最后靠 `git restore` 救回。

**⑥ 延伸与自检**：改完一定要真双击运行一次；`chcp` 查看代码页（936=GBK，65001=UTF-8）。

---

### 深度案例 4：新接口 404 —— “我明明改了代码”

**① 现象与报错原文**

```text
POST /api/agent/search_test → 404 Not Found
```

而代码里明明写了 `@router.post("/api/agent/search_test")`。

**② 触发过程**

1. 先启动测试服务：`uvicorn main:app --port 8010`（没加 `--reload`）；
2. 之后才新增路由；
3. 直接请求新接口 → 404。

**③ 根因**：Python 在**启动时导入模块**，路由表在那一刻就固定了；
没有热重载时，改文件不会影响正在运行的进程。**改动没生效，不等于代码写错。**

**④ 修复**：重启服务再验证（这条已写进 AGENTS.md 铁律：改动必须核对真的生效）

```powershell
# 按端口找监听进程更可靠（venv 的 python.exe 会再派生真实进程）
$c = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue
if ($c) { Stop-Process -Id $c[0].OwningProcess -Force }
# 重新启动后再打接口，看到有效响应才算改完
```

**⑤ 原理**：所有配置/路由/常量都有**生效时机**：进程启动、模块导入、浏览器加载、缓存过期……
遇到"改了没反应"，先列出这条链路上的生效点逐个排除。
前端同理：我们后来给静态资源加版本号与 `Cache-Control: no-cache`，
就是为了让"改了生效"变得确定。

**⑥ 延伸与自检**：把"重启 → 调接口 → 看日志/页面"固化为交付前动作；
用 `Get-NetTCPConnection -LocalPort <port>` 定位监听进程，比按名字杀 `python.exe` 更安全。

---

### 深度案例 5：重启后「狼尊时刻」消失 —— 只渲染不落库的数据会丢

**① 现象**：模型回答时能展开「狼尊时刻」（思考过程）；关闭服务重启后重新打开会话，
思考块没了，正文与 token 统计却还在。

**② 触发代码**（修复前的 `SilverWolf.py`，Agent 流式分支）

```python
def generate_agent():
    full_content = ""
    final_meta = {}
    ...
    elif etype == "reasoning":
        # 只把思考内容发给前端，没有累加保存
        yield f"data: {json.dumps({'reasoning': event['text'], 'session_id': session_id})}\n\n"
    ...
    save_message_with_meta(session_id, "assistant", full_content, final_meta or None)
    #                                                     ↑ meta 里只有 usage/tools/searches，没有 reasoning
```

**③ 根因**：流式接口输出三类数据——`content`（正文）、`reasoning`（思考）、`meta`（统计）。
落库时只存了 `content` 和 `meta`，`reasoning` 属于"只渲染不持久化"的临时数据；
重启后前端从数据库读历史，自然拿不到思考内容。
**前端显示什么，取决于后端存了什么。**

**④ 修复代码**：流式过程中累加思考内容，并写进消息 meta

```python
def generate_agent():
    full_content = ""
    full_reasoning = ""          # ← 新增：累加思考过程
    final_meta = {}
    ...
    elif etype == "reasoning":
        full_reasoning += event["text"]
        yield f"data: {json.dumps({'reasoning': event['text'], 'session_id': session_id})}\n\n"
    ...
    saved_meta = dict(final_meta)
    if full_reasoning:
        saved_meta["reasoning"] = full_reasoning     # ← 落库
    save_message_with_meta(session_id, "assistant", full_content, saved_meta or None)
```

前端加载历史消息时映射回气泡字段（`frontend/src/stores.js`）：

```js
...(m.meta ? {
    usage: m.meta.usage,
    tools_used: m.meta.tools_used,
    web_searches: m.meta.web_searches,
    rag_hits: m.meta.rag_hits,
    reasoning: m.meta.reasoning,   // ← v2.0.1：思考过程（狼尊时刻）重新可见
} : {}),
```

**⑤ 原理**：「渲染态」与「持久态」要分开设计。凡是用户会回看的信息（正文、思考、统计、引用来源），
都要问"它存在哪里、重开之后还在吗"。我们把这类信息统一放进 `messages.meta`（JSON 文本列）：
扩展字段不必改表结构，读取时解析一次即可。

**⑥ 延伸与自检**：

- 后端：打桩替换 `agent_runner.run_agent_stream`，产出带 `reasoning` 的事件，
  走真实 `/api/chat/stream`，再查数据库确认 `meta.reasoning` 有值（本次就是这么验的）；
- 前端：模拟 `session.get()` 返回带 `meta.reasoning` 的消息，断言 `chat.messages[i].reasoning` 被还原；
- 不同模型给思考的字段不同（`reasoning_content` 或网关不返回），UI 上"没有思考块"要视为正常。

---

### 深度案例 6：同名不同义 —— `rag_used` 布尔值被列表覆盖

**① 现象**：Agent 模式下前端"是否用了 RAG"的判断偶发失效（本该是布尔，却拿到数组）。
**不报错**，属于最难发现的逻辑错误。

**② 触发代码**（`agent_runner.py` 与 `SilverWolf.py` 拼装 meta 处）

```python
# 普通模式：rag_used 是布尔值
meta = {"rag_used": rag_info["has_context"], "rag_sources": rag_info["sources"]}
# Agent 模式：meta 里也有一个同名键，但值是列表
agent_meta = {"rag_used": [{"kb_slug": "kb", "sources": ["a.md"]}]}
meta.update(agent_meta)     # ← 布尔被列表覆盖，语义丢失
```

**③ 根因**：跨层传递的数据没有"字段契约"。同一个名字在两层里承担了不同语义
（"有没有用 RAG" vs "用了哪些 RAG 内容"），`dict.update` 静默覆盖，
类型从 `bool` 变成 `list`；前端 `if (parsed.rag_used)` 依然为真，后续用法全错。

**④ 修复代码**：拆成两个语义明确的字段

```python
# Agent meta：rag_hits 表示"命中的内容明细"，布尔语义仍由 rag_used 承担
agent_meta = {"rag_hits": [{"kb_slug": "kb", "sources": ["a.md"]}]}
```

```js
// 前端：布尔与明细分开读
botMsg.rag_used = parsed.rag_used;    // 本次是否命中知识库
botMsg.rag_hits = parsed.rag_hits;    // 命中了哪些内容（用于展示来源）
```

**⑤ 原理**：数据契约要"一名一义"。命名时自问："如果以后要扩展第 3 种情况，这个名字还准确吗"。
合并字典前先确认键的唯一性，尤其在跨模块拼装 meta 的场景。

**⑥ 延伸与自检**：加断言 `typeof meta.rag_used === 'boolean'`；
接口返回结构变化时（如 2.0.0 新增 meta），前端要兼容或版本化——我们靠缓存版本号 + 可选读法规避。

---

### 深度案例 7：`python-docx` 读不到简历正文 + 控制台 `UnicodeEncodeError`

**① 现象与报错原文**

```text
PARAGRAPHS: 0
TABLES: 0
（文件 132KB，用 Word 打开内容完整）
```

```text
UnicodeEncodeError: 'gbk' codec can't encode character '\u2022' in position 0: illegal multibyte sequence
```

**② 触发代码**

```python
doc = Document(r'简历.docx')
doc.paragraphs   # → []  （以为文档是空的）
doc.tables       # → []
```

```python
print('• 列表项')   # 在 GBK 控制台直接抛 UnicodeEncodeError
```

**③ 根因**：

1. 简历用**文本框（floating text box）**排版，文字都在 `word/document.xml` 的
   `<w:txbxContent>` 里；`python-docx` 的 `paragraphs` 只遍历正文顶层段落，
   所以返回空——**不是文件损坏，而是 API 覆盖不到**。
2. 控制台报错是另一回事：Windows 终端默认 GBK，`•`、`—` 等字符无法编码。

**④ 修复代码**

```python
# 结构探测：先确认有没有文本框，再决定读取方式
import zipfile, re
xml = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8')
print('has txbxContent:', 'txbxContent' in xml)

# 按文档顺序提取所有 <w:t>（文本框内文字也在其中）
texts = re.findall(r'<w:t[^>]*>(.*?)</w:t>', xml, re.S)
```

```python
# 中文/特殊字符输出：写文件而不是直接 print
open('out.txt', 'w', encoding='utf-8').write('\n'.join(texts))
```

**⑤ 原理**：Office 文件要区分「结构层」（段落/表格/文本框/图形）与「内容层」（文本节点）。
库的 API 只覆盖常见结构；遇到文本框、组合图形、分栏，就要退回 OOXML 层。
控制台问题属于"输出通道"限制，把结果写文件再读最省事。

**⑥ 延伸与自检**：陌生文档先做三步探测——段落数、表格数、是否含 `txbxContent`；
打印中文前先确认控制台编码（`chcp`）。同样的思路适用于 PDF（扫描件需 OCR）、
Excel（合并单元格会打乱行列对应）。

---

### 深度案例 8：联网搜索方案取舍 —— 库、爬虫与「抓下来的不是正文」

**① 现象与报错原文**

最初自己用正则解析 DuckDuckGo 结果页，在任何网络稍差的环境下都会失败：

```text
[WARNING] web_search: 搜索后端 duckduckgo 失败: timed out
{"ok":false,"engine":"","count":0,"results":[],
 "error":"tavily: 未配置 TAVILY_API_KEY; searxng: 未配置 SEARXNG_URL; duckduckgo: timed out"}
```

换用 `ddgs` 库后，在这台受限的沙箱里拿到的是更具体的底层报错（有诊断价值）：

```text
failed to load native root certificate: failed to open current user certificate store: 拒绝访问。 (os error 5)
[WARNING] web_search: 搜索后端 ddgs 失败: error sending request for url
  (https://www.google.com/wml/search?q=Python+asyncio+%E6%95%99%E7%A8%8B...) > operation timed out
[WARNING] web_search: 搜索后端 duckduckgo-html 失败: timed out
```

**② 触发代码**（旧实现：自己解析结果页 HTML）

```python
links = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
```

**③ 根因**：这属于**自己实现第三方接口**。问题有三层：

1. 结果页结构随时会变，正则一失效就是"搜索为空"，而且没有明确报错；
2. 拿不到结构化字段（标题/链接/摘要要各自提取、下标还要对齐）；
3. 网络层（UA、重定向、地区限制、证书）都得自己处理。

**④ 修复代码**（改用 `ddgs` 库 + 四层回退）

```python
def _search_ddgs(query: str, max_results: int):
    from ddgs import DDGS                      # 免费、无 Key
    with DDGS() as ddgs:
        raw = ddgs.text(query, region="cn-zh", safesearch="moderate", max_results=max_results)
    return [{"title": _clean(i.get("title", "")),
             "url": i.get("href", ""),
             "snippet": _clean(i.get("body", ""))[:400]} for i in raw or []]

# 搜索链路：能用的先用，全部失败时把每一层的原因拼起来返回给模型/用户
backends = (("tavily", _search_tavily), ("searxng", _search_searxng),
            ("ddgs", _search_ddgs), ("duckduckgo-html", _search_duckduckgo))
```

正文抽取也从"正则去标签"升级为按语义取正文（并保留 Firecrawl 作为可选增强）：

```python
def _extract_main_text(html_text: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe"]):
        tag.decompose()                       # 先去噪声
    box = (soup.find("article") or soup.find("main")
           or soup.find(attrs={"role": "main"}) or soup.body or soup)
    return "\n".join(ln.strip() for ln in box.get_text("\n").splitlines() if ln.strip())
```

**⑤ 原理与取舍**：

| 方案 | 优点 | 代价 | 适用 |
|------|------|------|------|
| 自己解析 HTML | 零依赖 | 结构一变就废、无结构化字段 | 不推荐 |
| `ddgs` 库 | 免费无 Key、结构化返回、维护者跟着改 | 国内可达性差 | 本地 demo / 海外网络 |
| SearxNG | 可自建、隐私好、JSON 输出 | 需要自己部署或找公共实例 | 长期自用 |
| Tavily | 为 LLM 设计、质量最好 | 需要 Key、有额度 | 正式使用 |
| Firecrawl 抓正文 | 能渲染 JS、正文质量高 | 需要 Key、按次计费 | 复杂站点深读 |
| BeautifulSoup 抓正文 | 无需 Key、离线可用 | 拿不到 JS 渲染内容 | 默认方案 |

另一条重要原则是**优雅降级**：任何一层失败都不抛给用户，而是继续尝试下一层，
最后把各层的原因合并成一句人话（前端「测试搜索」直接展示），
让用户知道"是没配置，还是网络不通"，而不是看到一个空结果发懵。
顺带一句：抓取网页属于爬虫行为，注意 robots.txt、频率与版权边界，别把它当"随便扒"的借口。

**⑥ 延伸与自检**：

- 单元测试正文抽取（喂一段含 nav/footer/script 的 HTML，断言正文保留、噪声被剔除）；
- 接口自检：`POST /api/agent/search_test` 返回 `engine` 与 `count`，失败时给 `error`；
- 环境排查：`failed to load native root certificate ... (os error 5)` 是底层 HTTP 客户端读
  Windows 证书库被拒（受限环境常见），换到正常桌面环境或改用其它后端即可，
  这类错误要区分"代码错"还是"环境限制"。

---

### 深度案例 9：自研爬虫搜索 —— "免 Key" 的代价与边界

**① 现象与报错原文**

先是 Tavily 的 Key 配好后立刻可用，但用量总会用完；于是自己做"抓搜索页"的兜底。
实测三种情况都出现过：

```text
# 情况 A：Tavily 首次调用偶发空结果（同一 Key 手动调用正常）
{"ok":false,"engine":"","count":0,"results":[],"error":"tavily: 无结果"}

# 情况 B：百度返回反爬验证页（HTTP 200，但正文只有 1.4KB）
STATUS: 200 LEN: 1438
TITLE: 百度安全验证
contains "result c-container": False

# 情况 C：Bing 解析成功，返回真实结果
{"ok":true,"engine":"bing-html","count":3,"results":[{"title":"教程 | OpenAI 官方 帮助 文档 中文版",...}]}
```

**② 触发代码**（第一版：只抓页面、不做任何防护与判定）

```python
def _search_baidu_html(query, max_results):
    resp = httpx.get("https://www.baidu.com/s", params={"wd": query}, timeout=_TIMEOUT)
    soup = BeautifulSoup(resp.text, "html.parser")
    return [... for item in soup.select("div.result")]      # ← 验证页里当然没有这些节点
```

**③ 根因**：

1. **反爬**：无 Cookie 直连百度会拿到"安全验证"页（HTTP 200，内容极短），
   解析不到任何结果节点，旧代码只会笼统地报"无结果"，看不出是被拦了；
2. **频率**：连续快速请求会被封 IP（尤其在同一出口 IP 上）；
3. **结构脆弱**：搜索结果页的 class 名会随改版变化，解析逻辑必须允许失败并降级；
4. **编码**：百度页面存在 GBK/UTF-8 混杂的历史包袱，字符串匹配要注意解码方式。

**④ 修复代码**

```python
_SCRAPE_MIN_INTERVAL = 1.5       # 两次爬虫之间的最小间隔（秒）

def _throttle():
    """控制频率 + 随机抖动，降低被封风险。"""
    global _LAST_SCRAPE_TS
    wait = _SCRAPE_MIN_INTERVAL - (time.time() - _LAST_SCRAPE_TS)
    if wait > 0:
        time.sleep(wait)
    time.sleep(random.uniform(0.3, 0.8))
    _LAST_SCRAPE_TS = time.time()

def _search_baidu_html(query, max_results):
    _throttle()
    resp = httpx.get("https://www.baidu.com/s", params={"wd": query, "rn": max_results * 2},
                     headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    head = resp.text[:3000]
    if "安全验证" in head or ("verify" in head.lower() and len(resp.text) < 6000):
        raise RuntimeError("百度返回安全验证页（需要 Cookie / 人工验证），已跳过")   # ← 说清为什么
    soup = _soup(resp.text)
    results = [...]
    if not results:
        raise RuntimeError("百度未解析到结果（页面结构可能已变化或被拦截）")
    return results
```

搜索链也收敛成两层，并把每一层的失败原因合并返回：

```python
all_backends = (("tavily", _search_tavily),        # 主：有 Key 时质量最好
                ("bing-html", _search_bing_html),  # 兜底 1：实测可用
                ("baidu-html", _search_baidu_html))  # 兜底 2：常被验证页拦，失败原因照实报
```

**⑤ 原理与取舍**：

- **"免 Key"不等于"无成本"**：成本从"配额"换成了"反爬对抗 + 维护解析规则"。
  所以正确姿势是"主后端用 API、兜底用爬虫"，而不是全押在爬虫上；
- **反爬要能被识别**：HTTP 200 不代表成功，要校验"页面里有没有预期的结构"，
  否则会把"被拦截"误判成"没有结果"，让人查半天；
- **礼貌爬取**：带真实 UA、限速、只在需要时抓、失败就降级，不做批量采集；
- **结构脆弱要有兜底**：解析失败 → 换下一个引擎 → 全失败则明确告诉用户"这次没搜到"，
  绝不让模型编造搜索结果。

**⑥ 延伸与自检**：

- 离线单测：把一段样例 HTML（含 `li.b_algo` / `div.result`）喂给解析函数，
  断言标题、链接、摘要都对（本次就是这么验的，避免"没网就没法测"）；
- 在线自检：`POST /api/agent/search_test` 分别传 `engine=tavily|bing|baidu`，
  看 `engine` 与 `count`；
- 排错顺序：先看"是不是被验证页拦了"（内容长度 < 6KB、含"安全验证"），
  再看"是不是结构变了"（解析结果为空），最后才怀疑网络。

---

### 深度案例 10：模型不知道"今天" —— 把缺失能力工具化

**① 现象与原文**（用户真实对话记录里的片段）

```text
用户：今天 deepseek 有什么新消息吗
助手：……先声明一句：我看不到你系统里的"今天"具体是哪一号，所以下面按官方更新日志的时间戳给你排，
      最新的一条在最上面，你自己对表。
```

模型的回答本身没错（它确实不知道），但体验很差：**"今天/本周/最新"是搜索类问题的常见定语**，
拿不到当前日期就只能含糊其辞。

**② 触发代码**：Agent 的工具表里只有 `web_search` / `fetch_page` / RAG 系列，
没有任何"取当前时间"的工具——模型想确认日期也无从下手。

**③ 根因**：大模型的知识是**训练截止时刻的快照**，它不是"持续运行的时钟"；
即使推理时是 2026 年，它也不知道现在是几月几号、星期几。
凡是依赖"当下"的信息（时间、天气、股价、新闻），都必须由外部工具提供。

**④ 修复代码**：新增内置工具

```python
def _tool_get_current_time(args):
    now = time.time()
    local = time.localtime(now)
    weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][local.tm_wday]
    text = (f"当前本地时间：{time.strftime('%Y-%m-%d %H:%M:%S', local)}（{weekday}）\n"
            f"时区：{time.strftime('%Z', local)}，Unix 时间戳：{int(now)}\n"
            f"UTC 时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now))}")
    return text, {"ok": True, "local_time": ..., "weekday": weekday, "timestamp": int(now)}

BUILTIN_TOOLS["get_current_time"] = {
    "description": "获取当前日期与时间（含星期与时区）。当用户问'今天/现在/最近/最新'时先调用它。",
    "parameters": {"type": "object", "properties": {}},
    "handler": _tool_get_current_time,
}
```

顺带让搜索结果自带抓取时间（`fetched_at`），模型判断"新鲜度"时有依据：

```python
return {"ok": True, "engine": name, "results": results, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
```

**⑤ 原理**：**"模型不知道的事，用工具补"，而不是靠提示词"记得今天是几号"。**
提示词里写死日期会过期；工具每次都返回实时值，永远正确。
这也是 Agent 与纯聊天模型的本质区别：把"能力缺口"显式建模成工具。

**⑥ 延伸与自检**：

- 单测：断言工具返回的日期等于 `time.strftime('%Y-%m-%d')`；
- 端到端：真实对话问"今天是几号"，检查 `tools_used` 里出现 `get_current_time`，
  且回答日期正确（本次实测返回"2026 年 9 月 15 日，星期二"）；
- 同类可扩展工具：天气、汇率、股价、单位换算——判断标准只有一个：
  **这个信息会不会随时间/外部状态变化？** 会，就该是工具，而不是塞进提示词。

---

### 深度案例 11：Token 为什么烧得这么狠 —— 上下文膨胀 × 多轮工具循环

**① 现象与原文数据**：用户问「今天 deepseek 有什么新消息」这种需要联网的问题，
一条回复底部的 token 统计是这样的（同一天的两次实测）：

```
输入 15145 / 输出 ~600  （4 步：搜索 → 抓正文 → 再搜索 → 回答）
输入 14478 / 输出 ~500  （3 步）
```

而当天的另一次简单问答（只调了 `get_current_time`，一轮结束）：

```
prompt 4339 / completion 61 —— 合计 4400
```

同样是"问一句话"，前者是后者的 **3.4 倍**。用户的原话是"你烧 token 有点狠"。

**② 触发代码**：Agent 循环每跑一步，都把**完整的 `messages` 重新发一遍**：

```python
# agent_runner.py（修复前）
while step < max_steps:
    stream = client.chat.completions.create(
        model=model, messages=messages, tools=tools_schema or None, stream=True,
    )
    ...
    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result["content"][:6000]})
```

配套的两个"放大因子"：

```python
# 1. 工具结果最多 6000 字符直接塞进上下文，而且每轮都跟着重发
"content": result["content"][:6000]

# 2. 历史只按"条数"限制，没有 token 预算的概念
MAX_HISTORY = 20      # 20 条长消息可以是 2 万 token
```

**③ 根因**：这是**无状态 HTTP 协议 + 按量计费**叠加出来的必然结果，四个因素相乘：

1. **每步重发**：LLM API 不记得上一轮对话，Agent 的每一步都得把"到目前为止的全部内容"
   重新传一次。4 步 = 同一份上下文付 4 次钱；
2. **prompt 才是账单主体**：输出只有几百 token，但每一步的输入都是"系统提示 + 全部历史 +
   所有工具结果"的累加，15000 里绝大部分是输入；
3. **工具结果是重灾区**：一次搜索返回 5 条结果 + 抓一篇正文，动辄 3000–6000 字符
   （≈1500–3000 token），写进上下文后**每一步都会重新计费一次**；
4. **历史没有预算**：`MAX_HISTORY = 20` 只管条数，不管长度——人设提示词约 1000 字、
   开场白、RAG 注入、20 条长对话，全都摊在每一轮里。

一句话概括：**成本 ≈ 上下文大小 × 循环轮数**。

**④ 修复代码**：新增 `context_manager.py`，从三个方向掐住成本：

```python
# context_manager.py —— 按 token 预算裁剪历史（超预算丢最旧的，锚点必留）
def trim_messages(messages, budget_tokens=6000, keep_head=2):
    head = messages[:keep_head]          # system 提示 + 助手开场
    tail = messages[keep_head:]
    last = tail[-1]                      # 当前提问：绝不能被裁掉
    history = tail[:-1]
    kept, used = [], 0
    for msg in reversed(history):        # 从最新往旧累加，够预算就停
        cost = estimate_tokens(str(msg.get("content") or "")) + 4
        if kept and used + cost > budget_tokens:
            break
        kept.append(msg); used += cost
    kept.reverse()
    dropped = history[: len(history) - len(kept)]
    return head + kept + [last], dropped
```

```python
# agent_runner.py —— 工具结果截断 + 单次回复上限
"content": context_manager.truncate_text(result["content"], tool_output_limit),  # 默认 3000 字符
stream = client.chat.completions.create(..., max_tokens=reply_max_tokens)        # 默认 2048
```

被裁掉的旧消息不是简单丢弃，而是**压成摘要存回会话**（`sessions.summary` 列），
下次对话作为一条 system 消息带回，实现"滚动摘要"：

```python
# SilverWolf.py —— 每轮对话开始前的上下文整理
messages = _apply_context_management(session_id, messages, provider)
# 1) 读旧摘要插到开场之后；2) 按预算裁剪；3) 有裁剪且开关打开 → 压缩并回写摘要
```

**⑤ 原理**：

- **LLM 是无状态的**，"记忆"是客户端每轮重新喂进去的，所以上下文长度直接等于成本；
- 成本模型是 `上下文大小 × 轮数`，那么优化只有两条路：**把上下文压小**、
  **把轮数减少**。压缩历史与截断工具结果属于前者，`max_steps` 与按需关联网属于后者；
- 裁剪必须保证"锚点"完整：system 提示（人设与规则）、当前提问不能丢，
  否则要么人设崩，要么答非所问。这是 trimming 类逻辑最容易写错的地方；
- 压缩本身也要花钱（一次小请求），所以要**限制输出长度（≤300 token）**，
  并在"确实裁掉了消息"时才触发，否则会出现"为了省钱又烧钱"。

**⑥ 延伸与自检**：

- 实测效果（脚本 `tmp/test_v204.py`）：一段 35076 token 的长历史，
  裁剪后 6676 token（其中锚点本身约 996 token），**历史部分省掉约 90%**；
- ⚠️ **裁剪预算的语义要写清楚，否则测试断言必错**：预算只管"历史部分"，
  system 提示、助手开场、当前提问属于锚点，不占预算。我第一版断言写成
  `after <= 6000 + 600` 直接 FAIL（`after=6676`），排查后才发现要
  `after - anchors_tokens <= 6000` 才对。**测试失败先怀疑断言，再怀疑实现**；
- 单测要覆盖：锚点保留、当前提问保留、丢弃条数 = 中间历史 − 保留历史、短文本不被截断、
  压缩在没有 provider 时优雅降级（返回旧摘要而不是抛异常）；
- 给用户的实用建议：简单问答把 Agent 模式或联网关掉；`max_steps` 从 4 降到 2；
  常查的资料塞进 RAG（检索只带相关片段）而不是每次联网；Tavily 额度紧张就关掉它。

---

### 深度案例 12：CDP 直连 Chrome 报 403 —— 真实浏览器自动化被"安全策略"拦下

**① 现象与报错原文**：

```
websocket._exceptions.WebSocketBadStatusException: Handshake status 403 Forbidden
 -+-+- {'content-length': '239', 'content-type': 'text/html'}
 -+-+- b'Rejected an incoming WebSocket connection from the http://127.0.0.1:9227 origin.
          Use the command line flag --remote-allow-origins=http://127.0.0.1:9227 to allow
          connections from this origin or --remote-allow-origins=* to allow all origins.'
```

**② 触发代码**：

```python
# tmp/cdp_ui_v204.py（修复前）
page = next(t for t in httpx.get(f"{CDP}/json/list").json() if t["type"] == "page")
ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)   # ← 这里 403
```

**③ 根因**：这是 **Chrome 的安全策略**，不是代码写错。`websocket-client` 默认会带上
`Origin: http://127.0.0.1:9227` 请求头，而新版 Chrome 对 DevTools Protocol 的 WebSocket
增加了来源校验：**防止任意网页偷偷连上本地调试端口**（本地调试端口的权限等于"控制浏览器"，
如果网页能直连，就等于任何网站都能操作你的浏览器、读取已登录的页面内容）。
浏览器把这类攻击面（DNS rebinding / 网页直连 localhost 调试端口）直接封掉了。

**④ 修复代码**：

```python
# 关掉 Origin 头再握手（我们本来就是本地脚本，不是网页）
ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
```

另一种做法是启动 Chrome 时放行来源（仅调试环境使用）：

```bat
chrome.exe --remote-debugging-port=9227 --remote-allow-origins=http://127.0.0.1:9227 --user-data-dir=%TEMP%\sw_chrome_ctx
```

**⑤ 原理**：调试协议是"高权限接口"，所以实现方必须假定**调用者可能来自不可信来源**。
Chrome 的取舍是"默认拒绝 + 显式放行"（默认安全，需要的人手动开）。
遇到 403/401 这类"握手就被拒"的错误，优先怀疑**鉴权与来源策略**，
而不是去改请求体——请求体根本没机会被读到。

**⑥ 延伸与自检**：

- 按项目规范（AGENTS §3），UI 测试必须用**真实浏览器**，不允许拿无头浏览器冒充；
  本环境没有现成的浏览器自动化工具，于是采用"启动真实 Chrome + CDP 驱动"的方式，
  能真实点击、输入、读 DOM，等价于人在浏览器里操作；
- 自检套路：先确认 `GET /json/version` 能返回（浏览器在），
  再从 `/json/list` 里挑 URL 匹配的那一个 page target（否则可能连上空白页）；
- 本次验证结果：17 项检查全过 —— 页面不白屏、设置弹窗能打开、Agent 页签可点、
  「启用 Tavily」开关与「上下文管理」三项数值框都在、界面上改动能写回后端、
  控制台无报错、无 404 资源；
- 测试完记得关掉自己启动的 Chrome 与临时用户目录，别把测试进程留在别人机器上。

---

### 深度案例 13：从"平铺一堆 py"到分层目录 —— 注册表为什么要改成自动发现

**① 现象（重构前的痛点）**：项目根目录平铺着 20 个 `.py`，
其中 `agent_tools.py` 一个文件 460 行、装了 11 个工具的**实现 + 定义 + 注册表 + 分发**。
想加一个"查天气"工具，得在同一文件里找三个地方改：写 handler、往 `BUILTIN_TOOLS` 里加条目、
确认 `openai_tool_schemas()` / `execute_tool()` 的分支还认它。
用户的反馈很直接：**"agent_tools 只是一个文件，不方便添加、删除、修改管理。"**

**② 触发代码（重构前）**：

```python
# agent_tools.py（改前）：实现、定义、注册全挤在一起
def _tool_web_search(args): ...
def _tool_fetch_page(args): ...
def _tool_rag_search(args): ...
# … 还有 8 个 …

BUILTIN_TOOLS = {
    "get_current_time": {"description": ..., "parameters": ..., "handler": _tool_get_current_time},
    "web_search":       {"description": ..., "parameters": ..., "handler": _tool_web_search},
    # … 每个工具都要在这里再登记一次 …
}
```

**③ 根因**：这是典型的**"文件承担了太多角色"**。一个模块同时是
「实现库 + 配置表 + 分发器」，任何新增都要横跨三种关注点；文件越长，
改错位置的概率越高（漏登记 = 工具存在但模型看不到，这种 bug 还特别难查）。

**④ 修复代码（重构后）**：

```python
# agent/tools/datetime_tool.py —— 加工具 = 加文件，文件里自带定义与实现
def _tool_get_current_time(args):
    info = get_current_time()
    return info["text"], {"ok": True, **info}

TOOLS = {
    "get_current_time": {
        "description": "获取当前的日期与时间（含星期与时区）…",
        "parameters": {"type": "object", "properties": {}},
        "handler": _tool_get_current_time,
    },
}
```

```python
# agent/tools/__init__.py —— 注册表变成"扫描目录"，不用再手写登记
def _discover():
    for mod_info in sorted(pkgutil.iter_modules([str(Path(__file__).parent)])):
        if mod_info.name.startswith("_"):      # 下划线开头 = 内部辅助，不是工具
            continue
        module = importlib.import_module(f"{__name__}.{mod_info.name}")
        for tool_name, spec in (getattr(module, "TOOLS", {}) or {}).items():
            BUILTIN_TOOLS[tool_name] = spec
```

分层后的结构（依赖方向单向）：

```
SilverWolf.py → agent/（runner.py + tools/） → middleware/（context / token_usage / stream_utils）
```

**⑤ 原理**：

- **模块的边界应该跟着"变化的频率"划，而不是跟着"代码量"划**。
  工具集合会不断增删（高频变化），注册逻辑几乎不动（低频），
  所以把两者拆开，让高频的那部分变成"加文件"；
- **用文件系统当注册表**是插件化的经典做法（pytest 收集测试、Flask 收集路由同理）：
  约定优于配置 —— 只要文件在、并且导出约定名字的变量，就自动生效；
- 自动发现要留**逃生口**：失败要能降级（单个模块导入失败只跳过它并记日志），
  否则一个语法错误会让整个 Agent 起不来。这一点在 `_discover()` 的 try/except 里体现了；
- 分层的硬约束是**依赖方向**：上层可以 import 下层，下层不许反向依赖，
  否则 middleware 会悄悄变成"什么都知道"的上帝模块。

**⑥ 延伸与自检**：

- 重构必须"行为不变"：这次跑了两轮验证 —— 后端接口冒烟（工具列表 11 个、搜索自检、
  流式对话）+ 真实浏览器界面回归，确认只是搬家不是改行为；
- 用 `git mv` 而不是"删了再新建"，git 才能识别为 rename，历史可追溯；
- 自检命令：`python -c "from agent import tools; print([t['name'] for t in tools.list_tools()])"`，
  工具数不对就说明某个文件没被发现（多半是没导出 `TOOLS` 或文件名以 `_` 开头）。

---

### 深度案例 14：上下文窗口 "6000" 到底管什么 —— 预算语义与 UI 语义对齐

**① 现象**：用户问"为什么别人是 200K-1M，你只有 6000"。
查代码发现两个问题：

```python
# middleware/context.py（改前）
DEFAULT_HISTORY_BUDGET = 6000        # ← 只约束"历史消息"
kept, dropped = trim_messages(messages, budget, keep_head=keep_head)
```

一是**范围太小**（1000~32000），二是**语义含混**：文档写的是"历史部分预算"，
但 `system` 提示、RAG 注入、当前提问都不在预算内 —— 用户设 6000，
实际发出去的可能是 6000 + 人设 1000 + RAG 3000 + 提问。用户在界面上看到的是
"我的窗口是 6K"，实际请求是 1 万，**口径不一致，数字就失去意义**。

**② 触发代码（改前）**：

```python
kept_history, used = [], 0
for msg in reversed(history):
    cost = estimate_tokens(str(msg.get("content") or "")) + 4
    if kept_history and used + cost > budget_tokens:   # used 不含锚点
        break
```

**③ 根因**：预算的**锚点（system / 开场 / 当前提问）不参与计数**，
所以"预算"与"实际上下文大小"之间永远差一个常数项。
这种偏差在小窗口时占比很大（6K 里差 4K 就是 40%），用户会觉得"这设置没生效"。

**④ 修复代码（改后）**：

```python
# 锚点先占预算，剩下的才是历史能用的额度 —— 预算 = 整个请求
anchors_cost = _messages_cost(head) + _messages_cost([last])
min_keep_msgs = max(0, int(min_keep_turns) * 2)      # 保底轮数：一轮=一问一答
for msg in reversed(history):
    cost = _messages_cost([msg])
    if anchors_cost + used + cost > budget_tokens and len(kept_history) >= min_keep_msgs:
        break
    kept_history.append(msg); used += cost
```

配套三件事：
1. 范围放宽到 4K ~ 1M（`MIN_CONTEXT_BUDGET` / `MAX_CONTEXT_BUDGET` 常量集中定义）；
2. 新增「保底最近轮数」：窗口再紧，最近 N 轮一定留（宁可略超预算，也不让模型断片）；
3. 服务端统一夹取范围（`_clamp_context_settings`），UI 与接口用同一套上下限。

界面上给两种输入方式：**档位滑块 + 手动输入框**。原因是
0~1000000 的线性滑块手感极差（前 80% 的行程都在选 8K~200K，
后面一点点就跳到 1M），所以滑块走"离散档位"（8K/16K/…/1M），
手输任意值时再把滑块吸附到最近档位。

**⑤ 原理**：

- **设置项的名字必须等于用户脑子里的那个量**。用户能感知的是"我这次对话总共用了多少上下文"，
  所以预算就该定义在整个请求上；只约束局部（"历史部分"）会让数字对不上，从而失去可信度；
- **裁剪算法要区分"可牺牲的"和"不可牺牲的"**：`system`（人设与规则）、
  当前提问属于后者，必须留；历史属于前者，可以按预算丢弃、并用摘要补偿；
- **保底 + 预算并存**时要有明确的优先级："保底"是硬下限，"预算"是软上限，
  冲突时保底赢 —— 因为断片的对话毫无价值，多花一点 token 是可接受的代价；
- 数值型设置**一律在服务端夹取**（`max(lo, min(v, hi))`）：
  界面是第一道防线，接口是第二道，配置文件被手改是第三道 —— 三道都在，程序才不会被玩坏。

**⑥ 延伸与自检**：

- 单测覆盖：总量不超预算、锚点保留、预算压到 4K 时保底 6 条仍在、1M 时不丢历史、
  越界值被夹住（16 项断言全过）；
- 真实浏览器验证：拖滑块到 200K → 后端 `ui_state.json` 记录 200000；
  手输 1000000 → 显示 1M 且滑块吸附到最后一档；
- 一个容易踩的坑：JSON 里 `true` 在 Python 里是 `bool`，而 `bool` 是 `int` 的子类，
  夹取时要先 `isinstance(value, bool)` 排除，否则 `True` 会被写成 `1`。

---

### 深度案例 15：改完没编译就往下走 —— 一次 `IndentationError` 的教训

**① 现象与报错原文**：起服务时直接崩，`%TEMP%\sw21.err.log` 里是：

```
  File "E:\create\program\Silver_Wolf\SilverWolf.py", line 1147
    data.update(_clamp_context_settings({
IndentationError: unexpected indent
```

第 1147 行确实"看起来不对"：它出现在文件**末尾**（`router.include_router(kb_router)` 之后），
而这段代码本该在 `api_save_ui_state()` 里面。

**② 触发代码**：用 `apply_patch` 修改这段逻辑时，先加了一版带 `only_present=True` 参数的调用，
紧接着又用一次「把参数去掉」的补丁去改它 —— 第二次补丁的匹配范围没锁死，
结果把整段调用**又插入到了文件尾部**，且保留了缩进。

**③ 根因**：文本补丁工具是"按上下文匹配"的，不是"按函数定位"的。
当补丁的上下文只有几行、并且这几行在文件里出现过两次（一次是刚插入的新版本），
就可能匹配到非预期位置。**这跟代码本身无关，是改代码的流程问题**：
改完没做语法检查就继续下一步，错误被推迟到"起服务"时才暴露，
期间还跑了一轮通过不了的测试，浪费的是排查时间。

**④ 修复代码**：

```python
# 删掉文件末尾误插入的 5 行（它们与 api_save_ui_state 里的调用完全重复）
# ── 挂载知识库路由（作为子路由） ──
router.include_router(kb_router)
```

改完立刻验证：

```bat
.venv\Scripts\python.exe -m py_compile SilverWolf.py
.venv\Scripts\python.exe -c "import main; from agent import runner, tools; from middleware import context; print('OK')"
```

**⑤ 原理**：

- **补丁类修改必须紧跟一次"最便宜的验证"**：`py_compile` 只要几十毫秒，
  却能拦住全部缩进/语法类错误。把它当成"写完一行代码的保存动作"，
  而不是"提交前的仪式"；
- 文件级工具的共同弱点：**同一个片段出现两次时会匹配到错的那个**。
  防御手段是让上下文足够长且唯一（带上函数名、上一行的独特注释），
  改完再用 `rg` 搜一遍关键词确认只出现一次；
- 报错行号指向"语法上第一个不合法的地方"，不一定是"逻辑上写错的地方"——
  本例中真正的问题是**上面多出的那 5 行**，而报错行只是被连累的位置。

**⑥ 延伸与自检**：

- 这类错误的最佳定位方式：`git diff` 看自己刚改了什么，
  再用 `rg -n "被插入的代码片段" 文件` 确认出现次数；
- 建立习惯：**每次改动后依次跑** ①`py_compile`/`node --check` ②导入检查 ③接口冒烟，
  三步都在 10 秒内，能挡掉绝大多数"低级但消耗时间"的故障；
- 顺带记一条：这次错误还暴露了"测试脚本的断言写错会被误当成代码 bug"——
  同一轮里我写的两条断言（丢弃条数公式、预算与锚点）都是**测试写错**，
  所以现在的习惯是：**测试失败先怀疑断言，再怀疑实现**。

---

### 深度案例 16：「无限」要用"不发参数"实现，而不是发一个很大的数字

**① 现象与需求**：用户要两个开关 —— 「无限轮数」（历史一条不丢）和
「无限长度」（单次回复不限制）。直觉写法是把数值设成一个大到不会触发的数字，
比如 `context_budget = 999999999`、`max_tokens = 1000000`。这个写法有坑。

**② 触发代码（直觉写法，容易踩坑）**：

```python
# ❌ 看起来能用，其实三处都埋雷
client.chat.completions.create(model=model, messages=messages,
                               stream=True, max_tokens=1_000_000)
```

**③ 根因**：

1. **服务端会校验上限**：多数厂商对 `max_tokens` 有硬上限（4K/8K/16K），
   超了直接返回 400 —— "无限"反而变成"完全不能用"；
2. **账单失控且不可预期**：真发 100 万，模型有可能会一直吐到上限；
3. **`null` 也不安全**：有些服务商把 `max_tokens: null` 当作非法值报错，
   所以"显式传空"同样不行；
4. **轮数侧同理**：把预算设成 10 亿，裁剪逻辑虽然不会触发，
   但 JSON 里存一个假数字会让后续调试（和用户）分不清"真的无限"还是"设得很大"。

**④ 修复代码（改后）**：

```python
# ✅ 用"布尔开关 + 不发送该参数"表达无限
token_kwargs = {"max_tokens": reply_max_tokens} if reply_max_tokens else {}
stream = client.chat.completions.create(
    model=model, messages=messages, stream=True, **token_kwargs,
)
```

```python
# 轮数侧：显式参数，裁剪函数第一行就短路
def trim_messages(messages, budget_tokens=..., keep_head=2, min_keep_turns=..., unlimited=False):
    if unlimited:
        return list(messages), []      # 一条都不丢，dropped 为空 → 也不会触发压缩
    ...
```

服务端把开关翻译成 `None`（`_runtime_limits` 里 `reply_max_tokens = None`），
四条调用链路（普通对话 / Agent 循环 / 云端失败回退本地 / 非流式）统一按
"有值才带参数"处理。

**⑤ 原理**：

- **"缺席"比"极大值"更能表达"没有约束"**：协议层面不传参数 = 使用服务端默认，
  这正是"不额外限制"的正确语义；用一个假的大数字去模拟，等于把一个语义问题
  伪装成数值问题，必然在某个校验点上翻车；
- 布尔开关要在**最外层**（设置 → 运行参数）翻译成 `None`，
  让内层函数只认 `Optional`，避免每个调用点各自判断；
- 界面上要给出**视觉反馈**：开关打开后把滑块与输入框置灰，
  否则用户会以为"拖不动"是 bug —— 状态与控件的联动也是功能的一部分。

**⑥ 延伸与自检**：

- 单测断言的是"开关 → 运行参数"这一层：`reply_unlimited=True` 时
  `_runtime_limits()["reply_max_tokens"] is None`，关掉后恢复 8192；
- 端到端验证：开着两个开关跑真实流式对话（本地 Ollama），确认不报 400、
  输出正常（实测 52 个数据块，回复完整），再关掉开关跑一次确认默认路径没被破坏；
- 反向自查：**任何一种"参数越界就会被服务端拒绝"的字段，都不该用大数字模拟无限**
  —— 同类字段还有 `top_k`、`top_p`、`n` 等。

---

### 深度案例 17：整轮回答只有 71 个字 —— Agent 把"过程碎碎念"当成了回答

**① 现象与原文**：用户问「最近有关几家 ai 公司的模型和价格有什么新消息吗」，
数据库里落库的 assistant 消息只有 71 个字符：

```
搜索结果全是导航站，没用。换几个精确的关键词。Bing爬虫给的都是导航/百科垃圾。
换Tavily试试。Tavily 有料了。挖几个页面看细节。
```

用户的原话是「它的输出明显有问题」。同一会话后面几轮也一样
（「搜到料了，而且是 2026 年的新料。挖几篇细读。」）——**一句话都没回答用户的问题**，
但 token 已经烧了 17232（4 步），后面几轮甚至到 55154。

**② 触发代码**：`agent/runner.py` 的 ReAct 循环把每一步的文本都当成回答累积：

```python
# agent/runner.py（修复前）
for chunk in stream:
    content, reasoning = extract_stream_delta(chunk)
    if content:
        content_parts.append(content)
        yield {"type": "content", "text": content}   # ← 每一步都直接流给用户
...
if not tool_buffer:                                  # 没有工具调用 = 这就是最终回答
    break
```

```python
# SilverWolf.py（修复前）：把流出去的内容原样拼成"回答"并落库
if etype == "content":
    full_content += event["text"]
```

**③ 根因**（两个独立缺陷叠加，缺一不可）：

1. **"思考"和"回答"共用一个通道**：模型在调用工具之前习惯先说一句它的打算
   （"搜索结果全是导航站，我来换关键词"）。这句话确实是模型输出的 `content`，
   但它属于**过程**，不是**结论**；而循环把每一步的 content 都累积成最终回答，
   于是过程话就变成了用户看到的答案。
2. **轮数用尽时没有收尾**：`max_steps=4` 用完时循环直接结束，
   从来没有"用已有资料作答"的最后一次调用。哪怕模型已经抓到了 DeepSeek 调价、
   GPT-6 发布这些真实资料，也没机会把它们写成结论。

两个缺陷单独出现都不算致命：只有 ① 会"回答里混着过程话"，
只有 ② 会"被截断但至少有个答案"；叠在一起就成了"整轮只有过程话"。

**④ 修复代码**：

```python
# 1) 带工具调用的那一步：把它的文本从回答里"撤回"，转进思考过程
if text.strip():
    yield {"type": "demote", "text": text}
```

```python
# 2) 轮数用尽 → 强制一次不带工具的收尾作答
if not answered:
    yield {"type": "warning", "text": "工具轮数用尽，先把已拿到的资料整理成结论"}
    messages.append({"role": "system", "content": "（系统）工具调用次数已用尽，"
                     "现在禁止再调用任何工具。请直接回答用户最初的问题：……"})
    final_stream = client.chat.completions.create(
        model=model, messages=messages, stream=True,
        stream_options={"include_usage": True}, **token_kwargs)
    for chunk in final_stream:
        content, _ = extract_stream_delta(chunk)
        if content:
            yield {"type": "content", "text": content}
```

**⑤ 原理**：

- **Agent 的输出有两种语义，必须分开**：`content` 是"给用户的话"，
  `reasoning` 是"给自己看的思考"。工具调用型模型天然会在两者之间摇摆，
  框架的责任是做那道分流，而不是把模型的每一句话都当成答案；
- **"撤回"比"先不发"更实用**：若为了判断"这句是不是最终回答"而先缓存整段文本，
  流式打字机效果就没了。发送 + 事后 `demote` 可以两者兼得
  （前端把已显示的那段挪进思考面板，用户看到的是"思考过程多了一段"）；
- **循环必须有一个"必然产生结论"的出口**：只要有 `max_steps`，
  就一定存在"预算用尽却还没答"的情况。此时正确做法是强制收尾（不带工具再问一次），
  而不是把最后一句半成品交给用户。

**⑥ 延伸与自检**：

- 单元测试用假 client 造出"第一步要工具 + 第二步给答案"的剧本，断言
  `demote` 事件存在、收尾答案存在、非流式版本也不含过程话（三条链路一起验）；
- **三处必须同步改**：SSE 层（SilverWolf.py）、前端（stores.js）、非流式（run_agent）。
  少改一处就会出现"流式正常、刷新后不对"的经典不一致；
- 真实云端复测同一条问法：回答从 71 字符变成 3269 字符（含型号、价格、来源），
  过程话则出现在「思考过程」面板里 —— 这才是"过程与结论各归各位"。

---

### 深度案例 18：一轮里搜了 10 次、抓了 8 个网页 —— Agent 没有"本轮记忆"

**① 现象**：同一轮对话的工具记录拉出来是这样的（修复前）：

```
TOOL: web_search {"query": "AI 大模型 发布 价格 最新消息", "max_results": 8}
TOOL: web_search {"query": "OpenAI GPT-5 定价 API 降价 2026年9月", "max_results": 8}
TOOL: web_search {"query": "大模型 发布 降价 2026年9月", "max_results": 10, "engine": "tavily"}
TOOL: web_search {"query": "AI model release price cut September 2026", "max_results": 10}
... 共 10 次搜索 + 8 次 fetch_page
```

prompt token 因此从 16370 涨到 50957。用户的原话：
「联网搜索数量也多，不符合正常使用的逻辑」。

**② 触发代码**：工具是完全"无状态"的 —— 它们不知道这一轮里发生过什么：

```python
# agent/tools/web_search.py（修复前）
def search(query: str, max_results: int = 5, engine: str = "auto"):
    max_results = max(1, min(int(max_results or 5), 10))   # 上限 10，模型就真敢要 10
```

**③ 根因**：

1. **模型没有"本轮记忆"**：上一句刚搜过的查询，模型换个说法又问一遍；
   对它来说这只是一次普通调用，对钱包来说是第二次计费、对上下文是第二份同样的内容；
2. **条数上限给太高**：`max_results` 允许到 10，而 8-10 条摘要对"判断有没有料"
   毫无增益，只是把 prompt 撑大；
3. **没有总量约束**：`max_steps=4` 限制的是"轮"而不是"调用" ——
   模型可以在一步里并行发 5 个工具调用，4 步就是 20 次。

**④ 修复代码**：新增 `agent/tools/_run_state.py`（本轮状态盒子）+ 三处收敛：

```python
# 1) 同一个查询只真发一次请求
cached = run["search_cache"].get(run_state.normalize_query(query))
if cached:
    return "（这个查询你刚才已经搜过，下面是同一批结果）\n" + cached["text"], cached["meta"]

# 2) 总量上限（默认 6 次搜索 / 6 次抓页）
if run["searches"] >= run["max_searches"]:
    return ("本轮搜索次数已达上限。别再调用搜索了 —— 用已经拿到的资料直接把结论写出来；"
            "确实查不到的部分，就明确说这部分没查到。", {"ok": True, "engine": "limit-reached"})

# 3) 条数收敛
DEFAULT_MAX_RESULTS, MAX_MAX_RESULTS = 3, 5
```

**⑤ 原理**：

- **给工具加"本轮记忆"是 Agent 工程里性价比最高的一招**：缓存 + 计数就能砍掉
  一半以上的重复调用，而且对模型完全透明（它只会收到"你刚搜过"的提示）；
- **限流要给"台阶"而不是"报错"**：到上限时返回的不是异常，而是一句
  "别再搜了，用已有资料给结论"，模型就会自然收敛到"作答"这个动作上
  （`ok=True` 也避免它把限流当成故障反复重试）；
- **上限要按"调用"算，不是按"轮"算**：`max_steps` 管不住并行工具调用。

**⑥ 延伸与自检**：

- 单测：同一轮里用两种写法问同一个问题（"AI 模型 价格 2026" / "ai模型价格2026"，
  归一化后等价）→ 断言只发了一次请求、计数只加 1；把上限设成 2 → 第三次返回 `limit-reached`；
- 真实复测：同一问法下工具调用从 10+8 降到 **4+3**，prompt token 从 50957 降到 28812；
- 归一化要克制：只做去空格/标点/大小写，不做分词或同义词 ——
  太激进会把"确实该重新搜"的情况也拦掉（宁可多搜一次，也别让模型拿错资料）。

---

### 深度案例 19：搜索结果前排全是导航站 —— "相关"不等于"有用"

**① 现象**：修复前那条搜索的结果（原样摘录）：

```
1. AI 工具集官网 | 1000+ AI 工具集合，国内外 AI 工具集导航大全  https://ai-bot.cn/
2. 免费 AI 工具 - 在线使用免费 AI 工具大全 | AIGC工具导航        https://www.aigc.cn/free-ai-tools
3. AskGo - 免费体验GPT,Claude,Gemini,Grok,DeepSeek               https://askgo.ai/
4. OpenAI _百度百科                                              https://baike.baidu.com/item/OpenAI/19758408
5. ChatGPT官网入口及国内使用ChatGPT教程（2026 年最新）            https://www.chatgpt-chinese.com/...
```

用户的原话：「联网搜索的网址质量差」。模型自己也被带偏，直接回了一句"全是导航站，没用"。

**② 触发代码**：`search()` 拿到什么就返回什么，只有截断没有分级：

```python
results = fn(query, max_results)          # 后端给的前 N 条，原样返回
return {"ok": True, "engine": name, "results": results, ...}
```

**③ 根因**：

1. **搜索引擎的排序目标是"被点击"，不是"信息密度"**：导航站/工具大全站为了流量
   把标题写成"XXX 官网入口 / 最新教程"，在关键词匹配上反而比官方文档更"相关"；
2. **中文 SEO 农场专门吃"2026 最新"这类时间词**，而用户的问法里恰好带时间词；
3. **我们直接信任了后端的顺序**：没有域名分级、没有同域名限流，
   于是整页可能都是同一家内容农场。

**④ 修复代码**（`agent/tools/web_search.py`）：

```python
def _rank_results(results):
    """去重 + 丢农场站 + 官方置顶 + 同域名最多 2 条。"""
    ...
    filtered = [r for r in kept if _domain_score(r["url"]) > -100]
    if filtered:                     # 关键：全被过滤时保留原结果，不能返回空
        kept = filtered
    return sorted(kept, key=lambda r: -_domain_score(r["url"]))

# 分级：导航农场 = -100（丢）/ 官方 = +5（置顶）/ 科技媒体 = +3 / 百科问答 = -5（降权）
```

**⑤ 原理**：

- **"相关度"是搜索引擎的目标函数，"可用性"才是 Agent 的目标函数**，
  两者不重合，中间必须自己加一层过滤；
- **分级要好于拉黑**：一刀切封域名（比如封掉知乎/百科）会误伤真实信息，
  所以用"丢弃 / 降权 / 置顶"三档，并保留兜底（全被过滤时返回原结果）——
  宁可给带噪音的结果，也不能让模型"什么都搜不到"；
- 规则要写在代码里并带注释，方便按需增删域名（**别硬编码进提示词**，提示词管不住排序）。

**⑥ 延伸与自检**：

- 单测：造一组混合结果（导航站 + 官方 + 两个同域名 + 百科），断言导航站被丢、
  官方排第一、重复 URL 去掉、同域名 ≤2 条、百科排最后，以及"只有垃圾站时不会返回空"；
- 真实复测的来源列表（修复后）：

```
https://openai.com/index/gpt-5-6                        ← 官方
https://api-docs.deepseek.com/zh-cn/news/news260910     ← 官方
https://api-docs.deepseek.com/zh-cn/quick_start/pricing
https://www.ithome.com/1/000/719.htm                    ← 媒体
http://finance.people.com.cn/BIG5/n1/2026/0616/...      ← 媒体
```

---

### 深度案例 20：测试脚本和浏览器"抢着写"用户配置

**① 现象**：测试收尾后，用户的 `data/ui_state.json` 里多出了测试留下的键
（同一类事故发生了两次）：

```json
{"kb_slug": "ctf-web", "...": "...", "reply_max_tokens": 32768, "reply_unlimited": true}
```

**② 触发过程**：UI 测试的收尾顺序是「脚本把原文件写回 → 关掉浏览器」，
而浏览器里的设置改动是通过 `fetch` 异步提交的：脚本写回之后，
浏览器那条"开关打开"的 POST 才落地，于是文件里留下了测试值。

**③ 根因**：**同一个文件有两个写入者**（测试脚本、浏览器页面）。
脚本只保证"自己写的那一刻"文件是对的，没有保证"之后没人再写"。
这不是产品 bug —— 浏览器本来就该能写自己的设置；问题在测试的收尾顺序。

**④ 修复**：收尾顺序固定为「先掐掉写入者，再恢复文件」：

```
1) 关闭浏览器 / 结束所有会产生 POST 的页面
2) 再执行 UI.write_text(origin, encoding="utf-8")  恢复原文件
3) 最后读回文件、断言与 origin 完全一致
```

**⑤ 原理**：**"恢复现场"必须发生在所有写入者静默之后**，
否则就是在和异步请求赛跑；"文件干不干净"也不能只看脚本自己那次写操作有没有执行。

**⑥ 延伸与自检**：

- 顺带确认过：打开页面本身**不会**改文件（加载 → 打开设置 → 等 10 秒，
  文件字节不变），所以产品侧不需要加什么"只读模式"；
- 每次动过用户配置之后，收尾都打印文件内容并肉眼确认一次
  （本次结果：`{"kb_slug": "ctf-web", "use_rag": true, "tavily_api_key": "...", "tavily_enabled": true}`）；
- 同类"恢复现场"清单：测试会话按 ID 删除、Chrome 用户数据目录删除、
  `%TEMP%\sw_*` 临时文件清空、`tmp/` 里的临时脚本删掉。

---

### 深度案例 21：在 Node 里离线编译 Vue 模板做前端静态检查

**① 现象与报错原文**：给 v2.2.0 的模板改动写离线校验脚本时，
`Vue.compile(template)` 对**一部分**组件直接抛异常，另一部分却正常：

```
compile FAIL message-bubble: Cannot read properties of undefined (reading '0')
TypeError: Cannot read properties of undefined (reading '0')
    at Object.decodeEntities (vue.global.prod.js)
    at Object.onattribend (vue.global.prod.js)
    at Qc.onerr.handleInAttrValue (vue.global.prod.js)
```

**② 触发代码**：脚本里只给了个「空壳」的 `document` 打桩，然后直接编译模板：

```js
global.document = {
    getElementById: () => null,
    createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }),
};
Vue.compile(componentTemplate);   // ← decodeEntities 在这里炸
```

**③ 根因**：`vue.global.prod.js` 是**浏览器构建**，它的模板编译器带一个 `decodeHtmlBrowser`：
把待解码的字符串塞进一个 `<div>` 的 `innerHTML`，再从 DOM 上读回
`children[0].getAttribute(...)` / `textContent` 来还原 HTML 实体（`&amp;`、`&#39;` 之类）。
打桩对象没有 `children` → `children[0]` 读到 `undefined` → 抛错。
也解释了「为什么只有一部分模板炸」：**只有文本/属性里含 HTML 实体的模板**才会走到解码分支。

**④ 修复**：把桩补成"够用的最小 DOM"——`innerHTML` 的 setter 分两条路：
属性模式（`<div foo="...">` → 解析出值再做实体解码）与文本模式（直接解码）：
（脚本是 `tmp/check_frontend.js`，一次性，用完按纪律清掉；核心是 `decodeStr()`
处理 `&#x...;` / `&#...;` / 命名实体）

**⑤ 原理**：浏览器全局构建（源码形如 `var Vue=function(){...}`）**不能用 require 当 CommonJS 模块**——
`require()` 会返回空对象（没有 `module.exports`）。正确姿势是
`global.window = global; (0, eval)(源码 + ';globalThis.Vue = Vue;')`。
同理，任何「把浏览器代码搬进 Node 跑」的场景，都要把它真正用到的 DOM 面补齐，缺哪个补哪个。

**⑥ 延伸与自检**：
- 本脚本还兼职校验图标名：把模板里所有 `<sw-icon name="...">`（含 `:name` 表达式里的
  字符串字面量）抽出来，和 `icons.js` 的注册表对账，防止写错图标名静默变空白；
  注意正则要避开 `:name=`（负向后顾 `(?<![:\w-])`），并且跳过 `=== 'local'` 这类比较值；
- **静态检查只是第一道闸**：prod 版编译器对部分语法错误不抛异常（实测坏 mustache 不报错，
  坏 `v-if=` 才抛），所以模板改动的最终验收仍然是真实浏览器打开页面。

### 深度案例 22：CDP 截图两种"怪象"——黑图 / 只拍到一角 / 超时秒退

**① 现象与报错原文**：v2.2.1 视觉精修复用 CDP 截图脚本，出现三类怪象：
（a）元素截图拍出来是**纯黑图**；（b）整页截图只拍到**一小块**（比如设置弹窗只剩左上角"设置"两个字）；
（c）`Page.captureScreenshot` 直接挂住，最后报
`websocket._exceptions.WebSocketTimeoutException: Connection timed out`。
诡异的是同一条命令前几分钟还好用。

**② 触发代码**：脚本把它拆成了两个独立进程的调用——

```
python tmp/cdp.py metrics 1280 860 1.5   # 进程 A：设视口
python tmp/cdp.py shot out.png           # 进程 B：截图（A 已退出）
```

**③ 根因**：两个独立问题叠在一起：
- **视口覆盖是"会话级"的**：`Emulation.setDeviceMetricsOverride` 只在**当前 CDP 连接**
  内生效；脚本进程结束、WebSocket 关闭，覆盖立刻被清掉，视口回退成 Edge 窗口的真实尺寸。
  于是进程 B 截图时页面已经是小视口：元素 rect 的坐标（如 x=986）落在小视口之外，
  `clip` 裁到了视口外的空白区 → 黑图；或者整页截图自然只剩窗口那一小块。
- **后台标签页不出帧**：`Page.captureScreenshot` 依赖合成器出帧，而目标页若是
  **后台标签页**，帧会被浏览器节流（与 rAF 被节流同源）——命令就一直挂着直到超时。
  本次触发场景：Edge 冷启动时弹了"同步确认"页并占据前台，应用页沦为后台页。

**④ 修复**：把"设视口 + 截图"合并进**同一次连接**，并在截图前把目标页提到前台：

```python
def prepare_view(self):
    self.call("Page.bringToFront")                       # 后台页不出帧，先提前台
    self.call("Emulation.setDeviceMetricsOverride", {
        "width": 1280, "height": 860, "deviceScaleFactor": 1.5, "mobile": False})
    time.sleep(0.4)                                      # 等重排/重绘
# 同一连接里紧接着 captureScreenshot（含 clip 的元素截图同理）
```

**⑤ 原理**：CDP 的 Emulation / Network 等域设置是**会话级（per-connection）**的，
进程退出即清理——和"设置保存在浏览器里"的直觉相反；截图、输入这类命令依赖渲染管线，
后台页会被节流。两个行为在文档里都只有一句话，踩了才知道疼。

**⑥ 延伸与自检**：
- 一次性脚本**不要跨进程依赖"上一条命令留下的状态"**，凡是"改状态 + 用状态"的动作
  一律合并在一个进程里；
- 截图超时先查目标页是不是前台页（`bringToFront` 成本极低，干脆每次截图都带上）；
- 元素位置对、截图却不对 → 先怀疑视口覆盖已失效，而不是怀疑选择器；
- 顺带一坑：`Runtime.evaluate` 的返回体若没有 `result.value` 而只有 `exceptionDetails`，
  说明**表达式本身抛了**（本次是 Python 里 f-string 与非 f-string 拼接时 `}}` 没被转义，
  拼出的 JS 多了一个花括号）——脚本报 KeyError('value') 时先打印返回体看异常。

---

### 深度案例 23：跨会话失忆 —— remember / recall / forget 长期记忆（v2.3.0）

**① 现象与问题**：银狼只有两本账——SQLite 会话记录（删了就没）与上下文压缩摘要
（`middleware/context.py`，只服务当前会话）。换个会话就是重开一局：用户在会话 A 里
说"项目用 FastAPI"，会话 B 问"我们技术栈是什么"，她答不上来，只能装作记得或反复追问。

**② 触发场景**：多轮聊天后发现——每次新会话模型对用户的偏好、拍板过的约定全部清零，
连"上次改到哪一步"都不记得，用户体验断裂。

**③ 根因**：记忆没有"跨会话的持久层"。"会话记忆"存的是聊天原文不是事实；
"上下文压缩摘要"存在当前会话行里，别的会话读不到。

**④ 设计**（三工具 + 自动注入 + 设置页）：

```python
# agent/tools/memory.py —— 存储 data/memory.json，与 API 层共用读写
def add_memory(content, tags="", source="agent"): ...      # remember：写
def search_memories(query, limit=5): ...                   # recall：按相关度召回
def delete_memory(mem_id): ...                             # forget：按 id 删
```

- **recall 为什么不用向量检索**：记忆条目通常一两句话，短文本用"中文双字 bigram +
  英文单词"命中打分就够了，零依赖、够快够准；真要语义检索项目里已有 RAG 那条链路，
  不必在这个轻量工具上加重磅依赖。
- **相关记忆自动注入**（`SilverWolf.py _prepare_chat_context`）：每次提问按相关度匹配
  最近 3 条注入 system（`messages.insert(2, ...)` 紧跟 system，防被上下文裁剪裁掉）——
  普通对话与 Agent 对话都生效，即使模型忘了主动 recall 也能想起来。
- **设置 → 记忆标签页**：查看 / 手动存档 / 单条删除 / 清空全部；新增像素 brain 图标；
  `SYSTEM_PROMPT` 补充使用守则（该记的记、该查的查、该删的删）。

**⑤ 原理与踩坑**：第一版 recall 用"bigram 重叠率（交集/并集）"打分，实测
`search_memories("小明喜欢什么语言")` 召回不了"委托人叫小明，写代码偏好 Python"——
查询里的"喜欢 / 什么 / 语言"与记忆几乎无交集，纯重叠率把"小明"这个唯一命中稀释成
低分。改成 **"查询词在内容里命中几个（占比）+ 长词命中加一点奖励"** 后召回正常。
规律：中文没有空格分词，检索打分要偏向"查询里的词在文档里出现了几个"，而不是
"两边字符集重合的比例"。

**⑥ 延伸与自检**：
- 自检：`pytest tests/test_smoke.py::test_memory_remember_recall_forget`；手动——设置 →
  记忆存档一条 → 新会话问相关话题能答上。
- 坑：`data/memory.json` 是明文，别把密钥/敏感信息当记忆存；单条记忆上限 2000 字符，
  防模型把整段对话怼进来。
- 后续可升级：把记忆条目也喂进 RAG 向量库做语义检索（需要时再上，不必现在）。

---

### 深度案例 24：Agent 只会聊不会干活 —— 工作区文件沙箱（v2.3.0）

**① 现象与问题**：Agent 唯一能落盘的是 `rag_write_file`
（`knowledge_bases/<kb>/files/` 文本文件），没有"给用户产出普通文件"的能力——
整理好的笔记、草稿、计划只能写在回复里，刷新就没了。

**② 设计背景**：直接放开文件系统很危险——模型被诱导写任意路径（`../`、绝对路径、
符号链接逃逸）就是灾难。所以给一个**受限工作区** `data/workspace/`（已 gitignore），
四个工具：`fs_list / fs_read / fs_write / fs_delete`，全部经过同一个沙箱门禁。

**③ 关键实现**（路径沙箱的标准做法）：

```python
def _safe_resolve(rel_path: str) -> Path:
    raw = (rel_path or "").strip().replace("\\", "/")
    if not raw or raw in (".", "./"):
        return WORKSPACE_DIR.resolve()        # 空路径 = 根（仅列表合法）
    if raw.startswith("/") or ":" in raw.split("/")[0]:
        raise ValueError("不允许使用绝对路径")   # 拦 C:/ 盘符
    target = (WORKSPACE_DIR / raw).resolve(strict=False)
    if not target.is_relative_to(WORKSPACE_DIR.resolve()):
        raise ValueError("路径逃逸出工作区")     # resolve 展开 .. 与符号链接后校验
    return target
```

写/删动作全部 `log_change()` 留痕；`fs_delete` 只允许删单文件或空目录，删根目录与
非空目录被硬拦。

**④ 踩坑**：空路径既要当"列表根目录"（`fs_list` 默认参数）又要防"删根"——在
`fs_delete` 里单独判断 `target == WORKSPACE_DIR.resolve()` 才拒绝，不能把空路径
直接当非法。

**⑤ 原理**：`resolve(strict=False)` 会**展开 `..` 和符号链接**，展开后仍落在根内才算
合法——这是路径沙箱的关键，比"字符串里有没有 `..`"这种检查可靠得多（符号链接
可以绕过字符串检查）。

**⑥ 延伸与自检**：
- 自检：`test_workspace_sandbox_escape_blocked`（4 种逃逸全拦）+ `test_workspace_write_read_delete`。
- 与知识库的边界：workspace 是"给用户的产出区"，知识库是"RAG 语料区"，职责不同互不越界。
- 配合审批：`fs_write / fs_delete` 标记 `approval_required`，用户确认才执行（见深度案例 25）。

---

### 深度案例 25：高危工具审批 —— SSE tool_approval + threading.Event 双端挂起（v2.3.0）

**① 现象与问题**：所有工具**无条件执行**——模型写文件、删文件、带密钥调外部 API
全自动，权限边界太粗。一边想给 Agent 更多能力（写工作区、改自定义工具），一边又
怕它手滑删错东西。需求是 Claude Code 式的"高危操作需确认"。

**② 设计**：
- 工具定义里加 `approval_required: True` 标记（`fs_write / fs_delete / tools_upsert / tools_delete`）；
- runner 在执行前 `yield {"type": "tool_approval", "request_id", "name", "args"}`；
- 前端弹授权窗，用户点「允许一次 / 拒绝」→ `POST /api/agent/approval`；
- 后端等待线程被唤醒，超时（60 秒）自动按拒绝处理。
- **三端一致**：SSE 层、前端 stores.js、非流式 `run_agent`（复用 `run_agent_stream`）同步改。

**③ 关键实现**：

```python
# runner.py：请求-响应等待，threading.Event 让"另一个 HTTP 请求"能唤醒生成器
entry = {"approved": False, "event": threading.Event()}
APPROVAL_REGISTRY[request_id] = entry
yield {"type": "tool_approval", "request_id": request_id, "name": c["name"], "args": c["args"]}
waited = entry["event"].wait(APPROVAL_TIMEOUT)          # 阻塞等前端回传
approved = entry["approved"] if waited else False        # 超时 → 拒绝
APPROVAL_REGISTRY.pop(request_id, None)                  # 无论结果，注册表必须清理
```

```js
// stores.js：读到 tool_approval 就挂起流式读取，弹窗按钮 resolve
if (parsed.tool_approval) {
    this.pendingApproval = { requestId, name, args, resolve: null };
    await new Promise((resolve) => {
        this.pendingApproval.resolve = resolve;
        setTimeout(resolve, 60000);                      // 前端 60 秒兜底，双保险
    });
    this.pendingApproval = null;
}
```

拒绝路径：不执行工具，把"被用户拒绝"当成一次 `tool_result`（ok=False, denied）回给
模型，让它换路或向用户说明。

**④ 踩坑**：
- 流式生成器跑在 Starlette 的**线程池**里（`iterate_in_threadpool`），等待期间会阻塞
  生成器线程——所以必须用 `threading.Event` 事件唤醒，不能轮询注册表。
- 用户点"停止生成"时，**必须同时 resolve 审批 Promise**，否则审批弹窗卡 60 秒、流程停不下来。
- 注册表条目无论成功/拒绝/超时都要 `pop` 清理，否则内存泄漏；审批接口收到已清理的
  request_id 返回 `ok=False`，前端静默忽略即可。

**⑤ 原理**：`threading.Event` 实现了**跨 HTTP 请求唤醒**：审批接口（另一个请求）里
`entry["event"].set()`，生成器线程的 `.wait()` 返回——两个请求通过进程内注册表协作，
不用引入消息队列。超时是双保险：后端 60 秒自动拒 + 前端 60 秒自动 resolve，两边都不会卡死。

**⑥ 延伸与自检**：
- `pytest tests/test_smoke.py -k approval`：允许→执行→写盘 / 拒绝→跳过→不写盘→注册表清理，两条链路。
- 审批粒度目前是"每次弹窗"；后续可加"本次会话信任该工具"的记忆化选项。

---

### 深度案例 26：任务规划 —— update_plan 与"三端一致"的流式协议扩展（v2.3.0）

**① 现象与问题**：复杂任务是**黑箱**——模型在 `max_steps=4` 里走一步看一步，用户
不知道它在干嘛，做到一半还容易跑偏最初目标（"搜索结果全是导航站，没用"这种过程话
都差点当回答交出去）。需求是 Claude 式的**计划清单 + 实时进度**。

**② 设计**：
- `update_plan / show_plan` 工具，计划存在本轮运行状态 `run_state["plan"]`（生成器帧持有）；
- runner 在工具执行后检查 `meta.plan` 非空 → 转发 SSE `plan` 事件；
- 前端 `botMsg.plan = parsed.plan`，消息气泡渲染"任务清单"进度条（pending / running / done）；
- 非流式 `run_agent` 把 plan 并入 meta —— **SSE 层 / stores.js / run_agent 三处同步**，
  正是 AGENTS.md 流式协议那条"三端一致"铁律的又一次应用。

**③ 关键实现**：

```python
# agent/tools/plan.py —— 状态机 + 清洗
VALID_STATUS = {"pending", "running", "done"}
# 非法状态一律归一化到 pending，防模型乱传值污染 UI
```

```python
# runner.py：工具结果后转发计划事件
if result.get("meta", {}).get("plan") is not None:
    yield {"type": "plan", "steps": result["meta"]["plan"]}
```

**④ 踩坑**：计划如果只存在工具返回值里，**下一次工具调用就没了**——必须存
`run_state["plan"]`（生成器帧持有，每步重新 bind），而不是只发一个事件给前端；
前端 `plan` 事件可能到多次，直接覆盖 `botMsg.plan` 即可（幂等，不用合并）。

**⑤ 原理**：本质是给 Agent 加**显式工作记忆 + 进度投影**——把模型内心步骤外化成 UI
状态。它不改变模型能力，只让过程可见，用户能判断"值不值得等"。

**⑥ 延伸与自检**：
- `test_plan_state_machine`：全量计划 / 单步更新 / 非法状态归一化。
- 与 `max_steps` 收尾、token 硬闸（深度案例 27）天然配合：计划清单 + 预算闸门 = 可预期的多步执行。

---

### 深度案例 27：钱在燃烧 —— 单轮 token 硬闸 + 前端停止生成（v2.3.0）

**① 现象与问题**：Agent 每调一次工具就**重发完整上下文**，多轮下来累计 token 翻几倍
（深度案例 11 就是上下文膨胀 × 多轮循环）；模型还能"无止境地搜下去"（受 `_run_state`
上限约束后仍有 6 次搜索 + 6 次抓页的余地）；而且用户**想停却停不了**——前端没有
取消机制，只能干等，白烧 token。

**② 设计**（两条腿：预算闸门 + 用户中断）：
- **后端硬闸**：`max_total_tokens` 参数（默认 200K），累计超过就停止再调工具、
  warning 提示、强制走既有收尾作答——复用深度案例里"max_steps 用尽强制收尾"的路径。
- **前端中断**：`AbortController` 取消流式请求；发送键生成中变红色「停止」键；
  已生成内容保留、标注"已停止生成"。

**③ 关键实现**：

```python
# runner.py 步骤 4.5：还有工具调用但已超预算 → 停止，走收尾
total_now = tracker.summary().get("total_tokens", 0)
if max_total_tokens and total_now > max_total_tokens:
    yield {"type": "warning", "text": f"累计 token 已达上限（>{max_total_tokens}）…"}
    answered = False
    break          # answered=False → 进入第 6 步强制收尾作答
```

```js
// stores.js：abort 后不能当普通报错处理
catch (err) {
    if (err && err.name === 'AbortError') {
        botMsg.content = botMsg.content || '（已停止生成）';
        botMsg.stopped = true;      // 保留已生成的部分，不红屏
    } else { /* 普通报错 */ }
}
```

**④ 踩坑**：
- 流式响应用 `reader.read()`，abort 会抛 `AbortError`，**必须单独 catch**，否则当普通
  报错把整条消息染红。
- 停止后 `sending` 要复位、`abortController` 置 null；同时要**解除挂起的审批 Promise**
  （见深度案例 25），否则停止生成后审批弹窗还卡着。
- 硬闸只在该轮**还想调工具**时触发；如果模型这轮直接给文本，就不拦——别把正常回答误杀。

**⑤ 原理**：成本控制两条腿缺一不可——预算闸门兜底（防止一次任务烧穿额度，属于
"后端可靠"），用户中断提体验（属于"前端可控"）。`answered=False + break` 复用
既有收尾逻辑，是把新闸门接进旧流程的关键，少写了半套"强制作答"。

**⑥ 延伸与自检**：硬闸触发路径可加一条 pytest（构造 usage 超限的假流）；后续可在
设置面板暴露 `max_total_tokens` 让用户调。

---

### 深度案例 28：工具层与工程补全 —— rag_import_url / 安全计算器 / pytest 冒烟 / 会话导出（v2.3.0）

**① 现象与问题**：四件小事各自有痛点——好文章想入库得手动复制粘贴；模型心算不可靠
（大数、小数、概率经常翻车）；仓库**零自动化测试**，全靠 AGENTS.md 手测清单；
聊天记录没有导出途径，想存档/分享只能截图。

**② 实现（每件都是低成本拼装）**：

- **rag_import_url**：`fetch_page` 抓正文 → 标题清洗成文件名 → `save_kb_file` 入库
  自动向量化。**复用既有积木**，一行注册表都没改（工具自动发现）。一轮最多导入 3 页，
  动作留痕。

```python
result = fetch_page(url, max_chars=RAG_IMPORT_MAX_CHARS)   # 复用网页正文抽取
saved = save_kb_file(kb_slug, filename, content)           # 复用知识库入库
```

- **calculate（安全计算器）**：`ast.parse` 表达式 → **白名单递归求值**——只放行
  四则 / 幂 / 比较 / 逻辑与有限几个数学函数；属性访问、下标、赋值、导入全部拒绝。
  "能算数"和"能执行代码"之间划一条明确的线，这是它和"代码执行沙箱"的本质区别。

```python
def _eval_node(node):
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS: ...
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id not in _SAFE_FUNCS: raise ValueError(...)
    raise ValueError(f"不支持的表达式形式：{type(node).__name__}")
```

- **pytest 冒烟**：`tests/test_smoke.py` 12 例——工具注册表 / 审批标记 / 记忆增删查 /
  沙箱逃逸 / 计算器安全 / 计划状态机 / 审批允许·拒绝双链路（假客户端）/ SSE 归一化。
  fixture 自建自清，绝不碰用户数据（延续深度案例 17 / 20 的测试纪律）。

- **会话导出**：`GET /api/sessions/{id}/export` 拼 Markdown（委托人 / 银狼（模型名）
  分节）→ `Response` 带 `Content-Disposition` 触发下载；前端侧边栏加导出按钮 + 像素
  download 图标。

**③ 踩坑**：
- 计算器工具定义里的 description 曾写成**跨行普通字符串**（不是多段拼接），直接
  `SyntaxError: unterminated string literal`，卡了一次导入——模板字符串/描述里换行
  要写成多段 `"…"` 拼接，教训在深度案例 15 也踩过同类（没编译就往下走）。
- 导出接口要测 404 分支（会话不存在时返回 404 而不是 500）。

**④ 原理**：这四个改动都在"**最小成本放大 Agent 可用性**"这个共同思路上——能复用
的积木（fetch_page / save_kb_file / 既有收尾逻辑）绝不重写；新增能力一律先设边界
（沙箱 / 白名单 / 次数限制 / 留痕）；测试先行保证可回滚。

**⑤ 延伸**：计算器可扩展函数白名单；导出可加 JSON / 对话脚本格式；pytest 冒烟可接
CI（AGENTS.md 允许无头测试用于 CI，但必须显式说明）。
