"""HTTP 工具 - 共享内核，被各层引用。"""

import ipaddress
import os

from starlette.requests import Request


def get_client_ip(request: Request) -> str:
    """从可信代理转发头或连接信息提取客户端 IP。"""
    direct_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("X-Forwarded-For")
    if not forwarded or not _is_trusted_proxy(direct_ip):
        return direct_ip

    addresses = [item.strip() for item in forwarded.split(",") if item.strip()]
    if not addresses:
        return direct_ip

    try:
        trusted_hops = max(1, int(os.getenv("MHTI_TRUSTED_PROXY_HOPS", "1")))
    except ValueError:
        trusted_hops = 1
    candidate = addresses[max(0, len(addresses) - trusted_hops)]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return direct_ip
    return candidate


def _is_trusted_proxy(client_ip: str) -> bool:
    """Only honor forwarding headers from configured immediate proxy networks."""
    configured = os.getenv(
        "MHTI_TRUSTED_PROXY_NETWORKS",
        "127.0.0.0/8,::1/128",
    )
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for value in configured.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False
