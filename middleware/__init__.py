"""
middleware 包 - 横切能力（不改变主流程，但每轮对话都会经过）

【什么代码该放这里】
    判断标准只有一条：**它是不是被多个入口共同需要、且不专属于某一个业务模块？**
    是 → 放这里；只服务 Agent 的 → 放 agent/；只服务某个功能的 → 放对应模块。

【当前成员】
    - `context.py`      对话上下文管理：按 token 预算裁剪历史、压缩摘要、截断长文本。
                        调用方：SilverWolf.py（普通对话与 Agent 对话都会先过一遍）。
    - `token_usage.py`  各家厂商 token 用量解析与估算（有些厂商不回 usage，需要估算）。
                        调用方：agent/runner.py、middleware/context.py。
    - `stream_utils.py` 流式增量归一化：不同厂商的 SSE 帧格式差异（空 choices、
                        content 是数组、reasoning_content 等）都在这里抹平。
                        调用方：SilverWolf.py、agent/runner.py。

【为什么 token_usage 算中间件而不是工具】
    它既被 Agent 循环直接使用（统计一轮消耗），也在对话结束后被 API 层调用来补齐
    统计信息——属于"贯穿请求生命周期的基础设施"，所以放中间件而不是工具目录。
    注意：`agent/tools/` 下放的是**模型可以主动调用**的工具，两者不是一回事。

【依赖方向】
    middleware ← agent ← SilverWolf.py（上层可以依赖下层，反过来不行）
"""
