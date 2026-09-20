# -*- coding: utf-8 -*-
"""
生成合并 CA 证书包: certifi 内置证书 + Windows 系统证书(ROOT/CA 存储区)

为什么需要:
    这台机器的网络环境中 TLS 被中间人代理接管(有自签根证书)。
    纯 Python 的 requests 默认只用 certifi 内置的证书列表 -> 报
    CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate。

    而 steamctl 依赖 gevent, 它在 import 时会 monkey-patch ssl,
    把 pip-system-certs 打的补丁顶掉, 所以"装个证书补丁包"不管用。
    最稳的办法是提前准备一份包含系统根证书的 CA bundle, 让进程通过
    SSL_CERT_FILE / REQUESTS_CA_BUNDLE 环境变量使用它。

用法:
    python tools/make_ca_bundle.py
    然后设置环境变量(见 tools/run_with_ca.bat):
        SSL_CERT_FILE        = <项目>\certs\ca_bundle.pem
        REQUESTS_CA_BUNDLE   = <项目>\certs\ca_bundle.pem
"""
import base64
import os
import ssl
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "certs")
OUT = os.path.join(OUT_DIR, "ca_bundle.pem")


def der_to_pem(der):
    b64 = base64.b64encode(der).decode("ascii")
    body = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return "-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n" % body


def windows_certs():
    """读取 Windows 证书存储区, 返回 PEM 文本列表"""
    out = []
    for store in ("ROOT", "CA"):
        try:
            entries = ssl.enum_certificates(store)
        except Exception as exc:                      # 非 Windows / 权限不足
            print("  跳过 %s: %s" % (store, exc))
            continue
        n = 0
        for cert_bytes, encoding, _trust in entries:
            # 注意: Windows 上 encoding 是 'x509_asn' 而不是 'x509'
            if encoding not in ("x509_asn", "x509"):  # 只要 DER 编码的标准证书
                continue
            try:
                out.append(der_to_pem(cert_bytes))
                n += 1
            except Exception:
                pass
        print("  %s 存储区: %d 张证书" % (store, n))
    return out


def main():
    print("=== 生成合并 CA 证书包 ===")
    parts = []
    # 1) certifi 内置(保证公网站点依然可用)
    try:
        import certifi
        with open(certifi.where(), "r", encoding="utf-8") as fp:
            text = fp.read()
        parts.append(text)
        print("  certifi: %d 字节 (%s)" % (len(text), certifi.__version__))
    except Exception as exc:
        print("  certifi 读取失败:", exc)

    # 2) Windows 系统证书(包含网络环境的中间人根证书)
    for pem in windows_certs():
        parts.append(pem)

    blob = "".join(p if p.endswith("\n") else p + "\n" for p in parts)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        fp.write(blob)

    count = blob.count("BEGIN CERTIFICATE")
    print()
    print("已写出: %s" % OUT)
    print("共 %d 张证书, %.1f KB" % (count, len(blob) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
