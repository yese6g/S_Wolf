"""自定义工具出站门禁：固定白名单域名，DNS 解析后锁定公网 IP，禁止重定向。

HTTP/HTTPS 直连已验证地址，HTTPS 仍按原域名验证证书；避免检查一次再二次解析的竞态。
只给声明式 HTTP 工具调用，不改变用户自行配置的模型 Provider 地址。
"""
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit


def public_addresses(host, port):
    """返回经验证的公网地址；任一解析记录为内网/特殊地址即拒绝。"""
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("自定义工具不允许访问本机、内网或特殊地址")
    return addresses


def request_public(url, method, headers, args, allowed_hosts, *, max_bytes=200000, max_chars=2000, response_info=False,
                   allow_loopback=False, accepted_status=(), rpc_id=None):
    """执行白名单 GET/POST，返回最多 2000 字符；正文读取也有字节上限。"""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or host not in allowed_hosts:
        raise ValueError("目标域名不在这项工具的批准白名单")
    if method not in {"GET", "POST"}:
        raise ValueError("只支持 GET/POST")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if allow_loopback and host in {'localhost','127.0.0.1','::1'}:
        addresses=['::1' if host=='::1' else '127.0.0.1']
    else:addresses = public_addresses(host, port)
    connection = http.client.HTTPConnection(addresses[0], port, timeout=15)
    try:
        connection.connect()
        if parsed.scheme == "https":
            connection.sock = ssl.create_default_context().wrap_socket(connection.sock, server_hostname=host)
        safe_headers = {k: v for k, v in headers.items() if k.lower() not in {"host", "content-length", "transfer-encoding", "connection"}}
        safe_headers["Host"] = parsed.netloc
        body = json.dumps(args, ensure_ascii=False).encode("utf-8") if method == "POST" else None
        if body:
            safe_headers["Content-Type"] = "application/json"
        connection.request(method, (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""), body, safe_headers)
        response = connection.getresponse()
        if not 200 <= response.status < 300 and response.status not in accepted_status:
            raise ValueError(f"HTTP {response.status}，不会自动跟随重定向")
        content_type=response.getheader('Content-Type','')
        if 'text/event-stream' in content_type and rpc_id is not None:
            count=0;fragments=[];data=None
            while count<=max_bytes:
                line=response.readline(max_bytes-count+1);count+=len(line)
                if count>max_bytes:raise ValueError('MCP响应超过大小上限')
                if not line:break
                if line.startswith(b'data:'):fragments.append(line[5:].lstrip().rstrip(b'\r\n'))
                if not line.strip() and fragments:
                    value=b'\n'.join(fragments);fragments=[]
                    packet=json.loads(value)
                    if packet.get('id')==rpc_id:data=value;break
            if data is None:raise ValueError('MCP响应缺少匹配的结果或超出大小上限')
        else:data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"出站响应超过 {max_bytes} 字节")
        encoding = response.headers.get_content_charset() or 'utf-8'
        try:
            text = data.decode(encoding, errors="replace")
        except LookupError:
            text = data.decode('utf-8', errors='replace')
        if max_chars is not None:
            text = text[:max_chars]
        return {'text': text, 'content_type': content_type, 'status':response.status,
                'session_id':response.getheader('Mcp-Session-Id','')} if response_info else text
    finally:
        connection.close()
