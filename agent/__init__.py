"""
agent 包 - Agent 本体（怎么想、怎么循环、能用哪些工具）

【目录结构】
    agent/runner.py     多步循环：调用模型 → 解析工具调用 → 执行工具 → 把结果喂回去，
                        直到模型不再要工具或达到 max_steps。
                        调用方：SilverWolf.py 的 /api/chat 与 /api/chat/stream。
    agent/tools/        工具仓库，一个功能一个文件，放进目录即自动注册（详见其 __init__.py）。

【和 middleware 的分工】
    - agent/：只有「Agent 模式」才会用到的东西（循环、工具调用）。
    - middleware/：普通对话与 Agent 对话都要用的横切能力（上下文裁剪、token 统计、
      流式格式归一化）。

【为什么单独成包】
    原来 agent_runner.py / agent_tools.py 平铺在根目录，随着功能变多会越来越难找：
    新增一个工具要改 460 行的注册表文件。拆成包之后，"加一个工具 = 加一个文件"。
"""
