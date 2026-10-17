import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "vless2xray", Path(__file__).parent.parent / "deploy" / "vless2xray.py"
)
vless2xray = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vless2xray)
build = vless2xray.build_config

UUID = "11111111-2222-3333-4444-555555555555"


def outbound(cfg):
    return cfg["outbounds"][0]


def test_reality_vision():
    cfg = build(
        f"vless://{UUID}@1.2.3.4:443?type=tcp&security=reality&pbk=PUBKEY&fp=chrome"
        "&sni=www.google.com&sid=ab12&spx=%2F&flow=xtls-rprx-vision#Мой%20VPN"
    )
    out = outbound(cfg)
    server = out["settings"]["vnext"][0]
    assert (server["address"], server["port"]) == ("1.2.3.4", 443)
    assert server["users"] == [{"id": UUID, "encryption": "none", "flow": "xtls-rprx-vision"}]
    assert out["streamSettings"] == {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
            "serverName": "www.google.com",
            "fingerprint": "chrome",
            "publicKey": "PUBKEY",
            "shortId": "ab12",
            "spiderX": "/",
        },
    }
    inbound = cfg["inbounds"][0]
    assert (inbound["listen"], inbound["port"], inbound["protocol"]) == ("127.0.0.1", 10808, "socks")


def test_ws_tls():
    cfg = build(f"vless://{UUID}@vpn.example.com:8443?type=ws&security=tls&path=%2Fws%3Fed%3D2048&host=cdn.example.com&alpn=h2%2Chttp%2F1.1")
    stream = outbound(cfg)["streamSettings"]
    assert stream["tlsSettings"] == {"serverName": "cdn.example.com", "alpn": ["h2", "http/1.1"]}
    assert stream["wsSettings"] == {"path": "/ws?ed=2048", "host": "cdn.example.com"}
    assert outbound(cfg)["settings"]["vnext"][0]["port"] == 8443


def test_grpc_and_xhttp():
    stream = outbound(build(f"vless://{UUID}@h:443?type=grpc&security=tls&serviceName=svc&mode=multi"))["streamSettings"]
    assert stream["grpcSettings"] == {"serviceName": "svc", "multiMode": True}
    stream = outbound(build(f"vless://{UUID}@h?type=splithttp&security=none&path=%2Fx"))["streamSettings"]
    assert stream == {"network": "xhttp", "xhttpSettings": {"path": "/x", "host": "", "mode": "auto"}}


@pytest.mark.parametrize(
    "link",
    [
        "vmess://abc",
        f"vless://{UUID}@h:443?type=tcp&security=reality",  # нет pbk
        f"vless://{UUID}@h:443?type=kcp",
        f"vless://{UUID}@h:443?headerType=http",
        "vless://h:443",
    ],
)
def test_rejects_unsupported(link):
    with pytest.raises(ValueError):
        build(link)
