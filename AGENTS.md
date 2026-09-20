# AGENTS.md — 银狼工作台编码守则

> 啧，来新人了？……行吧，先说好规矩，卡关之前先看这里。
> 本文件是给所有在这个仓库里改代码的 AI/协作者看的「游戏规则」。
> 不遵守的后果：代码回滚不了、页面白屏、银狼人设崩坏——三条都是事故。

---

## 0. 项目速览

Silver Wolf（银狼）是一个「工作台式」AI 聊天助手：

- 后端：FastAPI（`main.py` 入口，`SilverWolf.py` 路由）+ SQLite（会话记忆）+
  ChromaDB（RAG 向量库）+ OpenAI SDK（兼容 Ollama 与任意 OpenAI 兼容接口）
- 前端：Vue 3 **本地运行时**（`frontend/vendor/vue.global.prod.js`），无构建步骤、零 CDN。
  改动前端后**不需要**跑构建，但必须让浏览器强刷（资源 URL 带 `?v=` 版本号）
- 模型：Provider（提供商）体系，配置在 `data/providers.json`（已 gitignore，含 API Key，只存本机）
- 人设：银狼（星核猎手黑客），系统提示词在 `config_data.py` 的 `SYSTEM_PROMPT`

---

## 1. 铁律（违反 = 事故）

1. **每完成一次改动，必须创建一个 git commit。**
   - 一个逻辑改动 = 一个提交；做完就提交，别攒着。
   - 提交信息用 Conventional Commits（`feat:` / `fix:` / `refactor:` / `chore:` / `docs:` / `test:`），
     中文描述，允许带一点银狼味，但必须能让人一眼看懂改了什么。
   - 目标：任何一步出问题，都能 `git revert` 回滚到上一个可用版本。
2. **每次改动都必须测试，测试通过前不许交付。**
   - 后端改动：语法检查 + 模块导入 + 相关 API 冒烟测试（见 §3）。
   - 前端改动：`node --check` + 模板编译/渲染验证 + 交互冒烟（见 §3）。
   - 聊天相关改动：必须真实跑一次流式接口（本地 Ollama 或云端），确认逐字输出正常。
3. **交付前必须同步完善文档：`CHANGELOG.md`（新增版本条目）、`README.md`（必要时）。**
   - 功能/修复/破坏性变更都要写进 Changelog；使用方式变了就改 README / `docs/USER_MANUAL.md`。
4. **所有面向用户的文案、提示词、界面文本必须符合银狼人设。**
   - 风格：短句、慵懒毒舌、黑客+游戏双线比喻（BUG=漏洞、入侵=卡BUG、反制=开挂），
     偶尔傲娇（“才、才不是特意帮你”）。不要写成长篇大论的客服腔。
   - 技术准确性优先，吐槽是佐料不是主菜。
5. **不要破坏「离线优先」承诺。** 前端不得引入 CDN；后端依赖一律锁版本；RAG embedding 本地化。
6. **不要把版权素材提交进仓库。** 角色立绘、背景图等有版权风险的图片一律不入库
   （用户可通过「设置 → 外观」上传自己的背景图；仓库内只用占位/渐变）。
7. **项目无关的临时文件一律放 `tmp/`。** 简历、文档、脚本、下载缓存等与「项目自身功能」
   无关的东西不得散落在仓库根目录或其他目录；需要保留的放项目根 `tmp/`（已 gitignore），
   用完即删。
8. **每次收尾/结束对话前清空垃圾。** 临时脚本、构建中间件、渲染产物、日志、测试残留等
   全部清理干净，保持工作区与 `git status` 干净后再交付或结束。
9. **改动按需，但必须审查。** 不必要的地方可以不改；但只要动了，就必须**核对是否真的生效**：
   重启服务、刷新缓存、调用接口/打开页面看到实际效果，而不是"代码写完就算改完"。
   用户反馈"改了没生效"时，第一件事是查缓存、热重载、进程是否重启、配置是否被覆盖。
