# -*- coding: utf-8 -*-
"""
Steam 现代登录（IAuthenticationService 网页认证流程）

为什么不能用老办法:
    Python 的 steam / steamctl 走的是老式 CM 登录，密码字段是**明文**
    （CMsgClientLogon.password）。实测该库的 protobuf 里**根本没有
    encrypted_password 字段**，而现代 Steam 要求 RSA 加密的凭据，
    于是即使密码完全正确也会返回 EResult.InvalidPassword(5)。
    这就是「正确密码登不进去」的根因。

本模块改用 Steam 官网/客户端同款的网页认证流程:
    1. GetPasswordRSAPublicKey      拿 RSA 公钥 + 时间戳
    2. BeginAuthSessionViaCredentials 提交 RSA 加密后的密码
       -> 返回 client_id / request_id / steamid / allowed_confirmations
    3. 若需要验证码: UpdateAuthSessionWithSteamGuardCode
    4. PollAuthSessionStatus         -> refresh_token + access_token
    5. 以后用 access_token 登录 CM（CMsgClientLogon.access_token 这个字段是有的）

产物: refresh_token 存在本地（<LOCALAPPDATA>/isaac_mod_manager/steam_session.json），
      之后所有下载都不再需要密码。密码本身不落任何文件。
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request

API = "https://api.steampowered.com"
UA = "IsaacModManager/1.0"

# EAuthSessionGuardType
GUARD_NONE = 0
GUARD_EMAIL_CODE = 1
GUARD_DEVICE_CODE = 2          # 手机令牌里显示的 5 位码
GUARD_DEVICE_CONFIRM = 3       # 在手机 App 里点「批准」
GUARD_EMAIL_CONFIRM = 4
GUARD_MACHINE_TOKEN = 5

# 数据目录要和 isaac_mod_manager.py 里保持一致(那边是唯一真源)。
# 老版本用的是小写带下划线的 isaac_mod_manager, 这里读的时候回退一下,
# 免得这个脚本被单独调用时读不到已经登录过的凭据。
_DATA_BASE = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
SESSION_DIR = os.path.join(_DATA_BASE, "IsaacModManager")
_LEGACY_SESSION_DIR = os.path.join(_DATA_BASE, "isaac_mod_manager")


def _pick_session_dir():
    if os.path.isfile(os.path.join(SESSION_DIR, "steam_session.json")):
        return SESSION_DIR
    if os.path.isfile(os.path.join(_LEGACY_SESSION_DIR, "steam_session.json")):
        return _LEGACY_SESSION_DIR
    return SESSION_DIR


SESSION_FILE = os.path.join(_pick_session_dir(), "steam_session.json")
PENDING_FILE = os.path.join(SESSION_DIR, "steam_login_pending.json")


# ---------------------------------------------------------------- 基础请求
def _post(path, params, timeout=25):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(API + path, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(body).get("response", {})
        except ValueError:
            raise RuntimeError("Steam 返回异常 (HTTP %s)" % exc.code)
    try:
        return json.loads(body).get("response", {})
    except ValueError:
        raise RuntimeError("Steam 返回了无法解析的内容")


def _get(path, params, timeout=20):
    url = API + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace")).get("response", {})


def _rsa_encrypt(password, mod_hex, exp_hex):
    """用 Steam 给的公钥做 PKCS1v15 加密, 返回 base64"""
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa as _rsa, padding
        pub = _rsa.RSAPublicNumbers(int(exp_hex, 16), int(mod_hex, 16)).public_key()
        raw = pub.encrypt(password.encode("utf-8"), padding.PKCS1v15())
    except ImportError:
        # 退路: pycryptodome
        from Crypto.PublicKey import RSA
        from Crypto.Cipher import PKCS1_v1_5
        key = RSA.construct((int(mod_hex, 16), int(exp_hex, 16)))
        raw = PKCS1_v1_5.new(key).encrypt(password.encode("utf-8"))
    return base64.b64encode(raw).decode("ascii")


# ---------------------------------------------------------------- 本地会话
def load_session():
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return None


def save_session(data):
    os.makedirs(SESSION_DIR, exist_ok=True)
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1)
    os.replace(tmp, SESSION_FILE)


def clear_session():
    for p in (SESSION_FILE, PENDING_FILE):
        try:
            os.remove(p)
        except OSError:
            pass


def logged_in_account():
    """已登录则返回账号名, 否则 None"""
    s = load_session()
    if s and s.get("refresh_token"):
        return s.get("account") or "已登录"
    return None


def _save_pending(d):
    os.makedirs(SESSION_DIR, exist_ok=True)
    with open(PENDING_FILE, "w", encoding="utf-8") as fp:
        json.dump(d, fp, ensure_ascii=False)


def _load_pending():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return None


# ---------------------------------------------------------------- 登录流程
def _poll(client_id, request_id, tries=1, interval=1.0):
    """轮询登录结果; 拿到 refresh_token 就返回"""
    last = {}
    for _ in range(max(1, tries)):
        last = _post("/IAuthenticationService/PollAuthSessionStatus/v1/", {
            "client_id": client_id,
            "request_id": request_id,
        })
        if last.get("refresh_token"):
            return last
        time.sleep(interval)
    return last


def _finish(account, resp):
    """把 poll 的结果落盘成会话"""
    rt = resp.get("refresh_token") or ""
    at = resp.get("access_token") or ""
    if not rt:
        return {"status": "error", "error": "Steam 没有下发登录凭据(可能还没在手机上确认)"}
    save_session({
        "account": resp.get("account_name") or account,
        "refresh_token": rt,
        "access_token": at,
        "obtained_at": int(time.time()),
    })
    try:
        os.remove(PENDING_FILE)
    except OSError:
        pass
    return {"status": "ok", "account": account}


def pick_mode(confirmations):
    """从 allowed_confirmations 里挑一个我们能做到的验证方式"""
    types = []
    for c in confirmations or []:
        t = c.get("confirmation_type")
        if isinstance(t, int):
            types.append(t)
    if GUARD_DEVICE_CODE in types:
        return "device", types          # 手输手机令牌里的码
    if GUARD_EMAIL_CODE in types:
        return "email", types           # 手输邮箱里的码
    if GUARD_DEVICE_CONFIRM in types:
        return "confirm", types         # 手机上点「批准」, 不用输码
    if GUARD_MACHINE_TOKEN in types:
        return "machine", types
    return "", types


def begin_login(user, password):
    """第一步: 提交账号密码"""
    user = (user or "").strip()
    if not user or not password:
        return {"status": "error", "error": "账号和密码都要填"}

    try:
        key = _get("/IAuthenticationService/GetPasswordRSAPublicKey/v1/",
                   {"account_name": user})
    except Exception as exc:
        return {"status": "error", "error": "无法连接 Steam: %s" % str(exc)[:120]}
    if not key.get("publickey_mod"):
        return {"status": "error", "error": "Steam 没有返回 RSA 公钥, 请稍后重试"}

    try:
        enc = _rsa_encrypt(password, key["publickey_mod"], key["publickey_exp"])
    except Exception as exc:
        return {"status": "error", "error": "加密密码失败: %s" % str(exc)[:120]}

    try:
        resp = _post("/IAuthenticationService/BeginAuthSessionViaCredentials/v1/", {
            "account_name": user,
            "encrypted_password": enc,
            "encryption_timestamp": key.get("timestamp", int(time.time())),
            "remember_login": "true",
            "persistence": "1",
            "website_id": "Community",
            "device_details.device_friendly_name": "IsaacModManager",
            "device_details.platform_type": "2",
            "device_details.os_type": "-1",
            "device_details.gaming_device_type": "1",
        })
    except Exception as exc:
        return {"status": "error", "error": "提交登录失败: %s" % str(exc)[:120]}

    client_id = resp.get("client_id")
    request_id = resp.get("request_id")
    if not client_id or not request_id:
        # 账号密码不对时: 老账号会回 interval(限流提示), 不存在/被拒会回空
        interval = resp.get("interval")
        msg = resp.get("extended_error_message") or ""
        if interval:
            return {"status": "error",
                    "error": "账号或密码不对（Steam 限流提示 %s 秒后再试）" % interval}
        if msg:
            return {"status": "error", "error": msg}
        return {"status": "error",
                "error": "账号或密码不对，或该账号名在 Steam 不存在"}

    mode, types = pick_mode(resp.get("allowed_confirmations"))
    steamid = resp.get("steamid")
    pending = {"account": user, "client_id": client_id, "request_id": request_id,
               "steamid": steamid, "mode": mode, "types": types}
    _save_pending(pending)

    if mode in ("", "machine"):
        # 不需要额外验证 —— 直接拿结果
        p = _poll(client_id, request_id, tries=2)
        return _finish(user, p)

    return {"status": "code", "mode": mode, "types": types, "account": user,
            "steamid": steamid}


def submit_code(code):
    """第二步: 提交验证码(或确认已在手机批准)"""
    code = (code or "").strip()
    p = _load_pending()
    if not p:
        return {"status": "error", "error": "登录会话已过期，请重新输入账号密码"}
    client_id, request_id = p["client_id"], p["request_id"]
    mode = p.get("mode") or "device"

    if code:
        ctype = GUARD_DEVICE_CODE if mode == "device" else GUARD_EMAIL_CODE
        try:
            _post("/IAuthenticationService/UpdateAuthSessionWithSteamGuardCode/v1/", {
                "client_id": client_id,
                "steamid": p.get("steamid"),
                "code": code,
                "code_type": ctype,
            })
        except Exception as exc:
            return {"status": "error", "error": "提交验证码失败: %s" % str(exc)[:120]}

    # 验证码正确也要再 poll 一次才会下发 token
    resp = _poll(client_id, request_id, tries=3, interval=1.5)
    if not resp.get("refresh_token"):
        if code:
            return {"status": "code", "mode": mode, "types": p.get("types"),
                    "account": p.get("account"),
                    "error": "验证码不对或已过期，请重新输入"}
        return {"status": "confirm_wait", "account": p.get("account"),
                "error": "还没收到确认，请在手机 Steam 里点「批准」"}
    return _finish(p.get("account") or "", resp)


def poll_only():
    """手机批准模式: 只轮询, 不再提交验证码"""
    p = _load_pending()
    if not p:
        return {"status": "error", "error": "登录会话已过期，请重新输入账号密码"}
    resp = _poll(p["client_id"], p["request_id"], tries=2, interval=1.5)
    if resp.get("refresh_token"):
        return _finish(p.get("account") or "", resp)
    return {"status": "confirm_wait", "account": p.get("account"),
            "error": "还没收到确认，请在手机 Steam 里点「批准」"}


# ---------------------------------------------------------------- 给下载用
def access_token():
    """拿一个可用的 access_token（优先用会话里现成的）"""
    s = load_session()
    if not s:
        return None
    at = s.get("access_token")
    if at:
        return at
    rt = s.get("refresh_token")
    if not rt:
        return None
    resp = _post("/IAuthenticationService/GenerateAccessTokenForApp/v1/",
                 {"refresh_token": rt, "steamid": s.get("account")})
    at = resp.get("access_token")
    if at:
        s["access_token"] = at
        save_session(s)
    return at


def cm_login(client, username, token):
    """让 SteamClient 用 access_token 登录 CM（而不是明文密码）

    steam 库里没有现成的入口, 所以包一层 send: 截获 ClientLogon 消息,
    塞进 access_token 字段(这个字段 proto 里是有的)并清空 password。
    """
    from steam.enums.emsg import EMsg
    orig_send = client.send

    def send(message):
        try:
            if message.msg_type == EMsg.ClientLogon:
                message.body.access_token = token
                message.body.password = ""
        except Exception:
            pass
        return orig_send(message)

    client.send = send
    return client.login(username or "", password="")


def install_token_login():
    """把 steamctl 的 login_from_args 换成「用 access_token 登录」

    download_via_steampipe() 内部会自己 new 一个 CachingSteamClient 并调
    login_from_args, 所以这里从根上替换掉它 —— 这样下载流程完全不用改。
    """
    from steamctl import clients as sclients
    from steamctl.utils.storage import UserDataFile

    def login_from_args(self, args, print_status=True):
        from steam.enums import EResult
        s = load_session()
        if not s:
            raise RuntimeError("还没有登录 Steam，请先在界面里登录一次")
        user = s.get("account") or (getattr(args, "user", None) or "")
        self.username = user
        if not getattr(args, "anonymous", False):
            tok = access_token()
            if not tok:
                raise RuntimeError("登录凭据已失效，请在界面里重新登录一次")
            res = cm_login(self, user, tok)
            try:
                if int(res) != int(EResult.OK) and not self.logged_on:
                    raise RuntimeError("用登录凭据连接 Steam 失败 (结果 %s)" % res)
            except TypeError:
                pass
            try:
                UserDataFile("client/lastuser").write_text(user)
            except Exception:
                pass
            return res
        return self.anonymous_login()

    sclients.CachingSteamClient.login_from_args = login_from_args
