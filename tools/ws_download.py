# -*- coding: utf-8 -*-
"""
创意工坊 mod 下载器 —— 直接下到你自己的仓库, 完全不碰游戏目录

设计要点(为什么这么写):
  1) 不订阅、不经过游戏
     The Binding of Isaac 的工坊内容是新式 UGC(存在 Steam depot 里), 不是
     老式的 file_url 直链, 所以没法像 FireAxe 那样一行 HTTP 下载。正解是走
     SteamPipe: 用 WebAPI 拿到 hcontent_file(清单 GID), 再按 depot 从 CDN
     拉 manifest 和分块下载。本脚本复用 steamctl(python-steam 生态)的实现。

  2) 不需要 WebAPI key
     steamctl 自带的 workshop download 需要 WebAPI key(要去网页申请), 本脚本
     改用**公开的** ISteamRemoteStorage/GetPublishedFileDetails/v1 接口拿清单
     信息 —— 这个接口不需要 key, 所以主人少一步配置。

  3) 必须处理证书
     这台机器的网络环境 TLS 被中间人接管, 而 gevent 的 patch_ssl 会把证书
     加载搞坏(实测: patch_ssl 后连显式 cafile 都不认, 连 verify=False 都失败)。
     解决办法是 patch socket/select 但**不 patch ssl**, 并用合并了系统根证书的
     CA bundle。实测这条组合可用。

  4) 登录只需一次
     首次运行会提示输入 Steam 账号密码 + Steam Guard 验证码; 成功后
     steamctl 会把 login key 存到 %LOCALAPPDATA%\\steamctl\\steamctl\\client\\,
     以后直接复用, 不再要密码。

用法:
    python tools/ws_download.py <工坊ID或链接> [输出目录]
    python tools/ws_download.py 2900345009 "E:\\...\\isaac_mod_library"
    python tools/ws_download.py --user 账号 <ID> [输出目录]     # 指定账号
    python tools/ws_download.py --anon <ID>                     # 匿名(仅测试)
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request

_HERE0 = os.path.dirname(os.path.abspath(__file__))
if _HERE0 not in sys.path:
    sys.path.insert(0, _HERE0)
import steam_auth          # 现代登录(网页认证流程) + 本地会话

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CA_BUNDLE = os.path.join(ROOT, "certs", "ca_bundle.pem")

POPFILE_API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"


# ---------------------------------------------------------------- 环境准备
def prepare_tls():
    """把合并后的 CA bundle 交给进程使用, 并阻止 gevent 接管 ssl"""
    if os.path.isfile(CA_BUNDLE):
        # requests / urllib3 在"请求时"读这两个环境变量(必须放在 import 之前)
        os.environ["REQUESTS_CA_BUNDLE"] = CA_BUNDLE
        os.environ["CURL_CA_BUNDLE"] = CA_BUNDLE
        os.environ["SSL_CERT_FILE"] = CA_BUNDLE

    # steamctl 的模块在 import 时就会调用 gevent.monkey.patch_ssl(),
    # 那把 ssl 换掉后证书加载会失效, 所以这里先把它变成空操作。
    import gevent.monkey as _gm
    if not getattr(_gm.patch_ssl, "_imm_disabled", False):
        def _noop(*_a, **_k):
            return None
        _noop._imm_disabled = True
        _gm.patch_ssl = _noop


def parse_workshop_id(text):
    """从链接 / steam:// 协议串 / 纯数字里提取工坊 ID

    刻意**不做**「从任意文本里抓数字」的模糊猜测:
    那样会把页面里的 appid(250900) 误当成工坊 ID, 然后去下载一个不存在的东西。
    只认三种明确形式: 纯数字 / ?id=xxx / steam://…CommunityFilePage/xxx
    """
    text = (text or "").strip()
    if re.fullmatch(r"\d{4,12}", text):        # >12 位的是 SteamID64, 不是工坊 ID
        return text
    for pat in (r"[?&]id=(\d{6,12})",
                r"CommunityFilePage/(\d{6,12})"):
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)
    return None


def fetch_pubfile(ws_id):
    """用公开接口拿工坊条目详情(不需要 WebAPI key)

    注意: 公开接口 ISteamRemoteStorage/GetPublishedFileDetails 的字段名与
    steamctl 期望的 IPublishedFileService.GetDetails 不同
    (consumer_app_id vs consumer_appid, file_size 是字符串),
    这里统一归一化成 steamctl 的格式。
    """
    data = urllib.parse.urlencode(
        {"itemcount": 1, "publishedfileids[0]": ws_id}).encode()
    req = urllib.request.Request(POPFILE_API, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=25) as r:
        body = json.loads(r.read().decode("utf-8"))
    det = body["response"]["publishedfiledetails"][0]
    if det.get("result") != 1:
        raise RuntimeError("Steam 返回错误 result=%s (ID 可能不存在或已删除)"
                           % det.get("result"))

    # ---- 归一化字段 ----
    if "consumer_appid" not in det and det.get("consumer_app_id"):
        det["consumer_appid"] = int(det["consumer_app_id"])
    det.setdefault("consumer_appid", 0)
    det.setdefault("app_name", "")
    det.setdefault("filename", "")
    det["hcontent_file"] = str(det.get("hcontent_file") or "")
    try:
        det["file_size"] = int(det.get("file_size") or 0)
    except (TypeError, ValueError):
        det["file_size"] = 0
    return det


# ---------------------------------------------------------------- 登录
def block_interactive_input():
    """把「交互式提问」变成异常。

    为什么需要: 网页/后台调用时没有输入终端, 而老库在登录态失效时会用
    getpass() 提问 —— Windows 的 getpass 直接读控制台缓冲区, 连重定向 stdin
    都拦不住, 结果就是任务永久卡住。这里把提问入口直接换成抛错。
    """
    def _nope(*_a, **_k):
        raise RuntimeError("登录态需要重新验证, 但当前禁止交互输入。"
                           "请在管理器界面里点「登录 Steam」重新登录一次。")
    for modname in ("steam.client", "steamctl.clients"):
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        for attr in ("_cli_input", "getpass"):
            if hasattr(mod, attr):
                setattr(mod, attr, _nope)


def steam_login_key():
    """本地是否已有登录凭据 —— 有就不用再输密码。

    注意: 老库(steamctl)那套是把 login key 存在 %LOCALAPPDATA%/steamctl 下,
    但那条路要明文密码、现代 Steam 会拒; 现在改由 steam_auth 保存 refresh_token。
    """
    return steam_auth.logged_in_account()


# ---------------------------------------------------------------- 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="下载 Steam 创意工坊 mod 到指定目录(不碰游戏目录)")
    ap.add_argument("target", nargs="?", default=None,
                    help="工坊 ID 或完整链接")
    ap.add_argument("output", nargs="?", default=None,
                    help="输出目录(默认用管理器配置里的 mod 仓库)")
    ap.add_argument("--user", default=None, help="Steam 账号(留空则用上次的)")
    ap.add_argument("--password", default=None,
                    help="Steam 密码(不建议写在这里; 留空会安全地提示输入)")
    ap.add_argument("--anon", action="store_true", help="匿名登录(仅测试连通性)")
    ap.add_argument("--flat", action="store_true",
                    help="直接把内容下到目标目录(不建「标题_ID」子目录)")
    ap.add_argument("--no-prompt", action="store_true",
                    help="禁止任何交互式提问(网页/后台调用时用, 避免卡死)")
    ap.add_argument("--quiet", action="store_true", help="不显示进度条")
    ap.add_argument("-l", "--log-level", default="info",
                    choices=["quiet", "info", "debug"], help="日志级别")
    args0 = ap.parse_args(argv)

    # steamctl 内部的 INFO/DEBUG 日志靠 root logger 输出, 这里补上配置
    import logging
    logging.basicConfig(
        format="[%(levelname)s] %(name)s: %(message)s"
        if args0.log_level == "debug" else "[%(levelname)s] %(message)s",
        level=100 if args0.log_level == "quiet"
        else getattr(logging, args0.log_level.upper()))

    # steamctl 内部读的是 args.anonymous, 这里的旗标叫 --anon, 统一一下
    args0.anonymous = bool(args0.anon)

    prepare_tls()
    if args0.no_prompt:
        block_interactive_input()

    if not args0.target:
        print("[错误] 请给出工坊 ID 或链接。")
        print("       (登录请在管理器界面里点「登录 Steam」)")
        return 2

    ws_id = parse_workshop_id(args0.target)
    if not ws_id:
        print("[错误] 无法从 %r 里解析出工坊 ID" % args0.target)
        return 2

    # 前置校验: 没有登录态时绝不往下走 —— 否则底层会尝试交互式登录,
    # 在网页/后台调用(没有输入终端)的场景下会一直卡住。
    if not args0.anon and not args0.password:
        who = steam_login_key()
        if not who:
            print("[需要登录] 还没有 Steam 登录凭据, 无法下载。")
            print("          请在管理器界面里点「登录 Steam」按钮, 填一次账号密码即可。")
            return 4

    print("=" * 62)
    print("工坊 ID : %s" % ws_id)
    print("目标目录: %s" % os.path.abspath(default_output_dir()
                                           if not args0.output else args0.output))
    print("CA 证书 : %s" % (CA_BUNDLE if os.path.isfile(CA_BUNDLE) else "未找到(用系统默认)"))
    print("=" * 62)

    # 1) 查详情
    try:
        det = fetch_pubfile(ws_id)
    except Exception as exc:
        print("[错误] 查询工坊详情失败: %s" % exc)
        return 1

    title = (det.get("title") or "").strip()
    size = int(det.get("file_size") or 0)
    print("标题    : %s" % title)
    print("大小    : %.2f MB" % (size / 1048576) if size else "大小    : 未知")
    print("所属app : %s" % det.get("consumer_appid"))
    print()

    if not det.get("hcontent_file"):
        if det.get("file_url"):
            print("[说明] 这是老式直链内容, 用 steamctl ugc download 即可")
        else:
            print("[错误] 该条目没有可下载的清单(hcontent_file)")
        return 1

    # 2) 走 SteamPipe 下载(需要登录态才能取 depot key)
    try:
        from steamctl.commands.workshop.gcmds import download_via_steampipe
    except Exception as exc:
        print("[错误] 载入 steamctl 失败: %s" % exc)
        print("       请先: pip install steamctl")
        return 1

    # 让 steamctl 用「登录凭据(access_token)」登录 CM, 而不是明文密码 ——
    # 老库的密码是明文发的, 现代 Steam 会拒绝(正确密码也报 InvalidPassword)
    try:
        steam_auth.install_token_login()
    except Exception as exc:
        print("[警告] 登录方式替换失败(%s), 可能无法通过鉴权" % str(exc)[:80])

    # 输出目录:
    #   默认下到 "管理器的仓库/标题_ID" —— 这样下完 scan 一下就能被工具接管
    #   --flat 则直接下到指定目录(不建那层子目录)
    base_out = args0.output or default_output_dir()
    if args0.flat:
        out = os.path.abspath(base_out)
    else:
        safe_title = re.sub(r'[\\/:*?"<>|]+', "_", title).strip(" .") or ("ws_" + ws_id)
        out = os.path.abspath(os.path.join(base_out, "%s_%s" % (safe_title, ws_id)))
    os.makedirs(out, exist_ok=True)

    # 组装 steamctl 内部需要的参数对象
    class _Args(object):
        pass
    a = _Args()
    a.id = ws_id
    a.output = out
    a.no_directories = False      # 必须保留 mod 内部的子目录结构, 否则会装坏
    a.no_progress = bool(args0.quiet)
    a.cell_id = None
    a.anonymous = bool(args0.anon)
    a.user = args0.user
    a.password = args0.password

    print("开始下载到: %s" % out)
    print("-" * 62)
    try:
        rc = download_via_steampipe(a, det)
    except EOFError:
        print()
        print("[中断] 需要登录, 但当前没有可输入的终端。")
        print("       请在管理器界面里点「登录 Steam」登录一次。")
        return 3
    except KeyboardInterrupt:
        print("\n[取消] 已手动中断")
        return 3
    except Exception as exc:
        msg = str(exc)
        print("-" * 62)
        print("[失败] %s" % msg[:300])
        try:
            if os.path.isdir(out) and not os.listdir(out):
                os.rmdir(out)
                print("[清理] 已移除失败留下的空目录")
        except OSError:
            pass
        low = msg.lower()
        if "manifest code" in low or "(15)" in msg or "accessdenied" in low:
            print()
            print("=> 这是「没有下载权限」。原因通常是:")
            print("   1) 还没登录 Steam(匿名账号无权取该游戏的 depot key)")
            print("      解决: 在管理器界面里点「登录 Steam」登录一次")
            print("   2) 登录的账号没有拥有 The Binding of Isaac: Rebirth")
            print("      解决: 换成本体所在的账号")
        else:
            print("   可加 -l debug 看详细日志, 或先 --anon 测连通性")
        return 1
    print("-" * 62)
    if rc != 0:
        # 失败时别在仓库里留一个空文件夹, 会污染 mod 列表
        try:
            if os.path.isdir(out) and not os.listdir(out):
                os.rmdir(out)
                print("[清理] 已移除失败留下的空目录")
        except OSError:
            pass
        print("[失败] 下载返回码 %s" % rc)
        print("       若提示登录/拒绝访问, 请在界面里登录一次。")
        return int(rc) if isinstance(rc, int) else 1

    # 3) 报告结果
    total = 0
    files = 0
    for dp, _dn, fs in os.walk(out):
        for f in fs:
            files += 1
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    print("完成: %s" % title)
    print("输出目录: %s  (%d 个文件, %.2f MB)" % (out, files, total / 1048576))
    return 0


if __name__ == "__main__":
    sys.exit(main())