10. **注释要让新人看得懂。** 新增模块必须有中文头注释：这个文件干什么、在哪些地方被调用、
    核心原理/流程是什么；关键函数写清参数与返回值；踩坑处写明"为什么这么做"。
11. **每一次关键改动都要沉淀到 `changecode.md`，而且要写透。** 不只是报错——新功能、
    新工具、流式协议扩展这类"设计型改动"同样是项目资产。**报错/修复类**按下面 6 部分写
    （缺哪部分要说明原因）：
    1. **现象与报错原文**：把终端/浏览器里的原始堆栈或日志行贴出来（别转述成"报错了"）；
    2. **触发代码**：能复现问题的最小代码片段，标明文件与位置；
    3. **根因**：为什么这段代码会出错（语言/框架/协议/环境层面的解释）；
    4. **修复代码**：改完之后的片段，写清关键差异；
    5. **原理**：这类问题的通用规律，下次如何一眼识别；
    6. **延伸与自检**：验证手段（怎么确认修好了）、以及容易一起踩的坑。
    **功能/设计类**（新增记忆、审批、计划工具等）按「①现象与问题 → ②设计背景 →
    ③关键实现 → ④踩坑 → ⑤原理 → ⑥延伸与自检」写，格式参考 `changecode.md`
    深度案例 23~28（长期记忆 / 工作区沙箱 / 工具审批 / 任务规划 / token 硬闸 / 工程补全）。
    学习材料宁可写长，也不要只写"改了某行就好了"；这套档案是回滚、交接、复盘翻旧账的底气。

---

## 2. 标准改动流程

```
读代码 → 想清楚 → 改 → 测试 → 提交 → 更新文档 → 复盘沉淀（changecode）
```

1. **读**：先看相关模块和调用链，别凭感觉改。
2. **改**：小步改，一个逻辑一次提交的粒度。
3. **测**：按 §3 的清单跑，别跳过。
4. **提交**：`git add` 相关文件 → commit（信息清晰可回滚）。
5. **文档**：改动涉及用户可见行为时，更新 CHANGELOG / README，并**单独提交**文档改动。
6. **复盘**：关键改动 / 踩坑按铁律 11 补进 `changecode.md`（深度案例格式），单独提交。

> 顺序讲究：先代码后文档。文档提交可以和代码提交分开，回滚时互不牵连；
> changecode 复盘同理，独立提交最干净。

---

## 3. 测试清单

### 后端

```bat
:: 语法 + 导入（会顺带初始化 providers.json，无副作用）
.venv\Scripts\python.exe -c "import main; import SilverWolf; import provider_config; from agent import runner, tools; from middleware import context, token_usage; print('OK')"

:: API 冒烟（服务启动后）
:: GET  /api/health           服务/版本/Ollama 状态
:: GET  /api/config           提供商/默认值
:: GET  /api/providers        提供商列表（Key 必须脱敏）
:: POST /api/providers        新增/更新提供商（校验 base_url/model/kind）
:: POST /api/chat/stream      流式输出（重点：逐字推送、meta 带 session_id/model、最后 [DONE]）
```

流式输出三大雷区（必须验证）：
- 厂商会发 `choices` 为空的帧 → `_extract_stream_delta` 必须返回 `(None, None)`，不能崩。
- `delta.content` 可能是 `list`（不是 str）→ `_normalize_delta_text` 要能拼回字符串。
- 推理模型 `reasoning_content` → 前端要能收到并展示「思考过程」。

### 前端

```bat
node --check frontend\src\api.js
node --check frontend\src\stores.js
node --check frontend\src\app.js
```

- 模板改动：用本地 Vue 3 运行时编译全部组件模板（`Vue.compile`）做静态检查，
  再用**真实浏览器**（Edge / Chrome）打开页面验证不白屏、关键元素存在。
