"""工具风险策略：注册时打标，runner 执行时统一查询；未知外部工具默认审批。

写入、删除、网络与外部进程分别标记，名字守卫补住新工具漏审批的维护陷阱。
"""

import re
import fnmatch
import logging

MUTATIONS = {
    "fs_write",
    "fs_delete",
    "fs_edit",
    "fs_rollback",
    "rag_write_file",
    "rag_delete_file",
    "rag_import_url",
    "tools_upsert",
    "tools_delete",
    "remember",
    "forget",
}
NETWORK = {"web_search", "fetch_page", "rag_import_url"}


def classify(name, spec):
    """返回风险维度并更新 spec；工具描述/只读注解不能绕过审批。"""
    risks = list(spec.get("risks", []))
    if name in MUTATIONS:
        risks.append("delete" if "delete" in name or name == "forget" else "write")
        spec["approval_required"] = True
    if (
        re.search(r"(?:write|delete|upsert|import|rollback|edit)", name)
        and name not in MUTATIONS
    ):
        spec["approval_required"] = True
        risks.append("unknown_mutation")
    if name in NETWORK:
        risks.append("network")
    spec["risks"] = sorted(set(risks))
    return spec


def apply_hook(event, name, ok=None):
    """受限声明式Hooks只阻止或记账，不执行任意脚本、不放宽审批。"""
    from config_data import BASE_DIR
    from agent import persistence

    rules = persistence.load(BASE_DIR / "data", "ui_state", {}).get("tool_hooks", [])
    for rule in rules:
        if (
            not rule.get("enabled", True)
            or rule.get("event") != event
            or not fnmatch.fnmatchcase(name, rule.get("tool", ""))
        ):
            continue
        if rule.get("action") == "deny" and event == "before_tool":
            logging.getLogger("audit.tools").info(
                "hook=%s event=%s tool=%s denied=True", rule.get("id"), event, name
            )
            return "调用被工作台守卫规则阻止：" + str(rule.get("id", ""))
        if rule.get("action") == "log":
            logging.getLogger("audit.tools").info(
                "hook=%s event=%s tool=%s ok=%s", rule.get("id"), event, name, ok
            )
    return None
