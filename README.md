# Silver Wolf - 星核猎手 AI 助手

一个基于 Vue 3（本地运行时）和 FastAPI 的 AI 聊天助手原型，支持本地模型（Ollama）与
云端模型（任意 OpenAI 兼容接口），带会话记录、RAG 本地知识库和文件上传分析。

> 说明：本项目的角色「银狼」来自游戏《崩坏：星穹铁道》。仓库不包含任何官方素材，
> 默认无背景图、不预置任何模型，均可由用户自行添加。项目处于原型阶段，功能有限。

## 功能（v2.3.0）

- 界面：**像素街机风**（v2.2.0 起，v2.2.1 精修）—— 自绘像素图标（无 emoji）、
  街机 HUD 切角面板与扫描线，配色为银狼的蓝紫基调；开关 / 滑块等控件保持圆润精致；
  零新增依赖，仍是纯本地运行时

- Agent 模式：模型可自动调用工具完成多步任务 —— 联网搜索、RAG 检索、
  读写知识库文件、增删改自定义工具（声明式 HTTP 工具，不执行任意代码）
- 工作区文件工具：Agent 可在受限沙箱（`data/workspace/`）里读写文件、整理产出
- 长期记忆（跨会话）：Agent 可主动 `remember` 存档偏好与约定，新会话自动按提问
  召回相关记忆；设置 →「记忆」标签页可查看 / 删除 / 清空（存于 `data/memory.json`）
- 任务规划：复杂任务 Agent 先 `update_plan` 列步骤，回答里实时渲染「任务清单」进度条
- 高危工具审批：写文件 / 删文件 / 改自定义工具执行前会弹窗让你「允许一次 / 拒绝」，
  60 秒没点默认拒绝
- 停止生成：Agent 长任务生成中发送按钮变红色「停止」键，随时打断不白烧 token
- 计算器：`calculate` 工具做安全数值计算（AST 白名单，零风险）
- 会话导出：侧边栏每个会话可一键导出 Markdown 存档 / 分享
- 模型提供商管理：在设置中添加本地 Ollama 或任意 OpenAI 兼容接口（OpenAI、DeepSeek、
  阿里云百炼等），填写接口地址、模型名和 API Key 即可使用；下拉框只显示手动添加的模型
- 输入框切换模型：发送消息前可随时切换当前提供商和模型
- 文件上传：支持把本地文件（文本、代码、文档、压缩包、可执行文件等）作为消息附件
  一起发送，程序只读取内容或元信息（文本内容、压缩包目录、文件哈希等），不会执行文件
- 会话记忆：对话保存在本地 SQLite，删除会话时自动清理该会话上传的附件
- RAG 知识库：上传文档后离线向量化，提问时可自动检索相关内容
- Markdown 回复：回答以 Markdown 排版展示（标题、列表、代码块等）
- 外观自定义：主题色预设、自定义主色、背景图和头像（头像保存在服务端数据目录）
- 流式输出：兼容不同厂商的流式格式差异，支持查看模型的思考过程
- 上下文管理：可设置上下文窗口（4K ~ 1M）、保底最近轮数、单次回复上限与工具结果上限；
  超出预算的旧对话可自动压缩成摘要保留，显著降低多轮对话的 token 消耗
- Token 统计：每条回复底部显示本次消耗（输入 / 输出 / 合计），以及本次调用了哪些工具、
  联网搜索了哪些网页（可展开完整 URL）、命中了哪些 RAG 内容

### 联网搜索配置

联网搜索只有两种后端：

- **Tavily**（主后端，需要 API Key；在「设置 → Agent 能力」里填，申请简单、质量好）
- **自研爬虫兜底**（免 Key）：直接抓取 **Bing** 搜索结果页并解析

Tavily 按调用量计费，所以设置里有「**启用 Tavily**」开关：关掉后搜索直接走免 Key 的
爬虫后端，不消耗任何额度；需要更好的结果时再打开。

设置面板里有两个自检按钮：测试 Tavily / 测试爬虫(Bing)。
爬虫会带浏览器 UA、限制请求频率（最小间隔 1.5 秒 + 随机抖动）以避免被封。
请遵守目标站点的 robots.txt 与使用条款，不要用于批量采集。

