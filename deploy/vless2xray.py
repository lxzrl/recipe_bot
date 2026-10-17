#!/usr/bin/env python3
"""Превращает ссылку vless://… в конфиг Xray с локальным SOCKS5-прокси для бота.

    python3 vless2xray.py 'vless://uuid@host:443?security=reality&…' > config.json
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import parse_qs, unquote, urlsplit

NETWORKS = {"tcp", "raw", "ws", "grpc", "xhttp", "splithttp", "httpupgrade"}


def build_config(link: str, port: int = 10808) -> dict:
    url = urlsplit(link.strip())
    if url.scheme != "vless":
        raise ValueError("Ссылка должна начинаться с vless://")
    if not url.username or not url.hostname:
        raise ValueError("В ссылке нет UUID или адреса сервера")
    q = {k: v[0] for k, v in parse_qs(url.query).items()}

    network = q.get("type", "tcp")
    if network not in NETWORKS:
        raise ValueError(f"Тип транспорта «{network}» не поддерживается")
    if network == "raw":
        network = "tcp"
    if network == "splithttp":
        network = "xhttp"
    if network == "tcp" and q.get("headerType", "none") != "none":
        raise ValueError("TCP с HTTP-маскировкой (headerType) не поддерживается")

    user = {"id": unquote(url.username), "encryption": q.get("encryption", "none")}
    if q.get("flow"):
        user["flow"] = q["flow"]

    stream: dict = {"network": network}
    security = q.get("security", "none")
    if security == "reality":
        if not q.get("pbk"):
            raise ValueError("Для REALITY в ссылке нужен параметр pbk (публичный ключ)")
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": q.get("sni", ""),
            "fingerprint": q.get("fp", "chrome"),
            "publicKey": q["pbk"],
            "shortId": q.get("sid", ""),
            "spiderX": unquote(q.get("spx", "")),
        }
    elif security == "tls":
        tls = {"serverName": q.get("sni") or q.get("host") or url.hostname}
        if q.get("fp"):
            tls["fingerprint"] = q["fp"]
        if q.get("alpn"):
            tls["alpn"] = unquote(q["alpn"]).split(",")
        if q.get("allowInsecure") in ("1", "true"):
            tls["allowInsecure"] = True
        stream["security"] = "tls"
        stream["tlsSettings"] = tls
    elif security != "none":
        raise ValueError(f"Шифрование «{security}» не поддерживается")

    path = unquote(q.get("path", "/"))
    host = q.get("host", "")
    if network == "ws":
        stream["wsSettings"] = {"path": path, "host": host}
    elif network == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": path, "host": host}
    elif network == "xhttp":
        stream["xhttpSettings"] = {"path": path, "host": host, "mode": q.get("mode", "auto")}
    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": unquote(q.get("serviceName", "")),
            "multiMode": q.get("mode") == "multi",
        }

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",  # прокси доступен только самому серверу
                "port": port,
                "protocol": "socks",
                "settings": {"udp": False},
            }
        ],
        "outbounds": [
            {
                "tag": "vless-out",
                "protocol": "vless",
                "settings": {
                    "vnext": [{"address": url.hostname, "port": url.port or 443, "users": [user]}]
                },
                "streamSettings": stream,
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("link")
    parser.add_argument("--port", type=int, default=10808)
    args = parser.parse_args()
    try:
        config = build_config(args.link, args.port)
    except ValueError as e:
        sys.exit(f"Ошибка: {e}")
    json.dump(config, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
