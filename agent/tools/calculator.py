"""
agent/tools/calculator.py - 安全计算器（AST 白名单求值，零依赖零风险）

【这个文件干什么】
    语言模型做算术不可靠（小数点、大数、进制换算经常翻车），与其让它心算，
    不如给它一个计算器：`calculate` 工具处理纯数值表达式。
    用 Python 的 ast 解析表达式，**只放行白名单内的运算**——数字/算术/比较/逻辑
    和有限几个数学函数，任何属性访问、下標、赋值、循环、导入都会被拒绝，
    因此不存在任意代码执行的风险（这是它和「代码执行沙箱」本质的区别）。

【谁在调用】模型 → 工具注册表 → 本文件底部的 TOOLS（自动发现）。
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Dict

# 白名单数学函数：需要时在这里加，加了才算数
_SAFE_FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max, "pow": pow, "sum": sum,
    "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "degrees": math.degrees, "radians": math.radians,
    "factorial": math.factorial, "gcd": math.gcd,
}
# 常亮（名字白名单）
_SAFE_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos, ast.USub: operator.neg,
    ast.Invert: operator.invert, ast.Not: operator.not_,
}
_CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_MAX_EXPR_LEN = 500  # 表达式长度上限，防模型塞一个超长怪物进来


def evaluate_expression(expression: str) -> float:
    """安全求值一个数学表达式；不安全/不合法的表达式抛 ValueError。"""
    if not expression or len(expression) > _MAX_EXPR_LEN:
        raise ValueError(f"表达式为空或超过 {_MAX_EXPR_LEN} 字符")
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree.body)


def _eval_node(node: ast.AST):
    """递归求值白名单 AST 节点；任何不在白名单里的节点直接拒绝。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex, bool)):
            return node.value
        raise ValueError(f"不支持的常量类型：{type(node.value).__name__}")
    if isinstance(node, ast.Name):
        if node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]
        raise ValueError(f"不允许使用变量：{node.id}")
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for v in node.values:
                if not _eval_node(v):
                    return False
            return True
        for v in node.values:
            if _eval_node(v):
                return True
        return False
    if isinstance(node, ast.Compare) and node.ops and all(type(op) in _CMP_OPS for op in node.ops):
        # 只支持单层比较（a < b），多链（a < b < c）被拒，避免实现出错
        if len(node.ops) != 1:
            raise ValueError("只支持单层比较运算")
        return _CMP_OPS[type(node.ops[0])](_eval_node(node.left), _eval_node(node.comparators[0]))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id not in _SAFE_FUNCS:
            raise ValueError(f"不允许调用的函数：{node.func.id}")
        if node.keywords:
            raise ValueError("不支持关键字参数")
        return _SAFE_FUNCS[node.func.id](*[_eval_node(a) for a in node.args])
    raise ValueError(f"不支持的表达式形式：{type(node).__name__}（只允许数值运算）")


def _format_result(value) -> str:
    """把结果格式化成可读文本：整数给整，浮点去尾零保留 6 位。"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, complex):
        return str(value)
    if isinstance(value, float):
        if value.is_integer() or abs(value) >= 1e15:
            return f"{int(value)}"
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _tool_calculate(args: Dict[str, Any]):
    expression = str(args.get("expression", "") or "").strip()
    if not expression:
        return "需要提供 expression（要计算的数学表达式）。", {"ok": False}
    try:
        result = evaluate_expression(expression)
    except Exception as e:
        # 求值失败也要把表达式还回去，方便模型自我纠正
        return f"计算失败：{e}。表达式原文：{expression}", {"ok": False, "expression": expression}
    return (
        f"{expression} = {_format_result(result)}",
        {"ok": True, "expression": expression, "result": _format_result(result)},
    )


TOOLS = {
    "calculate": {
        "description": "安全计算数学表达式：支持四则运算、幂、括号、比较与逻辑，"
                       "以及常用函数（sqrt/log/sin/floor/round/abs/min/max/pow/factorial/gcd 等）。"
                       "需要精确数值、或心算不太可靠时（大数、小数、换算、概率）使用它，"
                       "把要算的式子原样交给它。示例：'2**10'、'sqrt(144) + round(3.14159, 2)'、'(17+25)*2 > 80'。",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "纯数学表达式（不含赋值/函数定义/导入等语句）"},
            },
            "required": ["expression"],
        },
        "handler": _tool_calculate,
    },
}