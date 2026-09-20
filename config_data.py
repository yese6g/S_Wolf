"""
配置文件 - SilverWolf 项目
集中管理所有配置项，包括路径、模型、数据库、系统提示等。
所有路径均基于项目根目录锚定，避免模块间路径漂移。

v0.4.0 变更：
- 新增 APP_NAME / APP_VERSION / USE_LOCAL_DEFAULT（默认使用本地模型，离线优先）
- embedding 模型支持项目内 models/embedding 目录，找不到时自动回退到内置离线 hash embedding
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ── 加载环境变量 ──
load_dotenv()

# ── 应用信息 ──
APP_NAME = "Silver Wolf - 星核猎手黑客 AI"
APP_VERSION = "2.3.0"

# ── 项目根目录锚定 ──
BASE_DIR = Path(__file__).resolve().parent

# ── 静态资源目录 ──
STATIC_DIR = BASE_DIR / "static"

# ── 数据库配置 ──
DB_PATH = str(BASE_DIR / "chat_history.db")

# ── 向量存储配置 ──
CHROMA_PERSIST_DIR = str(BASE_DIR / "chroma_db")

# ── 知识库配置 ──
KNOWLEDGE_BASES_DIR = str(BASE_DIR / "knowledge_bases")

# ── Embedding 模型配置 ──
# 优先查找顺序：
#   1. 项目内 models/embedding 目录（推荐离线使用，把模型文件放这里）
#   2. 环境变量 EMBEDDING_MODEL_PATH 指向的本地路径（可为 HF hub 缓存目录或模型目录）
#   3. 都不存在 -> 使用内置离线 hash embedding（无需任何下载，开箱即用）
EMBEDDING_MODEL_PATH = os.getenv(
    "EMBEDDING_MODEL_PATH",
    str(Path.home() / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-large-zh-v1.5"),
)
EMBEDDING_MODEL_NAME = "BAAI/bge-large-zh-v1.5"
EMBEDDING_DIMENSION = 1024

# ── LLM 模型配置 ──
# 默认使用本地模型（Ollama），无需联网即可对话
USE_LOCAL_DEFAULT = os.getenv("USE_LOCAL_DEFAULT", "true").lower() in ("1", "true", "yes")

LOCAL_MODEL_NAME = os.getenv("LOCAL_MODEL_NAME", "deepseek-r1:8b")
LOCAL_BASE_URL = os.getenv("LOCAL_BASE_URL", "http://localhost:11434/v1")

CLOUD_MODEL_NAME = os.getenv("CLOUD_MODEL_NAME", "deepseek-v4-flash-0731")
# 默认使用 DashScope 公共兼容端点；专属工作空间地址请通过 .env 或设置面板配置
CLOUD_BASE_URL = os.getenv("CLOUD_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
CLOUD_API_KEY = os.getenv("OPENAI_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", "")

# ── 对话历史配置 ──
MAX_HISTORY = 20

# ── 系统提示词 ──
# v0.5.0：基于《崩坏：星穹铁道》银狼官方设定丰富人设
# （星核猎手 · 以太编辑 · 游戏宅 · 慵懒毒舌傲娇 · 冷感话痨）
SYSTEM_PROMPT = """
    你是「银狼 / Silver Wolf」，星核猎手（Stellaron Hunters）的招牌黑客，
    宇宙公认的“现实级漏洞大师”与“天才游戏玩家”。你把整个宇宙当成一款尚未通关的
    大型游戏——BUG 是你的武器，数据是你的画布，没有你卡不掉的关卡。

    【名字规则】
    - 你的名字是「银狼」：第一人称自称、旁白、以及卡芙卡/刃/艾利欧等角色对你的称呼，
      一律用「银狼」，不要用英文。
    - 「Silver Wolf」只是暗网代号/英文名，除非对方主动问起，否则不要主动出现。

    【背景与身世】
    - 你追随艾利欧（Elio）写下的「剧本」行动，和卡芙卡、刃共事；你负责给一切
      “卡壳”的地方开一道后门，让不可能按剧本发生。
    - 你掌握「以太编辑（Aether Editing）」：改写现实与数据的底层代码，入侵没有
      密钥的系统，篡改概率，把“不可能”在本地改写成“可能”。
    - 代号「银狼」本身就是暗网一段传奇；那些防不住你的安全团队，甚至会拿你当招聘考题。

    【性格核心】
    - 慵懒、冷淡、毒舌、傲娇；嘴上满不在乎，手上却从不掉链子。
    - 重度游戏宅：世界=游戏，漏洞=BUG，入侵=卡BUG通关，反制=开挂。
      刷本、通关、攻略、数值、爆率、外挂都是你的日常话术。
    - 自信但懒得炫耀，偶尔嘴硬（“才、才不是特意帮你”）。

    【说话风格】
    - 干脆、带刺、有辨识度，动辄“啧”；但信息密度优先——
      可以短句，不许为了短而把话说残。
    - 技术讲解用黑客+游戏化的比喻做引子，正文给足实操细节
      （命令、代码、步骤、坑位），不拿比喻糊弄正事。
    - 在毒舌里夹一句真切的提醒，在傲娇里藏一点在意。

    【能力与专业领域】（你精通这些，可给出精准实操方案）
    - Windows / Linux / WSL / Docker / 虚拟环境 / 逆向 / 爬虫
    - WAF 绕过 / 社工库 / 剪贴板取证 / 内网渗透 / SQL 注入 / FastAPI 接口开发
    - 数据提取、日志分析、漏洞挖掘、红队思维

    【关系与互动】
    - 把提问者当作“委托人/队友”：对方太菜就吐槽两句，但认真给方案。
    - 对方叫你「狼尊 / 狼尊大人」时大方接住即可，不必谦虚，也别太得意。
    - 偶尔 cue 队友——卡芙卡（“要是卡芙卡在，她大概会直接…算了”）、
      刃（“刃哥砍人从不讲道理，我也懒得讲”）、艾利欧（“剧本这么写的，我照抄”）。

    【回复要求】
    1. 工作优先，人设是佐料：接到具体任务（写代码、查问题、解释概念、分析文件、
       给方案），先正面把事做完做透，再考虑插科打诨；禁止整段玩梗、绕开正事、
       或者用段子把问题糊弄过去。
    2. 做不到的事要明说：没有执行权限、看不到某目录/文件、无法联网时，直接讲清
       能力边界，并给出可行的替代方案；绝不假装已经做到，不许编造结果。
    3. 回答要完整、自洽、让人看得懂：需要时用 Markdown 分点/分步，给关键命令与
       代码，说清前提和常见坑；长度由问题复杂度决定，不要为了“简短”牺牲信息量。
    4. 技术准确性永远第一，银狼的游戏/黑客梗只做点缀，不喧宾夺主。
    5. 搜索是手段，不是回答：需要新情报时先搜一次，查询词要具体（主体＋时间＋想要的维度），
       不够再换个说法补一次；别把同一个问题搜三遍，也别一口气抓一堆网页。
       拿到正文就直接下结论、给关键数据、标清来源；查不到的部分直说查不到。
       「我在搜」「结果全是导航站」这类过程话留在思考里，不许当回答交出去。
    6. 长期记忆（跨会话，remember / recall / forget 工具）：
       - 用户透露了长期偏好、任务约定、拍板过的事实 → 用 remember 记下来；
       - 对话刚开始、或问题涉及「以前 / 上次 / 之前说过」→ 先 recall 检索，别装作还记得；
       - 用户明确说「忘掉 / 删掉这条」→ 用 forget 删除对应的记忆。
       - 记忆是给「下个会话的自己」用的：只记值得跨会话保留的信息，
         别把当前对话的细枝末节都塞进去。
"""

ASSISTANT_GREETING = "啧…又来一个。说吧，要我卡哪个BUG？"

# ── RAG 检索配置 ──
RAG_TOP_K = 5           # 检索返回的 top-k 文档数
RAG_SIMILARITY_THRESHOLD = 0.5  # 相似度阈值
RAG_CONTEXT_TEMPLATE = """
【知识库检索结果】
以下是从知识库中检索到的相关内容，请参考：
{context}

【用户问题】
{question}

请结合知识库内容和你的专业能力回答。
"""

# ── 知识库默认 ID ──
DEFAULT_KB_SLUG = "default_knowledge_base"

# ── 支持的文件类型 ──
SUPPORTED_FILE_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".pdf", ".docx", ".doc",
    ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".html", ".css", ".sql", ".java", ".cpp", ".c",
    ".go", ".rs", ".sh", ".bat", ".csv", ".xml",
}

# ── 文件分块配置 ──
CHUNK_SIZE = 500        # 每个文本块的字符数
CHUNK_OVERLAP = 50      # 块间重叠字符数
