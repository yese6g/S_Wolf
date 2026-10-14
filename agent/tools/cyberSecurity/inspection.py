"""安全专业面板与模型共用的被动检查：检查URL、响应头或用户提供的配置。

调用链：agent/tools/__init__.py 递归发现本模块的 TOOLS 并注册 →
模型经 function calling 调用，或工作台「分析」页用 /tools/{name}/test 直接调用。
边界：不主动扫描内网、不发起 HTTP 请求、不生成未经实测的漏洞结论；
公网抓取仍走 middleware/outbound.py 的固定IP出站门禁。
"""

import json
from urllib.parse import urlsplit

from middleware.outbound import public_addresses

# 缺失即为配置缺口的响应头：(键, 严重度, 缺失说明, 期望值线索)
EXPECTED_HEADERS = (
    (
        "content-security-policy",
        "high",
        "缺少 CSP，浏览器不会限制脚本与资源来源。",
        "至少定义 default-src 'self'，避免 unsafe-inline / unsafe-eval / *。",
    ),
    (
        "strict-transport-security",
        "high",
        "HTTPS 站点缺少 HSTS，首次明文跳转仍可被拦截。",
        "max-age 至少 15552000，确认全站 HTTPS 后再考虑 includeSubDomains。",
    ),
    (
        "x-content-type-options",
        "medium",
        "缺少 nosniff，部分浏览器会按内容猜测类型。",
        "设为 nosniff。",
    ),
    (
        "referrer-policy",
        "low",
        "缺少 Referrer-Policy，跨站请求可能带出完整来源地址。",
        "常用 strict-origin-when-cross-origin 或 no-referrer。",
    ),
    (
        "permissions-policy",
        "low",
        "缺少 Permissions-Policy，浏览器特性默认开放。",
        "按需关闭 camera / microphone / geolocation 等。",
    ),
)
# 会泄露实现细节的响应头：存在即为线索
LEAKY_HEADERS = ("server", "x-powered-by", "x-aspnet-version", "x-aspnetmvc-version", "x-generator")


def _as_list(value):
    """同一响应头可能重复出现：允许字符串、列表或换行分隔。"""
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [part for part in str(value).split("\n") if part.strip()]