搜索的用量与质量都有收敛：默认每次取 3 条（最多 5 条），一轮对话最多搜 6 次、
抓 6 个网页；同一个查询会复用上次结果、同一个 URL 不会重复抓。
结果会先做一轮筛选 —— 丢掉 AI 导航站与工具农场、官方来源置顶、同域名最多 2 条。
拿到资料后模型直接给结论，搜索过程只留在「思考过程」里。

打开搜索结果里的网页时，程序会抽取正文给模型阅读：配置了 Firecrawl Key 走 Firecrawl
（能处理 JS 渲染页面），否则用 BeautifulSoup 本地抽取（优先 `article` / `main`，剔除导航与脚本）。
抓取网页属于爬虫行为，请遵守目标站点的 robots.txt 与使用条款，控制请求频率。

另外，Agent 内置 `get_current_time` 工具：模型回答「今天有什么新闻」这类问题前会先取真实时间，
不会再出现"我不知道今天是几号"的情况。

### 省 token 的上下文管理

Agent 每跑一步（模型 → 工具 → 模型）都会把**完整上下文重新发一遍**，上下文越大，
消耗按轮数成倍放大。设置 → 「上下文」里可以调：

- **上下文窗口**：一次请求最多带多少 token（默认 200K，可选 4K ~ 1M）。
  滑块按档位走（8K / 16K / 32K / 64K / 128K / 200K / 320K / 512K / 1M），
  也可以手动输入任意值。窗口越满，之后每一轮重新计费的量越大。
- **保底最近轮数**：窗口再紧，最近这几轮对话也不丢（默认 10 轮）。
- **无限轮数**：打开后不再裁剪，历史一条不丢（代价是每轮都要重发完整上下文）。
- **上下文压缩**：被丢掉的旧消息交给模型压成一段短摘要存在本地，下次对话作为背景带回，
  既不丢关键信息又比原文省很多；压缩失败只是丢掉历史，不影响对话。
- **单次回复上限 / 工具结果上限**：限制模型单次能吐多少 token、
  工具返回的内容最多带多少字符进上下文（默认 3000 字符，上限 50000）；
  单次回复默认 8192 词元，也可以打开**无限长度**让它不受限制。

只想省额度的话，把「上下文窗口」调小、「工具结果上限」调小即可；
希望长对话不丢线索，就保持「上下文压缩」开启。

## 项目结构

```
main.py                FastAPI 入口
SilverWolf.py          路由层：聊天 / 会话 / 流式 / 提供商 / 设置
agent/                 Agent 本体
  runner.py            多步循环（模型 → 工具 → 模型）
  tools/               工具仓库，一个功能一个文件（放进目录即自动注册）
middleware/            横切能力：上下文裁剪、token 统计、流式格式归一化
rag.py / knowledge_base.py / vector_stores.py   RAG 检索与向量库
file_history_store.py  SQLite 会话与消息
file_attachments.py    附件解析（文本/压缩包/Office/可执行文件元信息）
provider_config.py     提供商与 API Key 管理
tests/                 pytest 冒烟（Agent 工具 / 沙箱 / 审批 / SSE 归一化）
frontend/              Vue 3 本地运行时（无构建、零 CDN）
```

新增一个工具只需要在 `agent/tools/` 里加一个文件并导出 `TOOLS` 字典，不用改注册表。
跑测试：`.venv\Scripts\python -m pytest tests/ -q`（测试自建自清，不碰用户数据）。

## 本地部署