- 交互改动（弹窗/下拉/表单/附件上传）：**必须用真实浏览器实际操作**——打开 Edge / Chrome
  访问页面，真实点击、输入、上传文件，别只靠眼睛看代码。
  > ⛔ 禁止用无头浏览器充当 UI 测试，也没有「无头兜底」这回事：无头只能用于
  > CI 且必须显式说明；当前环境没有真实浏览器自动化能力时，停下来请用户手动验证，
  > 或者申请真实浏览器控制能力，不得用无头浏览器冒充“已测试”。
- 改 `stores.js` 的 `addMessage` 相关逻辑时：**必须返回响应式代理**
  （`this.messages[this.messages.length - 1]`），直接改 push 前的原始对象 = 流式不显示的老 Bug。

### 交付前

- 服务能起、首页能开、本地模型能聊、云端模型能聊、设置能改。
- `git status` 干净（或只剩你故意留下的东西）。
- CHANGELOG 有本次版本条目，README 与现状一致。
- **测试数据已清理**：测试产生的会话、上传的测试附件（`data/uploads/`）、临时文件全部删干净；
  用户自己的数据（会话、附件、配置、数据库）一律不动。
- ⛔ **测试不许覆盖/删除用户已有数据**：`data/avatars/`（用户头像）、`data/providers.json`、
  `data/ui_state.json`、`chat_history.db` 等属于用户数据。若测试确实需要动它们
  （如测头像上传），必须先备份原文件，测完**原样恢复**，绝不把用户数据当测试残留清理。

---

## 4. 架构要点（改哪里看哪里）

| 文件 | 职责 | 注意事项 |
|------|------|----------|
| `main.py` | FastAPI 入口，挂载静态/路由 | 别把业务逻辑塞进来 |
| `SilverWolf.py` | 聊天/会话/流式/提供商 API | 流式解析用 `_extract_stream_delta`，别绕开 |
| `agent/runner.py` | Agent 多步循环（模型 → 工具 → 模型） | 工具调用参数（轮数/长度上限）由这里透传 |
| `agent/tools/` | **工具仓库：一个功能一个文件，放进目录即自动注册** | 新增工具 = 新建 `xxx.py` 并导出 `TOOLS` 字典，别去改注册表 |
| `agent/tools/_run_state.py` | 本轮工具状态（搜索/抓页计数与缓存） | 下划线开头不注册；每步要重新 `bind()`，原因见文件头注释 |
| `middleware/context.py` | 上下文裁剪 / 压缩 / 截断 | 普通对话与 Agent 对话都要过一遍 |
| `middleware/token_usage.py` | token 用量解析与估算 | 厂商不回 usage 时必须能估算 |
| `middleware/stream_utils.py` | 各家流式增量归一化 | 空 choices、content 为 list、reasoning 都在这处理 |
| `provider_config.py` | Provider 配置读写（`data/providers.json`） | **API Key 明文存储**，对外一律 `_public_provider` 脱敏 |
| `config_data.py` | 常量、版本号、系统提示词 | 改人设 = 改 `SYSTEM_PROMPT` |
| `file_history_store.py` | SQLite 会话/消息 | 别动表结构除非万不得已 |
| `rag.py` / `vector_stores.py` | RAG 检索与向量化 | embedding 离线优先，别触发联网下载 |
| `frontend/src/stores.js` | 全局状态（会话/聊天/提供商/知识库） | 响应式代理陷阱见 §3 |
| `frontend/src/app.js` | 全部 Vue 组件 | 模板字符串，无构建，注意转义 |
| `frontend/src/api.js` | fetch 封装 | 新接口要在这里登记 |

### 分层规则（v2.1.0 起）

```
middleware/  ← 横切能力（上下文、token、流式归一化）：普通对话与 Agent 都用
agent/       ← Agent 专属：循环 + tools/（模型能主动调用的工具）
根目录        ← 主体功能与入口：main.py、SilverWolf.py、数据库、RAG、附件、配置
```