def url_check(args):
    """被动检查 URL 与 DNS 是否公网地址：不发任何 HTTP 请求。"""
    url = str(args.get("url", "")).strip()
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or not parsed.hostname
        ):
            raise ValueError("需要无凭据的http(s)地址")
        addresses = public_addresses(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
        result = {
            "host": parsed.hostname,
            "addresses": addresses,
            "https": parsed.scheme == "https",
            "network_request": False,
            "note": "只做了协议与 DNS 归属判断，没有向目标发出请求。",
        }
        return json.dumps(result, ensure_ascii=False), {"ok": True, **result}
    except (OSError, ValueError) as exc:
        return str(exc), {"ok": False, "blocked": True}


def headers_check(args):
    """被动体检用户粘贴的响应头：缺失项、弱配置、Cookie 标志与信息泄露。"""
    headers = args.get("headers", {})
    if not isinstance(headers, dict):
        return "headers必须是对象。", {"ok": False}
    values = {str(k).lower(): _as_list(v) for k, v in headers.items()}
    joined = {k: "; ".join(v) for k, v in values.items()}
    # HSTS 只对 HTTPS 有意义；调用方不确定时按 HTTPS 处理并如实注明
    is_https = args.get("https", True) is not False

    findings = []

    for name, severity, missing_note, hint in EXPECTED_HEADERS:
        if name == "strict-transport-security" and not is_https:
            continue
        if not joined.get(name):
            findings.append({"severity": severity, "header": name, "issue": missing_note, "hint": hint})

    # frame 保护：X-Frame-Options 与 CSP frame-ancestors 任一即可
    if not joined.get("x-frame-options") and "frame-ancestors" not in joined.get("content-security-policy", ""):
        findings.append(
            {
                "severity": "medium",
                "header": "x-frame-options / frame-ancestors",
                "issue": "两者都缺失，页面可被任意站点内嵌（点击劫持的配置前提）。",
                "hint": "设置 X-Frame-Options: DENY，或在 CSP 中写 frame-ancestors 'none'。",
            }
        )

    csp = joined.get("content-security-policy", "")
    if csp:
        for token, severity, issue in (
            ("unsafe-inline", "medium", "CSP 允许内联脚本或样式，削弱了 XSS 防护。"),
            ("unsafe-eval", "medium", "CSP 允许 eval 类动态执行。"),
            ("*", "low", "CSP 含通配来源，白名单实际被放宽。"),
        ):
            if token in csp:
                findings.append({"severity": severity, "header": "content-security-policy", "issue": issue, "hint": f"移除 {token}，改用 nonce 或哈希。"})
        if "default-src" not in csp:
            findings.append(
                {
                    "severity": "low",
                    "header": "content-security-policy",
                    "issue": "没有 default-src 兜底，未列出的资源类型不受限制。",
                    "hint": "补 default-src 'self' 作为兜底。",
                }
            )

    cookie_lines = []
    for key in ("set-cookie", "set-cookie2"):
        cookie_lines.extend(values.get(key, []))
    for raw in cookie_lines:
        cookie_name = raw.split("=", 1)[0].strip() or "(未命名)"
        flags = raw.lower()
        for flag, severity, issue in (
            ("secure", "medium", "Cookie 未标 Secure，明文请求也会带上。"),
            ("httponly", "medium", "Cookie 未标 HttpOnly，脚本可直接读取。"),
            ("samesite", "low", "Cookie 未标 SameSite，跨站请求会携带。"),
        ):
            if flag not in flags:
                findings.append({"severity": severity, "header": "set-cookie", "issue": f"{cookie_name}: {issue}", "hint": f"为该 Cookie 加上 {flag.title()}。"})

    for name in LEAKY_HEADERS:
        if joined.get(name):
            findings.append(
                {
                    "severity": "low",
                    "header": name,
                    "issue": f"响应头暴露实现细节：{joined[name][:120]}",
                    "hint": "生产环境移除或改写这些响应头。",
                }
            )

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda item: order[item["severity"]])
    counts = {level: sum(1 for f in findings if f["severity"] == level) for level in order}
    result = {
        "checked_headers": sorted(values),
        "counts": counts,
        "findings": findings,
        "note": "响应头缺失只是配置线索，不等于已确认可利用漏洞；需结合页面用途与实际请求验证。",
    }
    if not is_https:
        result["note"] += " 本次按 HTTP 站点处理，未评估 HSTS。"
    summary = (
        f"共检查 {len(values)} 个响应头，线索 {len(findings)} 条"
        f"（高 {counts['high']} / 中 {counts['medium']} / 低 {counts['low']}）。"
        + result["note"]
    )
    return summary + "\n" + json.dumps(result, ensure_ascii=False), {"ok": True, **result}


TOOLS = {
    "security_url_check": {
        "description": "被动检查URL与DNS是否公网；拒绝本机/内网/元数据地址，不发HTTP请求。",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        "handler": url_check,
        "risks": ["network"],
    },
    "security_headers_check": {
        "description": "被动体检用户提供的HTTP响应头：缺失的安全头、弱CSP、Cookie标志与信息泄露，按严重度列出线索；不主动扫描，不发请求。",
        "parameters": {
            "type": "object",
            "properties": {
                "headers": {"type": "object", "description": "响应头对象，值可以是字符串或字符串数组"},
                "https": {"type": "boolean", "description": "站点是否 HTTPS，仅用于判断 HSTS 是否适用；不发起请求"},
            },
            "required": ["headers"],
        },
        "handler": headers_check,
    },
}