环境要求：Windows / Linux / macOS，Python 3.10 或更高。若只使用云端模型，可以不用安装
Ollama；使用本地模型则需安装 [Ollama](https://ollama.com)。

### 一键安装（推荐）

Windows：双击运行 `install.bat`。

Linux / macOS：在项目目录执行 `./install.sh`。

脚本会自动创建虚拟环境、安装 Python 依赖，并检查前端运行时与 Ollama 是否就绪。

### 手动安装

Windows（命令提示符或 PowerShell）：

```bat
:: 1. 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

:: 2. 启动并拉取本地模型（可选）
ollama serve
ollama pull deepseek-r1:8b

:: 3. 启动应用
start.bat
```

启动后浏览器访问 http://127.0.0.1:8000

> Vue 3 前端运行时已随仓库内置（`frontend/vendor/vue.global.prod.js`），无需单独下载；
> 若文件缺失，运行 `install.bat` / `install.sh` 会自动下载。

Linux / macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

首次使用请在页面左下角打开设置，在「模型提供商」中添加模型；模型相关配置保存在
`data/providers.json`，不会写入代码仓库。

## Docker 部署

### 准备工作

1. 安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Windows / macOS）
   或 Linux 的 Docker Engine，并确认 `docker` 命令可用（终端执行 `docker --version` 验证）。
2. 获取项目代码：`git clone` 或下载压缩包解压，然后在项目根目录打开终端。

### 第一步：构建镜像

在项目根目录执行：

```bash
docker build -t silver-wolf .
```

- `-t silver-wolf` 是给镜像起的名字，可自行修改；
- 结尾的 `.` 表示使用当前目录作为构建上下文；
- 首次构建需要联网安装 Python 依赖，之后再次构建会使用缓存，速度较快。

### 第二步：运行容器

```bash
docker run -d --name silver-wolf -p 8000:8000 -v silver_wolf_data:/app/data silver-wolf
```

参数说明：

- `-d`：后台运行，容器不会占用当前终端；
- `--name silver-wolf`：容器名称，后续用这个名字管理容器；
- `-p 8000:8000`：把容器内的 8000 端口映射到本机 8000 端口，浏览器访问
  http://127.0.0.1:8000 即可；
- `-v silver_wolf_data:/app/data`：创建一个数据卷，用于保存配置文件（含 API Key）、
  数据库和上传的附件。**建议保留**，否则删除或重建容器后配置和聊天记录会丢失；
- 末尾的 `silver-wolf` 是第一步构建的镜像名。

如果 8000 端口已被占用，可换一个映射端口，例如 `-p 8001:8000`，然后访问
http://127.0.0.1:8001。

### 第三步：配置模型并连接宿主机 Ollama（可选）

打开 http://127.0.0.1:8000，在左下角设置中添加模型提供商。容器内默认无法访问
宿主机上运行的程序，如需让容器使用宿主机 Ollama：

- Windows / macOS（Docker Desktop）：接口地址填写
  `http://host.docker.internal:11434/v1`；
- Linux：先让 Ollama 监听所有网卡（启动前设置环境变量
  `OLLAMA_HOST=0.0.0.0:11434`），再在容器内填写宿主机内网 IP，例如
  `http://192.168.1.100:11434/v1`（用 `hostname -I` 可查看本机 IP）。

如果只是使用云端模型，或直接在宿主机上运行程序，则无需关心以上内容。

### 常用管理命令

```bash
docker ps                  # 查看运行中的容器
docker logs silver-wolf    # 查看日志
docker stop silver-wolf    # 停止容器
docker start silver-wolf   # 重新启动容器
```

更新到新版本：重新执行 `docker build -t silver-wolf .`，然后
`docker rm -f silver-wolf` 删除旧容器，再按第二步重新运行。

## 配置说明

绝大多数配置在界面内完成（设置面板），不需要修改代码或环境变量。代码中保留的环境变量
仅在全新环境、且尚未在界面添加提供商时作为首次启动的默认值，日常使用可以忽略。

## 数据与隐私

- API Key 保存在本机 `data/providers.json`，接口返回时已做脱敏；
- 数据库、上传的附件、知识库向量等运行时数据都位于 `data/` 目录，已在 .gitignore 中排除，
  不会进入代码仓库；
- 上传的文件仅做读取分析，不会被程序执行。

## 项目结构

```
├── main.py                  # 应用入口（FastAPI）
├── SilverWolf.py            # 聊天、会话、提供商、附件等接口
├── provider_config.py       # 模型提供商配置管理
├── file_attachments.py      # 附件保存与内容提取
├── file_history_store.py    # SQLite 会话/消息存储
├── rag.py / vector_stores.py  # RAG 检索与向量化
├── frontend/                # Vue 3 前端（本地运行时，无构建）
├── static/                  # 静态资源（不含版权素材）
└── docs/                    # 功能说明、更新日志等
```

## 相关文档

- [功能说明](docs/USER_MANUAL.md)
- [更新日志](CHANGELOG.md)

## License

[MIT](LICENSE)。角色设定版权归《崩坏：星穹铁道》官方所有，本仓库不包含官方素材。