- 依赖方向只能是 `上层 → 下层`（`SilverWolf.py` → `agent/` → `middleware/`），不许反向 import；
- 判断新代码放哪：**只有 Agent 用** → `agent/`；**模型能主动调用** → `agent/tools/`；
  **每个请求都要过一遍的基础设施** → `middleware/`；**业务主体** → 根目录对应模块。

### 提供商（Provider）数据模型

```json
{
  "id": "local",
  "name": "本地 Ollama",
  "kind": "local",
  "base_url": "http://localhost:11434/v1",
  "api_key": "",
  "model": "deepseek-r1:8b",
  "models": ["deepseek-r1:8b"],
  "is_default": true
}
```

- `model`：默认模型（聊天兜底）。
- `models`：用户在设置里**显式添加**的模型列表，输入框下拉只显示这些，初始为空。
- 本地 Ollama 可自动拉模型列表，但**不要**把云端聚合接口的几百个模型灌进下拉框。

### 流式协议

`POST /api/chat/stream`，SSE 格式：`data: {json}\n\n`，内容增量带 `content`（推理带 `reasoning`），
结束前发 meta（`session_id / rag_used / rag_sources / provider_id / model / fallback_warning`），
最后 `data: [DONE]`。前端逐行解析 `data: ` 前缀，别用 `JSON.parse` 直接吃整段。

Agent 模式还会发 `tool_call` / `tool_result` / `warning` / `usage`，
以及 **`demote`**：带工具调用的那一步，模型输出的其实是"我准备怎么搜"的过程话，
`demote` 把它从回答里撤回、转进思考过程。**改流式协议时，SSE 层、前端 stores.js、
非流式 `run_agent` 三处要同步改**，否则三端表现会不一致（这是踩过的坑）。

---

## 5. 银狼人设守则（写提示词/文案时）

> 先分清两个东西，别搞混：
> - **人设的权威定义**：`config_data.py` 里的 `SYSTEM_PROMPT`——这是运行时发给模型的系统提示词，
>   银狼的角色、性格、说话风格都以它为准；改人设 = 改那里。
> - **本节的用途**：只是给「写界面文案 / 提示词 / 提交说明」的 AI 的风格参考，
>   保证所有用户可见文字都在银狼的调性上。本节不是人设本体，也不替代 SYSTEM_PROMPT。

- 语气：慵懒、冷淡、毒舌、傲娇；短句为主，动辄“啧”。
- 比喻：世界=游戏、漏洞=BUG、入侵=卡BUG、反制=开挂；技术讲解点到为止。
- 内容：技术必须准确，可以吐槽但不许糊弄；能三句说清绝不说十句。
- 关系：把使用者当“委托人/队友”，偶尔 cue 卡芙卡、刃、艾利欧。
- 反例：一本正经的客服话术、长篇说明书腔、无意义的谄媚——都不行。

---

## 6. 完工检查（Done Checklist）

- [ ] 每个逻辑改动都有独立 commit，信息清晰
- [ ] 后端导入/语法 OK，相关 API 冒烟通过
- [ ] 前端语法/渲染/交互验证通过
- [ ] 流式输出真实跑通（本地或云端）
- [ ] 测试数据已清理（测试会话 / data/uploads 测试附件 / 临时文件），用户数据未受影响
- [ ] 没有覆盖/删除用户的头像、提供商配置、界面状态等 data/ 数据
- [ ] 项目无关文件已归入 `tmp/`，垃圾已清空，工作区干净
- [ ] 改动已核对真的生效（重启/清缓存/实际调用或页面验证），不是只看代码
- [ ] 新代码有中文注释（作用 / 调用位置 / 原理），踩坑点已说明
- [ ] 本轮关键改动与踩坑已沉淀到 `changecode.md`（报错类 6 部分 / 功能类深度案例格式）
- [ ] CHANGELOG / README 已同步
- [ ] 银狼人设没有跑偏
- [ ] 没有把 `data/providers.json`、`.env`、`*.db` 提交进 git
