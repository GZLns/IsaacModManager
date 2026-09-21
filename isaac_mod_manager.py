# -*- coding: utf-8 -*-
"""
以撒的结合:重生 Mod 管理器 —— Web UI 版
==========================================================
机制 (与 FireAxe 同原理, 完全保留):
  - mod 实体文件夹统一放「仓库」(默认 游戏根目录/isaac_mod_library)
  - 启用 = 在游戏 mods/ 里创建目录联接 junction;  禁用 = 只删联接, 实体永不挪动
  - 分组 + 优先级 + 「仅启用此组」一键在 忏悔 / 忏悔+ 之间切换
  - 「一键同步」按状态重建链接并清理失效链接
本文件只负责后端与 API; 界面在 web/ 目录 (HTML + CSS + JS), 自带动画与视差。
启动: python isaac_mod_manager.py            (自动开浏览器)
      python isaac_mod_manager.py --no-browser --port 8800
      python isaac_mod_manager.py --config D:\\some\\other_config.json
"""

import argparse
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import xml.etree.ElementTree as ET
import zipfile

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    # 打包成 exe: 只读资源 (web/assets/tools/certs) 在 PyInstaller 解压目录 _MEIPASS 里;
    # 可写数据 (配置/日志/凭据/缓存) 一律放「用户数据目录」, 见 user_data_dir()
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR


def _res_dir(name):
    """资源目录解析: 优先用「exe/脚本旁边」的同名目录, 否则回落到打包内部。

    这样做的意义: 主人想改前端 (CSS/JS) 时, 只要把 web/ 放在 exe 旁边即可热改,
    不需要重新打包; 没放就用打包进去的那份, 保证单文件也能跑。
    """
    beside = os.path.join(APP_DIR, name)
    if os.path.isdir(beside):
        return beside
    return os.path.join(BUNDLE_DIR, name)


WEB_DIR = _res_dir("web")
ASSETS_DIR = _res_dir("assets")
TOOLS_DIR = _res_dir("tools")       # 工坊下载脚本 + 一键登录 bat
CERTS_DIR = _res_dir("certs")       # 合并了系统根证书的 CA bundle
# ======================================================================
#  用户数据目录 (配置 / 日志 / 凭据 / 缓存)
# ======================================================================
# 为什么不在程序旁边: 以前配置写在 exe 旁边, 换个新版本(重新打包会把 dist 清空)
# 或换个目录就丢分组 —— 踩过。放到用户目录后, exe 随便替换/移动, 数据都还在。
USER_DIR_NAME = "IsaacModManager"          # %LOCALAPPDATA%\IsaacModManager
CONFIG_FILE_NAME = "config.json"
LEGACY_CONFIG_NAMES = ("isaac_mod_manager_config.json",)
GAME_DIR_NAME = "The Binding of Isaac Rebirth"


def user_data_dir():
    """用户数据目录 (Windows: %LOCALAPPDATA%\IsaacModManager)"""
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or os.path.expanduser("~"))
    d = os.path.join(base, USER_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return APP_DIR            # 实在没权限就退回程序旁边, 至少还能用
    return d


def _migrate_legacy_user_dir():
    """老版本的数据目录是小写带下划线的 %LOCALAPPDATA%\\isaac_mod_manager, 新版统一成
    IsaacModManager —— 首次运行时把老目录里的东西复制过来(老目录保留不动)。

    这一步很关键: Steam 登录凭据(steam_session.json)和缩略图缓存都在那儿,
    不搬的话用户要重新登录一次。
    """
    if os.path.basename(os.path.normcase(USER_DIR)) != os.path.normcase(USER_DIR_NAME):
        return []                      # 回落到了程序目录, 不做搬运
    old = os.path.join(os.path.dirname(USER_DIR), "isaac_mod_manager")
    if os.path.normcase(old) == os.path.normcase(USER_DIR) or not os.path.isdir(old):
        return []
    moved = []
    for name in ("config.json", "steam_session.json", "steam_login_pending.json",
                 "isaac_mod_manager_config.json"):
        src, dst = os.path.join(old, name), os.path.join(USER_DIR, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
                moved.append(name)
            except OSError:
                pass
    src_d, dst_d = os.path.join(old, "thumbs"), os.path.join(USER_DIR, "thumbs")
    if os.path.isdir(src_d) and not os.path.isdir(dst_d):
        try:
            shutil.copytree(src_d, dst_d)
            moved.append("thumbs/")
        except OSError:
            pass
    return moved


USER_DIR = user_data_dir()
MIGRATED_USER_FILES = _migrate_legacy_user_dir()      # 老数据目录首次搬运(只做一次)
CONFIG_PATH = os.path.join(USER_DIR, CONFIG_FILE_NAME)
LOG_PATH = os.path.join(USER_DIR, "isaac_mod_manager.log")


def _legacy_config_candidates():
    """老版本把配置写在「程序/exe 旁边」, 这些是迁移来源。

    多个来源时按修改时间倒序 —— 取最新的那份, 避免把旧的覆盖掉新的
    (实测确实踩到: 项目根那份是 14:53 的, dist 旁边那份是 19:28 的, 分组还不一样)。
    """
    out = []
    for d in (APP_DIR, BUNDLE_DIR, os.getcwd()):
        for name in LEGACY_CONFIG_NAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p) and p not in out:
                out.append(p)
    out.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return out


def migrate_legacy_config(target=None):
    """把老配置迁到用户数据目录 (只在目标还不存在时做一次)。

    返回迁移来源路径; 没迁移则返回 None。
    老文件原样保留不动 —— 万一回退到老版本 exe, 那边也还能用。
    """
    target = target or CONFIG_PATH
    if os.path.exists(target):
        return None
    for src in _legacy_config_candidates():          # 已按 mtime 倒序 = 最新的优先
        if os.path.abspath(src) == os.path.abspath(target):
            continue
        try:
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            shutil.copy2(src, target)
            return src
        except OSError:
            pass
    return None


def resolve_config_path(explicit=None, portable=False):
    """决定这次用哪个配置文件。

    优先级: 显式 --config > 便携模式(程序旁边有 config.json 或 --portable)
            > 用户数据目录 (顺带做一次老配置迁移)
    """
    if explicit:
        return explicit
    beside = os.path.join(APP_DIR, CONFIG_FILE_NAME)
    if portable or os.path.isfile(beside):
        return beside
    migrate_legacy_config(CONFIG_PATH)
    return CONFIG_PATH


APP_VERSION = "2.0.1"               # 与 GitHub Release 的 tag 对应
APP_REPO = "GZLns/IsaacModManager"

APP_TAG = "isaac-mod-manager"       # 单实例探测用的标识
LIBRARY_DIR_NAME = "isaac_mod_library"
LEGACY_DISABLED = "mods_disabled"          # 旧移动式方案留下的目录, 会被自动识别


def guess_mods_path():
    """首次运行自动找游戏的 mods 目录 (注册表拿 Steam 位置 + 各库位置一起试)

    找不到就返回空串, 由用户在设置里自己填。
    """
    rel = os.path.join("steamapps", "common", GAME_DIR_NAME, "mods")
    roots = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for val in ("SteamPath", "InstallPath"):
                        try:
                            roots.append(winreg.QueryValueEx(k, val)[0])
                        except OSError:
                            pass
            except OSError:
                pass
    except Exception:
        pass
    # libraryfolders.vdf 会列出所有 Steam 库(包括装在别的盘的)
    for r in list(roots):
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if not os.path.isfile(vdf):
            continue
        try:
            with open(vdf, "r", encoding="utf-8", errors="replace") as f:
                roots += [m.replace("\\\\", "\\")
                          for m in re.findall(r'"path"\s*"([^"]+)"', f.read())]
        except OSError:
            pass
    seen = set()
    for r in roots:
        p = os.path.normpath(os.path.join(r, rel))     # 统一成 反斜杠 + 大写盘符
        if len(p) > 1 and p[1] == ":":
            p = p[0].upper() + p[1:]
        k = os.path.normcase(p)
        if k in seen:
            continue
        seen.add(k)
        if os.path.isdir(p):
            return p
    return ""


DEFAULT_CONFIG = {
    "mods_path": "",           # 留空时首次运行会自动探测(guess_mods_path)
    "library_path": "",
    "show_disabled": True,
    "bg_enabled": True,
    "bg_opacity": 0.5,
    "card_opacity": 0.4,
    "groups": [],
}

ST_ENABLED, ST_DISABLED, ST_UNIMPORTED = "enabled", "disabled", "unimported"


# ======================================================================
#  基础工具
# ======================================================================
_CACHE = None


def cache():
    """本地缓存(SQLite) 单例 —— 工坊元数据 / 文件指纹 / 加载顺序 / 限流。

    放在用户数据目录里, 与配置同级; 换新版本 exe 不会影响它。
    """
    global _CACHE
    if _CACHE is None:
        import modcache
        _CACHE = modcache.get_cache(USER_DIR)
    return _CACHE


def load_config(path=CONFIG_PATH):
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, json.loads(json.dumps(v)))
    if not cfg.get("mods_path"):
        cfg["mods_path"] = guess_mods_path()      # 首次运行: 自动找一下
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ============================================================
#  Steam 创意工坊
# ============================================================
STEAM_APPID = "250900"        # The Binding of Isaac: Rebirth
STEAM_WS_API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
STEAM_WS_PAGE = "https://steamcommunity.com/sharedfiles/filedetails/?id="


def parse_workshop_id(text):
    """从剪贴板文本里解析创意工坊 ID。

    支持这几种粘贴内容:
      https://steamcommunity.com/sharedfiles/filedetails/?id=2900345009
      https://steamcommunity.com/workshop/filedetails/?id=2900345009
      steam://url/CommunityFilePage/2900345009
      2900345009
    """
    if not text:
        return None
    text = str(text).strip()
    m = re.search(r"[?&]id=(\d{6,12})", text)
    if m:
        return m.group(1)
    m = re.search(r"CommunityFilePage/(\d{6,12})", text)
    if m:
        return m.group(1)
    m = re.fullmatch(r"(\d{6,12})", text)
    if m:
        return m.group(1)
    return None


def dir_size(path):
    """目录总字节数 (拿不到的文件跳过, 不抛错)"""
    total = 0
    for dp, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def fetch_workshop_info(ids, timeout=15, max_age=10800, use_cache=True):
    """调 Steam Web API 批量查工坊条目信息 (公开数据, 不需要登录)

    返回 {id: {id,title,size,updated,result,url,cached}}

    - **先查本地缓存**(默认 3 小时内视为新鲜), 只对缺失/过期的 id 发请求
    - 发请求前过一遍**滑动窗口限流**(180 次 / 300 秒), 被限流就只用缓存
    - `result=9`(条目不存在/已删除) 会记成永久失效, 以后不再重试
    """
    ids = [str(i) for i in (ids or []) if i]
    if not ids:
        return {}
    out = {}
    todo = list(ids)
    if use_cache:
        try:
            c = cache()
            dead = c.dead_ids()
            hit = c.get_workshop(ids, max_age=max_age)
            for pid, it in hit.items():
                out[pid] = {"id": pid, "title": it["title"], "size": it["size"],
                            "updated": it["updated"], "result": 1,
                            "url": STEAM_WS_PAGE + pid, "cached": True,
                            "preview": it.get("preview", ""),
                            "description": it.get("description", "")}
            todo = [i for i in ids if i not in out and i not in dead]
        except Exception:
            todo = list(ids)

    fresh = {}
    if todo:
        allowed = True
        try:
            allowed = cache().rate_allow()
        except Exception:
            allowed = True
        if allowed:
            BATCH = 50
            for i in range(0, len(todo), BATCH):
                chunk = todo[i:i + BATCH]
                data = {"itemcount": len(chunk)}
                for j, pid in enumerate(chunk):
                    data["publishedfileids[%d]" % j] = pid
                body = urllib.parse.urlencode(data).encode()
                req = urllib.request.Request(STEAM_WS_API, data=body)
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        raw = json.loads(r.read().decode("utf-8"))
                except Exception as e:
                    if not out:
                        raise RuntimeError("查询 Steam 失败(检查网络): %s" % e)
                    break
                for d in raw.get("response", {}).get("publishedfiledetails", []):
                    pid = str(d.get("publishedfileid") or "")
                    if not pid:
                        continue
                    res = d.get("result")
                    fresh[pid] = {
                        "id": pid,
                        "title": d.get("title") or "",
                        "preview": d.get("preview_url") or "",
                        "description": d.get("short_description") or "",
                        "created": int(d.get("time_created") or 0),
                        "size": int(d.get("file_size") or 0),
                        "updated": int(d.get("time_updated") or 0),
                        "result": res,
                        "url": STEAM_WS_PAGE + pid,
                        "cached": False,
                    }
            for pid, it in fresh.items():
                out[pid] = it
            if fresh or todo:
                try:
                    c = cache()
                    good = {k: v for k, v in fresh.items() if v.get("result") == 1}
                    if good:
                        c.put_workshop(good)
                    for pid in todo:
                        it = fresh.get(pid)
                        if it is not None and it.get("result") == 9:
                            c.mark_dead(pid)
                except Exception:
                    pass
    return out


def _ver_tuple(v):
    """"2.0.0" -> (2, 0, 0); 用来比较版本大小(字符串比较会出错)"""
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def check_self_update(timeout=12):
    """查 GitHub 上有没有新版本(公开仓库, 不需要 token)"""
    url = "https://api.github.com/repos/%s/releases/latest" % APP_REPO
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "IsaacModManager/" + APP_VERSION)
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "current": APP_VERSION,
                "error": "查询失败(检查网络): %s" % str(e)[:140]}
    latest = str(d.get("tag_name") or "").lstrip("vV")
    return {
        "ok": True,
        "current": APP_VERSION,
        "latest": latest,
        "newer": _ver_tuple(latest) > _ver_tuple(APP_VERSION),
        "url": d.get("html_url") or "",
        "published": (d.get("published_at") or "")[:10],
        "notes": (d.get("body") or "")[:1500],
        "assets": [{"name": a.get("name"), "size": a.get("size"),
                    "url": a.get("browser_download_url")}
                   for a in (d.get("assets") or [])],
    }


STRIP_ID_FILE = ".imm_workshop_id"      # 原工坊 ID 存在这(我们自己读, 游戏不认)
_ID_RE = re.compile(r"<id>\s*([^<]*?)\s*</id>")


def saved_workshop_id(mod_path):
    """读我们记下来的原工坊 ID"""
    f = os.path.join(mod_path, STRIP_ID_FILE)
    if os.path.isfile(f):
        try:
            with io.open(f, encoding="utf-8") as fp:
                return fp.read().strip()
        except OSError:
            pass
    return ""


def strip_workshop_id(mod_path, mode="zero"):
    """把 mod 的工坊 ID 摘掉, 让它对游戏/Steam 变成"本地 mod"。

    背景: mods/ 是 Steam 工坊 mod 的托管区, 游戏启动时按订阅状态同步 ——
    取消订阅后即使在本管理器里启用, 对应的目录也会被清掉(实测)。
    摘掉 id 之后游戏不认它是工坊 mod, 就不会动它。

    原 id 记进 `.imm_workshop_id`(管理器自己用), metadata.xml 里按 mode 处理:
      mode="zero"   → 写成 <id>0</id>   (保留结构, 最不容易破坏解析)
      mode="remove" → 整行删掉

    返回被摘下来的原 id; 无需处理时返回 None。
    """
    meta = os.path.join(mod_path, "metadata.xml")
    if not os.path.isfile(meta):
        return None
    try:
        with io.open(meta, encoding="utf-8", errors="replace") as fp:
            txt = fp.read()
    except OSError:
        return None
    m = _ID_RE.search(txt)
    if not m:
        return None
    cur = m.group(1).strip()
    if not cur or cur == "0":
        return None                      # 已经处理过
    try:
        with io.open(os.path.join(mod_path, STRIP_ID_FILE), "w", encoding="utf-8") as fp:
            fp.write(cur)
    except OSError:
        return None
    if mode == "remove":
        txt = re.sub(r"\s*<id>\s*[^<]*?\s*</id>", "", txt, count=1)
    else:
        txt = _ID_RE.sub("<id>0</id>", txt, count=1)
    try:
        with io.open(meta, "w", encoding="utf-8") as fp:
            fp.write(txt)
    except OSError:
        return None
    return cur


def restore_workshop_id(mod_path, mode="zero"):
    """还原工坊 ID(想恢复成"官方订阅态"时用)"""
    orig = saved_workshop_id(mod_path)
    if not orig:
        return None
    meta = os.path.join(mod_path, "metadata.xml")
    if not os.path.isfile(meta):
        return None
    try:
        with io.open(meta, encoding="utf-8", errors="replace") as fp:
            txt = fp.read()
    except OSError:
        return None
    if _ID_RE.search(txt):
        txt = _ID_RE.sub("<id>%s</id>" % orig, txt, count=1)
    else:                                # 之前是删行, 得补回去(放在 <directory> 后面)
        m = re.search(r"(<directory>[^<]*</directory>)", txt)
        if m:
            txt = txt[:m.end()] + "\n    <id>%s</id>" % orig + txt[m.end():]
        else:
            return None
    try:
        with io.open(meta, "w", encoding="utf-8") as fp:
            fp.write(txt)
    except OSError:
        return None
    return orig


def read_metadata(mod_path):
    info = {"name": "", "version": "", "id": "", "description": ""}
    meta = os.path.join(mod_path, "metadata.xml")
    if os.path.exists(meta):
        try:
            root = ET.parse(meta).getroot()
            for tag in info:
                node = root.find(tag)
                if node is not None and node.text:
                    info[tag] = node.text.strip()
        except Exception:
            pass
    if not info["name"]:
        info["name"] = os.path.basename(mod_path)
    if not info["id"] or info["id"] == "0":
        # "去工坊化"过的 mod: metadata 里的 id 被写成 0(或删掉), 真实的 id 在
        # .imm_workshop_id 里 —— 管理器这边要照旧认得它是哪个工坊 mod
        info["id"] = saved_workshop_id(mod_path)
    return info


def is_junction(path):
    """是否为联接/符号链接; 用 lstat 读链接自身属性, 失效链接也能识别"""
    try:
        import stat as _stat
        return bool(os.lstat(path).st_file_attributes & _stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        pass
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return attrs != -1 and bool(attrs & 0x400)
    except Exception:
        return False


def create_junction(link, target):
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
        return
    except Exception as first:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                           capture_output=True, encoding="mbcs", errors="replace")
        if r.returncode != 0:
            detail = ((r.stdout or "") + (r.stderr or "")).strip()
            raise RuntimeError(detail or ("创建联接失败: %s" % first))


def remove_junction(link):
    """只删除联接本身, 绝不触碰实体"""
    if not is_junction(link):
        raise RuntimeError("不是联接, 拒绝删除: %s" % link)
    os.rmdir(link)


def junction_raw_target(link):
    try:
        t = os.readlink(link)
        return t[4:] if t.startswith("\\\\?\\") else t
    except Exception:
        try:
            return os.path.realpath(link)
        except Exception:
            return None


def _norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def short_path(p, head=16, tail=22):
    if not p or len(p) <= head + tail + 1:
        return p
    return p[:head] + "…" + p[-tail:]


# ======================================================================
#  核心逻辑 (与原版完全一致, 只是去掉了界面)
# ======================================================================
class ModLibrary:
    ISAAC_APPID = "250900"          # Steam 上 The Binding of Isaac: Rebirth 的 AppID

    def __init__(self, cfg=None, config_path=CONFIG_PATH):
        self.config_path = config_path
        self.dry_run = False        # 测试用: 不真正打开资源管理器/启动游戏
        self.cfg = cfg if cfg is not None else load_config(config_path)
        self.mods = {}          # dirname -> {name, version, id, state, dead_link?, link}
        self.dir_to_group = {}
        self.logs = []
        self.conflict_map = {}       # dirname -> [被多个 mod 同时提供的相对路径]
        self.conflict_detail = []    # 冲突清单(按涉及 mod 数倒序)
        self.update_map = {}         # dirname -> 工坊有更新的信息
        self._conflict_sig = None    # 上次冲突扫描时的状态签名(没变就复用结果)
        self._conflict_ts = 0.0
        self.scan()

    # ---------- 路径 ----------
    @property
    def mods_path(self):
        return self.cfg["mods_path"]

    @property
    def library_path(self):
        p = self.cfg.get("library_path") or ""
        return p or os.path.join(os.path.dirname(self.mods_path), LIBRARY_DIR_NAME)

    def save(self):
        save_config(self.cfg, self.config_path)

    def log(self, msg):
        self.logs.append(msg)
        del self.logs[:-40]

    # ---------- 扫描 ----------
    def scan(self):
        self.mods = {}
        lib_n = _norm(self.library_path)
        if os.path.isdir(self.library_path):
            for entry in sorted(os.listdir(self.library_path)):
                p = os.path.join(self.library_path, entry)
                if os.path.isdir(p) and not is_junction(p):
                    info = read_metadata(p)
                    self.mods[entry] = {"name": info["name"], "version": info["version"],
                                        "id": info["id"], "state": ST_DISABLED}
        if os.path.isdir(self.mods_path):
            for entry in sorted(os.listdir(self.mods_path)):
                p = os.path.join(self.mods_path, entry)
                if not os.path.isdir(p) and not is_junction(p):
                    continue
                if is_junction(p):
                    raw = junction_raw_target(p)
                    if raw and _norm(raw).startswith(lib_n + os.sep):
                        # 链接名可能带排序前缀(如 "003_xxx"), 所以用"指向的仓库目录名"当键,
                        # 这样自动排序改了链接名也不会让 mod 从列表里消失
                        tgt = os.path.basename(os.path.normpath(raw))
                        key = tgt if tgt in self.mods else entry
                        if key in self.mods:
                            self.mods[key]["state"] = ST_ENABLED
                            self.mods[key]["link"] = entry
                        elif not os.path.isdir(p):
                            self.mods[key] = {"name": entry, "version": "", "id": "",
                                              "state": ST_DISABLED, "dead_link": True}
                elif entry not in self.mods:
                    info = read_metadata(p)
                    self.mods[entry] = {"name": info["name"], "version": info["version"],
                                        "id": info["id"], "state": ST_UNIMPORTED}
        legacy = os.path.join(os.path.dirname(self.mods_path), LEGACY_DISABLED)
        if os.path.isdir(legacy):
            for entry in sorted(os.listdir(legacy)):
                p = os.path.join(legacy, entry)
                if os.path.isdir(p) and not is_junction(p) and entry not in self.mods:
                    info = read_metadata(p)
                    self.mods[entry] = {"name": info["name"], "version": info["version"],
                                        "id": info["id"], "state": ST_DISABLED}
        # 组里已不存在的 mod 清理掉
        self.dir_to_group = {}
        for g in self.cfg["groups"]:
            g["mods"] = [d for d in g["mods"] if d in self.mods]
            for d in g["mods"]:
                self.dir_to_group[d] = g["name"]

    # ---------- 状态输出 ----------
    def state(self):
        self.ensure_conflicts()
        stats = {"total": len(self.mods), "enabled": 0, "disabled": 0, "unimported": 0,
                 "selected_ratio": 0}
        for m in self.mods.values():
            if m["state"] == ST_ENABLED:
                stats["enabled"] += 1
            elif m["state"] == ST_DISABLED:
                stats["disabled"] += 1
            else:
                stats["unimported"] += 1
        mods = []
        for d, m in self.mods.items():
            mods.append({"dir": d, "name": m["name"], "version": m["version"],
                         "id": m.get("id", ""), "state": m["state"],
                         "dead_link": bool(m.get("dead_link")),
                         "group": self.dir_to_group.get(d, ""),
                         "link": m.get("link") or d,
                         "conflicts": len(self.conflict_map.get(d) or [])})
        groups = []
        for i, g in enumerate(self.cfg["groups"]):
            en = sum(1 for d in g["mods"] if self.mods.get(d, {}).get("state") == ST_ENABLED)
            groups.append({"name": g["name"], "priority": i, "mods": list(g["mods"]),
                           "enabled": en, "total": len(g["mods"])})
        return {
            "mods": mods,
            "groups": groups,
            "stats": stats,
            "cfg": {"mods_path": self.mods_path, "library_path": self.library_path,
                    "mods_path_short": short_path(self.mods_path),
                    "library_path_short": short_path(self.library_path),
                    "bg_enabled": bool(self.cfg.get("bg_enabled", True)),
                    "bg_opacity": float(self.cfg.get("bg_opacity", 0.5)),
                    "card_opacity": float(self.cfg.get("card_opacity", 0.4)),
                    "has_bg": os.path.isfile(os.path.join(ASSETS_DIR, "bg_source.jpg")),
                    "workshop_dir": self.workshop_content_dir() or "",
                    "steam_root": self.steam_library_root() or "",
                    # 数据目录: 配置/分组/凭据/缓存都在这, 换新版 exe 不会动它
                    "data_dir": os.path.dirname(os.path.abspath(self.config_path)),
                    "config_file": os.path.abspath(self.config_path),
                    "portable": (os.path.normcase(os.path.dirname(
                        os.path.abspath(self.config_path))) == os.path.normcase(APP_DIR)),
                    "backup_enabled": bool(self.cfg.get("backup_enabled", True)),
                    "backup_keep": int(self.cfg.get("backup_keep") or 10),
                    "sort_rules": len(self.cfg.get("sort_rules") or []),
                    "strip_workshop_id": bool(self.cfg.get("strip_workshop_id", True)),
                    "strip_id_mode": self.cfg.get("strip_id_mode") or "zero",
                    "stripped_count": sum(
                        1 for d in self.mods
                        if os.path.isdir(os.path.join(self.library_path, d))
                        and saved_workshop_id(os.path.join(self.library_path, d)))},
            "version": APP_VERSION,
            "conflicts": {"count": len(self.conflict_detail),
                          "mods": len(self.conflict_map),
                          "items": self.conflict_detail[:80]},
            "updates": self.update_state(),
            "logs": self.logs[-6:],
        }

    # ---------- 启用 / 禁用 ----------
    def _import_one(self, dirname):
        src = None
        for base in (self.mods_path,
                     os.path.join(os.path.dirname(self.mods_path), LEGACY_DISABLED)):
            p = os.path.join(base, dirname)
            if os.path.isdir(p) and not is_junction(p):
                src = p
                break
        if src is None:
            raise RuntimeError("找不到实体文件夹: " + dirname)
        dst = os.path.join(self.library_path, dirname)
        if os.path.exists(dst):
            raise RuntimeError("仓库里已存在同名文件夹: " + dirname)
        os.makedirs(self.library_path, exist_ok=True)
        shutil.move(src, dst)
        return dst

    def set_enabled(self, dirname, enable):
        rec = self.mods.get(dirname)
        if rec is None:
            # 之前这里静默 return, 导致 API 返回 200 但什么都没做 (前端会谎报"启用 1 个")
            raise RuntimeError("列表中找不到该 mod: " + dirname)
        link = os.path.join(self.mods_path, rec.get("link") or dirname)
        if enable:
            if rec["state"] == ST_ENABLED:
                return
            if rec["state"] == ST_UNIMPORTED:
                self._import_one(dirname)
                rec["state"] = ST_DISABLED
            if is_junction(link):
                raw = junction_raw_target(link)
                inside = bool(raw) and _norm(raw).startswith(_norm(self.library_path) + os.sep)
                if os.path.isdir(link) and inside:
                    return                                  # 幂等
                if os.path.isdir(link):
                    raise RuntimeError("mods/ 里已有同名链接(指向别处): " + link)
                remove_junction(link)                       # 失效链接先清掉
            elif os.path.exists(link):
                raise RuntimeError("mods/ 里存在同名实体文件夹, 请先手动处理: " + link)
            target = os.path.join(self.library_path, dirname)
            if not os.path.isdir(target):
                raise RuntimeError("仓库里找不到该 mod 的实体文件夹: " + target)
            # 启用前先把工坊 ID 摘掉 —— 否则一旦取消订阅, 游戏启动时会把
            # mods/ 里的这个条目清掉(实测: 8 个 mod 全是这么没的)
            if self.cfg.get("strip_workshop_id", True):
                got = strip_workshop_id(target, self.cfg.get("strip_id_mode") or "zero")
                if got:
                    self.log("已去掉工坊 ID(%s), 让游戏不再把它当订阅内容: %s" % (got, dirname))
            create_junction(link, target)
        else:
            if rec["state"] == ST_UNIMPORTED:
                self._import_one(dirname)
                return
            if os.path.exists(link) and not is_junction(link):
                raise RuntimeError("mods/ 里存在同名实体文件夹, 不敢动: " + link)
            if is_junction(link):
                remove_junction(link)

    def set_dirs(self, dirs, enable, raise_on_fail=False):
        """批量启用/禁用; raise_on_fail=True 时单个失败直接抛错(供 API 返回 400)"""
        ok, fail = 0, []
        for d in dirs:
            if self.mods.get(d, {}).get("state") == (ST_ENABLED if enable else ST_DISABLED):
                continue
            try:
                self.set_enabled(d, enable)
                ok += 1
            except Exception as e:
                fail.append("%s (%s)" % (d, e))
        self.scan()
        if fail and ok == 0 and raise_on_fail:
            raise RuntimeError(fail[0])
        msg = "%s %d 个 mod" % ("启用" if enable else "禁用", ok)
        if fail:
            msg += "；失败: " + "; ".join(fail)
        self.log(msg)
        return msg, fail

    def strip_all_ids(self, mode=None):
        """把仓库里所有 mod 都"去工坊化"(一次性), 并恢复被摘掉的 id 记录"""
        mode = mode or self.cfg.get("strip_id_mode") or "zero"
        done, skipped = [], []
        for d in sorted(self.mods):
            p = os.path.join(self.library_path, d)
            if not os.path.isdir(p):
                continue
            got = strip_workshop_id(p, mode)
            if got:
                done.append({"dir": d, "id": got})
            else:
                skipped.append(d)
        self.scan()
        self.log("已去工坊化 %d 个 mod" % len(done))
        return {"changed": len(done), "done": done, "skipped": skipped, "mode": mode}

    def restore_all_ids(self):
        """还原所有 mod 的工坊 ID"""
        done = []
        for d in sorted(self.mods):
            p = os.path.join(self.library_path, d)
            if not os.path.isdir(p):
                continue
            got = restore_workshop_id(p)
            if got:
                done.append({"dir": d, "id": got})
        self.scan()
        self.log("已还原 %d 个 mod 的工坊 ID" % len(done))
        return {"changed": len(done), "done": done}

    def strip_state(self):
        """看看当前有多少 mod 是"已去工坊化"的"""
        stripped, normal = [], []
        for d in sorted(self.mods):
            p = os.path.join(self.library_path, d)
            if not os.path.isdir(p):
                continue
            if saved_workshop_id(p):
                stripped.append({"dir": d, "name": self.mods[d].get("name") or d,
                                 "id": saved_workshop_id(p)})
            else:
                normal.append(d)
        return {"enabled": bool(self.cfg.get("strip_workshop_id", True)),
                "mode": self.cfg.get("strip_id_mode") or "zero",
                "stripped": stripped, "normal_count": len(normal)}

    def clean_dead_link(self, dirname):
        p = os.path.join(self.mods_path, dirname)
        if is_junction(p) and not os.path.isdir(p):
            remove_junction(p)
            self.log("已清理失效链接: " + dirname)
        self.scan()

    def import_all(self):
        todo = [d for d, m in self.mods.items() if m["state"] == ST_UNIMPORTED]
        ok, fail = 0, []
        for d in todo:
            try:
                self._import_one(d)
                ok += 1
            except Exception as e:
                fail.append("%s (%s)" % (d, e))
        self.scan()
        for d in todo:
            if self.mods.get(d, {}).get("state") == ST_DISABLED:
                try:
                    self.set_enabled(d, True)
                except Exception as e:
                    fail.append("%s (%s)" % (d, e))
        self.scan()
        msg = "导入 %d 个 mod 到仓库" % ok
        if fail:
            msg += "；失败: " + "; ".join(fail)
        self.log(msg)
        return msg

    # ================================================================
    #  ① 冲突检测: 多个 mod 会覆盖同一个资源文件
    # ================================================================
    # 只统计"子目录里的"资源文件 —— 根目录的 main.lua / metadata.xml 每个 mod 都有,
    # 那不算冲突。子目录里的 resources/xxx.png 才会真的互相覆盖。
    CONFLICT_EXTS = {".png", ".anm2", ".wav", ".lua"}
    SCAN_IGNORE_DIRS = {"__pycache__", ".git", ".svn", "node_modules", "$RECYCLE.BIN"}

    @classmethod
    def _dir_token(cls, path):
        """目录状态的轻量指纹: 顶层 mtime + 条目名与各自 mtime。

        只要这个 token 没变, 就认为上次缓存的文件清单还能用 ——
        避免每次启动都把每个 mod 的整棵目录树走一遍(大 mod 几百个文件)。
        """
        h = hashlib.blake2b(digest_size=16)
        try:
            h.update(("%.6f\0" % os.path.getmtime(path)).encode())
            entries = sorted(os.listdir(path))
        except OSError:
            return None
        for e in entries:
            if e in cls.SCAN_IGNORE_DIRS:
                continue
            h.update(e.encode("utf-8", "replace") + b"\0")
            try:
                h.update(("%.6f\0" % os.path.getmtime(os.path.join(path, e))).encode())
            except OSError:
                h.update(b"0\0")
        return h.hexdigest()

    def _mod_conflict_files(self, dirname, force=False):
        """取一个 mod 里"可能冲突"的资源文件相对路径列表(带指纹缓存)"""
        base = os.path.join(self.library_path, dirname)
        if not os.path.isdir(base):
            return []
        try:
            c = cache()
        except Exception:
            c = None
        token = self._dir_token(base)
        if c is not None and token and not force:
            row = c.get_fingerprint(dirname)
            if row and row.get("token") == token:
                return row["files"]
        files = []
        for root, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in self.SCAN_IGNORE_DIRS]
            for n in names:
                if os.path.splitext(n)[1].lower() not in self.CONFLICT_EXTS:
                    continue
                rel = os.path.relpath(os.path.join(root, n), base)
                if os.sep not in rel:
                    continue                      # 根目录的文件不算
                files.append(rel.lower().replace("/", os.sep))
        if c is not None and token:
            try:
                c.set_fingerprint(dirname, token, files)
            except Exception:
                pass
        return files

    def conflict_scan(self, force=False):
        """扫描所有**已启用** mod 之间会互相覆盖的文件, 返回冲突清单"""
        idx = {}
        for d, rec in self.mods.items():
            if rec["state"] != ST_ENABLED:
                continue
            for rel in self._mod_conflict_files(d, force=force):
                idx.setdefault(rel, []).append(d)
        cmap, detail = {}, []
        for rel, dirs in idx.items():
            if len(dirs) < 2:
                continue
            ds = sorted(dirs)
            for d in ds:
                cmap.setdefault(d, []).append(rel)
            detail.append({
                "path": rel,
                "mods": ds,
                "names": [self.mods.get(d, {}).get("name") or d for d in ds],
            })
        detail.sort(key=lambda x: (-len(x["mods"]), x["path"]))
        self.conflict_map = cmap
        self.conflict_detail = detail
        self.log("冲突检测: %d 处重叠, 涉及 %d 个 mod" % (len(detail), len(cmap)))
        return detail

    def ensure_conflicts(self, force=False, max_age=25):
        """按需刷新冲突结果: mod 集合/状态没变且没超时就复用"""
        sig = tuple(sorted((d, m["state"]) for d, m in self.mods.items()))
        fresh = (time.time() - self._conflict_ts) < max_age
        if not force and self._conflict_sig == sig and fresh:
            return self.conflict_detail
        self._conflict_sig = sig
        self._conflict_ts = time.time()
        return self.conflict_scan(force=force)

    # ================================================================
    #  ③ mod 更新提醒: 比较工坊的 time_updated
    # ================================================================
    def check_updates(self, mark_seen=False, max_age=600):
        """检查已安装 mod 在工坊上有没有新版本。

        做法: 记住"上次见到的 time_updated"。第一次见到记下来(视为已知),
        之后工坊时间变大就算有更新 —— 这样不会把刚装的 mod 全报成"有更新"。
        """
        pairs = [(d, str(rec.get("id") or "")) for d, rec in self.mods.items()
                 if rec.get("id")]
        if not pairs:
            return {"checked": 0, "updates": [], "rate": None}
        ids = [p[1] for p in pairs]
        try:
            infos = fetch_workshop_info(ids, max_age=max_age)
        except Exception as e:
            return {"checked": 0, "updates": [], "error": str(e)[:140]}
        try:
            c = cache()
        except Exception:
            return {"checked": 0, "updates": [], "error": "缓存不可用"}
        updates = []
        for d, wid in pairs:
            it = infos.get(wid) or {}
            cur = int(it.get("updated") or 0)
            if not cur:
                continue
            key = "seen_upd:" + wid
            seen = c.kv_get(key)
            if seen is None:
                c.kv_set(key, cur)
            elif cur > int(seen or 0):
                updates.append({"dir": d, "name": self.mods[d].get("name") or d,
                                "id": wid, "was": int(seen or 0), "now": cur,
                                "url": STEAM_WS_PAGE + wid})
        marked = 0
        if mark_seen:
            for u in updates:
                c.kv_set("seen_upd:" + u["id"], u["now"])
            marked = len(updates)
            updates = []            # 用户已确认"我知道了", 本次就不再列出来
        self.update_map = {u["dir"]: u for u in updates}
        c.kv_set("last_update_check", time.strftime("%Y-%m-%d %H:%M:%S"))
        return {"checked": len(ids), "updates": updates, "marked": marked,
                "rate": c.rate_state()}

    def update_state(self):
        try:
            last = cache().kv_get("last_update_check")
        except Exception:
            last = None
        return {"count": len(self.update_map),
                "items": sorted(self.update_map.values(), key=lambda x: x["name"]),
                "last_check": last}

    # ================================================================
    #  ④ 自动排序: 规则(按工坊ID的 before/after) + 拓扑排序 + 落到链接名
    # ================================================================
    _SEQ_RE = re.compile(r"^\d{2,4}_")

    def sort_rules(self):
        return list(self.cfg.get("sort_rules") or [])

    def set_sort_rules(self, rules):
        out = []
        for r in (rules or []):
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or "").strip()
            if not rid:
                continue
            item = {"id": rid}
            for k in ("after", "before"):
                vals = [str(x).strip() for x in (r.get(k) or []) if str(x).strip()]
                if vals:
                    item[k] = vals
            out.append(item)
        self.cfg["sort_rules"] = out
        self.save()
        return out

    def auto_sort(self):
        """按规则算加载顺序(不改文件)。

        节点 = 已启用的 mod; 边 = "A 必须在 B 之后"。用 Kahn 做拓扑排序,
        环里的 mod 会原样附在末尾并单独报出来。
        """
        nodes = [d for d, r in self.mods.items() if r["state"] == ST_ENABLED]
        if not nodes:
            return {"order": [], "cycles": [], "applied": False}
        by_id = {}
        for d in nodes:
            wid = str(self.mods[d].get("id") or "")
            if wid:
                by_id[wid] = d
        edges = {d: set() for d in nodes}          # d 必须排在 edges[d] 里的元素之后
        for r in self.sort_rules():
            me = by_id.get(str(r.get("id")))
            if not me:
                continue
            for other in (r.get("after") or []):
                o = by_id.get(str(other))
                if o and o != me:
                    edges[me].add(o)
            for other in (r.get("before") or []):
                o = by_id.get(str(other))
                if o and o != me:
                    edges.setdefault(o, set()).add(me)
        # Kahn: 反复取"没有前置要求"的节点(按目录名稳定排序, 结果可复现)
        order, done = [], set()
        remain = set(nodes)
        progress = True
        while remain and progress:
            progress = False
            for d in sorted(remain):
                if edges.get(d, set()) - done:
                    continue
                order.append(d)
                done.add(d)
                remain.discard(d)
                progress = True
        cycles = sorted(remain)                     # 互相依赖(成环)的
        order += cycles
        return {"order": order, "cycles": cycles, "applied": False,
                "rules": len(self.sort_rules())}

    def apply_load_order(self, order=None):
        """把算出来的顺序**落到 mods/ 里的链接名**上(加 "NNN_" 前缀)。

        只改链接(指向仓库实体的那个软链接), 仓库里的实体目录名不动 ——
        游戏按 mods/ 下的目录名排序, 所以改链接名就等于改加载顺序。
        """
        if order is None:
            order = self.auto_sort()["order"]
        if self.dry_run:
            return {"changed": 0, "dry_run": True, "order": order}
        plan, seen = [], set()
        for i, d in enumerate(order, 1):
            rec = self.mods.get(d)
            if not rec or rec["state"] != ST_ENABLED:
                continue
            cur = rec.get("link") or d
            if os.path.basename(cur) in seen:
                continue
            seen.add(os.path.basename(cur))
            base = self._SEQ_RE.sub("", cur)
            plan.append((cur, "%03d_%s" % (i, base)))
        # 先把所有要动的链接改成临时名, 再改成目标名 —— 否则 A→B 而 B 还在会撞名
        tmp = []
        for idx, (cur, _tgt) in enumerate(plan):
            src = os.path.join(self.mods_path, cur)
            if not is_junction(src):
                continue
            t = ".imsort%03d" % idx
            try:
                os.rename(src, os.path.join(self.mods_path, t))
                tmp.append((t, _tgt))
            except OSError:
                pass
        changed = 0
        for t, tgt in tmp:
            try:
                os.rename(os.path.join(self.mods_path, t),
                          os.path.join(self.mods_path, tgt))
                changed += 1
            except OSError:
                try:
                    os.rename(os.path.join(self.mods_path, t),
                              os.path.join(self.mods_path, self._SEQ_RE.sub("", tgt)))
                except OSError:
                    pass
        try:
            cache().save_order(order)
        except Exception:
            pass
        self.log("已应用加载顺序: %d 个链接重命名" % changed)
        self.scan()
        return {"changed": changed, "order": order}

    def clear_load_order(self):
        """去掉所有链接上的顺序前缀(回到自然顺序)"""
        if self.dry_run:
            return {"changed": 0, "dry_run": True}
        changed = 0
        for d, rec in list(self.mods.items()):
            cur = rec.get("link") or d
            base = self._SEQ_RE.sub("", cur)
            if base == cur:
                continue
            src = os.path.join(self.mods_path, cur)
            if not is_junction(src):
                continue
            try:
                os.rename(src, os.path.join(self.mods_path, base))
                changed += 1
            except OSError:
                pass
        try:
            cache().save_order([])
        except Exception:
            pass
        self.scan()
        self.log("已清除加载顺序前缀: %d 个" % changed)
        return {"changed": changed}

    # ================================================================
    #  ⑥ 备份(轻量快照: 配置 + mod 清单, 不是整个仓库)
    # ================================================================
    def backup_dir(self):
        return os.path.join(os.path.dirname(os.path.abspath(self.config_path)), "backups")

    def backup_now(self, reason="manual"):
        d = self.backup_dir()
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            raise RuntimeError("备份目录不可写: %s" % e)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tag = re.sub(r"[^\w\-]+", "_", reason or "manual")[:20]
        path = os.path.join(d, "backup-%s-%s.zip" % (stamp, tag))
        manifest = {
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason, "app_version": APP_VERSION,
            "mods_path": self.mods_path, "library_path": self.library_path,
            "groups": self.cfg.get("groups") or [],
            "sort_rules": self.cfg.get("sort_rules") or [],
            "mods": [{"dir": k, "name": v.get("name", ""), "id": v.get("id", ""),
                      "state": v.get("state", ""), "link": v.get("link") or k}
                     for k, v in sorted(self.mods.items())],
        }
        note = ("Isaac Mod Manager 备份\r\n"
                "时间: %s\r\n原因: %s\r\n\r\n"
                "config.json  —— 当时的完整配置(含分组与排序规则)\r\n"
                "mods.json    —— 当时的 mod 清单(名字/工坊ID/启用状态/链接名)\r\n\r\n"
                "这是「状态快照」, 不含 mod 文件本体。要还原 mod 文件请用仓库目录。\r\n"
                % (manifest["created"], reason))
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("config.json", json.dumps(self.cfg, ensure_ascii=False, indent=2))
            z.writestr("mods.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            z.writestr("README.txt", note)
        keep = max(1, int(self.cfg.get("backup_keep") or 10))
        try:
            files = sorted(glob.glob(os.path.join(d, "backup-*.zip")))
            for old in files[:-keep]:
                try:
                    os.remove(old)
                except OSError:
                    pass
        except Exception:
            pass
        self.log("已备份: " + os.path.basename(path))
        return {"name": os.path.basename(path), "path": path,
                "size": os.path.getsize(path)}

    def list_backups(self):
        d = self.backup_dir()
        out = []
        if os.path.isdir(d):
            for f in sorted(glob.glob(os.path.join(d, "backup-*.zip")), reverse=True):
                try:
                    out.append({"name": os.path.basename(f), "size": os.path.getsize(f),
                                "time": time.strftime("%Y-%m-%d %H:%M:%S",
                                                      time.localtime(os.path.getmtime(f)))})
                except OSError:
                    pass
        return {"dir": d, "enabled": bool(self.cfg.get("backup_enabled", True)),
                "keep": int(self.cfg.get("backup_keep") or 10), "items": out}

    def _auto_backup(self, reason):
        """批量操作前自动备份(开关关掉就跳过)"""
        if not self.cfg.get("backup_enabled", True):
            return None
        try:
            return self.backup_now(reason)
        except Exception as e:
            self.log("自动备份失败(继续操作): %s" % str(e)[:100])
            return None

    def sync_to_game(self):
        created, removed, fixed, fail = 0, 0, 0, []
        for d, m in self.mods.items():
            link = os.path.join(self.mods_path, d)
            try:
                if m["state"] == ST_ENABLED and not is_junction(link):
                    target = os.path.join(self.library_path, d)
                    if not os.path.isdir(target):
                        fail.append("%s (仓库里缺实体)" % d)
                        continue
                    create_junction(link, target)
                    created += 1
                elif m["state"] == ST_DISABLED and is_junction(link):
                    raw = junction_raw_target(link)
                    if raw and _norm(raw).startswith(_norm(self.library_path) + os.sep):
                        remove_junction(link)
                        removed += 1
            except Exception as e:
                fail.append("%s (%s)" % (d, e))
        lib_n = _norm(self.library_path)
        if os.path.isdir(self.mods_path):
            for entry in os.listdir(self.mods_path):
                p = os.path.join(self.mods_path, entry)
                if not is_junction(p):
                    continue
                raw = junction_raw_target(p)
                if raw and _norm(raw).startswith(lib_n + os.sep) and not os.path.isdir(p):
                    try:
                        remove_junction(p)
                        fixed += 1
                    except Exception:
                        pass
        self.scan()
        msg = "同步完成: 新建链接 %d, 移除链接 %d" % (created, removed)
        if fixed:
            msg += ", 清理失效链接 %d" % fixed
        if fail:
            msg += "；失败: " + "; ".join(fail)
        self.log(msg)
        return msg

    def open_dir(self, which="library", path=None):
        target = path or (self.library_path if which == "library" else self.mods_path)
        if not os.path.isdir(target):
            raise RuntimeError("目录不存在: " + target)
        if not self.dry_run:
            subprocess.Popen(["explorer", os.path.normpath(target)])
        return "已打开: " + target

    def launch_game(self):
        """启动游戏: 优先用 Steam 协议(会自动拉起 Steam), 失败再直接运行 exe"""
        game_dir = os.path.dirname(self.mods_path)
        exe = os.path.join(game_dir, "isaac-ng.exe")
        # 1) Steam 协议: steam://rungameid/<AppID>
        if not self.dry_run:
            try:
                os.startfile("steam://rungameid/%s" % self.ISAAC_APPID)
                self.log("已通过 Steam 启动游戏")
                return "已通过 Steam 启动游戏"
            except Exception as e:
                self.log("Steam 协议启动失败(%s), 尝试直接运行 exe" % e)
        # 2) 直接运行游戏主程序
        if os.path.isfile(exe):
            if not self.dry_run:
                try:
                    subprocess.Popen([exe], cwd=game_dir)
                except Exception as e:
                    raise RuntimeError("启动失败: %s" % e)
            self.log("已启动游戏(直接运行 exe): " + exe)
            return "已启动游戏: " + exe
        # 3) 都没找到: 打开游戏目录让用户自己点
        self.open_dir(path=game_dir)
        raise RuntimeError("没找到 isaac-ng.exe, 已打开游戏目录: " + game_dir)

    # ---------- 分组 ----------
    def _group(self, name):
        return next((g for g in self.cfg["groups"] if g["name"] == name), None)

    def group_create(self, name):
        name = (name or "").strip()
        if not name:
            raise RuntimeError("分组名不能为空")
        if self._group(name):
            raise RuntimeError("已有同名分组: " + name)
        self.cfg["groups"].append({"name": name, "mods": []})
        self.save()
        self.log("已创建分组「%s」" % name)
        return "已创建分组「%s」" % name

    def group_delete(self, name):
        g = self._group(name)
        if not g:
            raise RuntimeError("分组不存在: " + name)
        self.cfg["groups"].remove(g)
        self.save()
        self.scan()
        self.log("已删除分组「%s」(mod 不受影响)" % name)
        return "已删除分组「%s」" % name

    def group_assign(self, name, dirs):
        g = self._group(name)
        if not g:
            raise RuntimeError("分组不存在: " + name)
        if not dirs:
            raise RuntimeError("请先选择 mod")
        for d in dirs:
            if d in self.mods and d not in g["mods"]:
                g["mods"].append(d)
        self.save()
        self.scan()
        msg = "已把 %d 个 mod 加入组「%s」" % (len(dirs), name)
        self.log(msg)
        return msg

    def group_remove(self, dirs):
        for d in dirs:
            for g in self.cfg["groups"]:
                if d in g["mods"]:
                    g["mods"].remove(d)
        self.save()
        self.scan()
        msg = "已移出 %d 个 mod 的分组" % len(dirs)
        self.log(msg)
        return msg

    def group_move(self, name, delta):
        idx = next((i for i, g in enumerate(self.cfg["groups"]) if g["name"] == name), None)
        if idx is None:
            raise RuntimeError("分组不存在: " + name)
        j = idx + int(delta)
        if not (0 <= j < len(self.cfg["groups"])):
            return "已到边界"
        gs = self.cfg["groups"]
        gs[idx], gs[j] = gs[j], gs[idx]
        self.save()
        return "已调整优先级: 「%s」-> P%d" % (name, j)

    def group_switch(self, name):
        g = self._group(name)
        if not g:
            raise RuntimeError("分组不存在: " + name)
        others = set()
        for og in self.cfg["groups"]:
            if og is not g:
                others.update(og["mods"])
        self.set_dirs(list(others), False)
        msg, _ = self.set_dirs(list(g["mods"]), True)
        out = "已切换到「%s」: 该组已启用, 其余组已禁用" % name
        self.log(out)
        return out

    def group_enable(self, name):
        g = self._group(name)
        if not g:
            raise RuntimeError("分组不存在: " + name)
        self.set_dirs(list(g["mods"]), True)
        msg = "已追加启用「%s」" % name
        self.log(msg)
        return msg

    def group_disable(self, name):
        g = self._group(name)
        if not g:
            raise RuntimeError("分组不存在: " + name)
        self.set_dirs(list(g["mods"]), False)
        msg = "已禁用「%s」" % name
        self.log(msg)
        return msg

    # ---------- 设置 ----------
    # ---------- Steam 创意工坊 ----------
    def steam_library_root(self):
        """从 mods 路径反推 Steam 库根目录: .../steamapps/common/<游戏>/mods → ..."""
        cur = os.path.abspath(self.mods_path)
        for _ in range(8):
            if os.path.basename(cur).lower() == "steamapps":
                return os.path.dirname(cur)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return None

    def workshop_content_dir(self):
        """Steam 工坊内容目录: <库>/steamapps/workshop/content/250900"""
        root = self.steam_library_root()
        if not root:
            return None
        return os.path.join(root, "steamapps", "workshop", "content", STEAM_APPID)

    def workshop_status(self, pid):
        """某个工坊条目在本地处于什么状态"""
        pid = str(pid)
        wdir = self.workshop_content_dir()
        return {
            "downloaded": bool(wdir) and os.path.isdir(os.path.join(wdir, pid)),
            "in_library": any(d.endswith("_" + pid) for d in self.mods),
            "workshop_dir": wdir or "",
            "steam_root": self.steam_library_root() or "",
        }

    def list_subscribed_workshop(self):
        """列出 Steam 工坊目录里已下载的条目 (标注是否已经导入仓库)"""
        wdir = self.workshop_content_dir()
        if not wdir or not os.path.isdir(wdir):
            return []
        out = []
        for entry in sorted(os.listdir(wdir)):
            p = os.path.join(wdir, entry)
            if not (entry.isdigit() and os.path.isdir(p)):
                continue
            info = read_metadata(p)
            out.append({
                "id": entry,
                "title": info.get("name") or entry,
                "size": dir_size(p),
                "imported": any(d.endswith("_" + entry) for d in self.mods),
            })
        return out

    def import_workshop_item(self, pid):
        """把 Steam 已下载好的工坊 mod 复制进仓库并启用。

        Steam 那份原样不动 (用复制不用移动): 一是 Steam 会自己维护/更新那份,
        二是万一 Steam 校验文件完整性, 也不会因为我们动过而出错。
        """
        pid = str(pid).strip()
        if not pid.isdigit():
            raise RuntimeError("创意工坊 ID 不合法: " + pid)
        wdir = self.workshop_content_dir()
        if not wdir:
            raise RuntimeError("找不到 Steam 工坊目录 —— mods 路径看起来不在 Steam 库里")
        src = os.path.join(wdir, pid)
        if not os.path.isdir(src):
            raise RuntimeError("Steam 还没下载这个 mod。请在弹出的页面点「订阅」，"
                               "下载完成后这里会自动导入。")
        info = read_metadata(src)
        base = re.sub(r'[<>:"/\\|?*\r\n\t]', "_", info.get("name") or "").strip(" .")
        if not base:
            base = "workshop_" + pid
        dirname = "%s_%s" % (base, pid)
        dst = os.path.join(self.library_path, dirname)
        if os.path.exists(dst):
            raise RuntimeError("仓库里已经有这个 mod 了: " + dirname)
        os.makedirs(self.library_path, exist_ok=True)
        shutil.copytree(src, dst)
        self.scan()
        self.set_enabled(dirname, True)
        self.scan()      # set_enabled 只动文件系统, 要再扫一次状态才会变成「已启用」
        self.log("已从 Steam 工坊导入并启用: " + dirname)
        return dirname

    # ---------- 工坊下载: 唤起 Steam 客户端 + 自动守候 + 自动导入 ----------
    def steam_session_file(self):
        # 与其它数据同目录(老版本写的是小写 isaac_mod_manager, Windows 不区分大小写,
        # 所以已经登录过的凭据仍然能被读到, 不需要重新登录)
        return os.path.join(USER_DIR, "steam_session.json")

    def steam_logged_in(self):
        """本地是否已有登录凭据(refresh_token) —— 有就不用再输密码。

        说明: 老库(steamctl)把 login key 存在 %LOCALAPPDATA%/steamctl 下, 但那条路
        要求明文密码, 现代 Steam 会拒绝(正确密码也报 InvalidPassword), 所以改用
        steam_auth 的网页认证流程, 凭据存在 steam_session.json 里。
        这里直接读文件, 不起子进程(状态查询很频繁)。
        """
        try:
            with io.open(self.steam_session_file(), encoding="utf-8") as fp:
                data = json.load(fp)
            if data.get("refresh_token"):
                return data.get("account") or "已登录"
        except Exception:
            pass
        return None

    def _python_candidates(self):
        """可能装了 steamctl 的 Python 解释器候选表(按优先级)"""
        out = []
        if not getattr(sys, "frozen", False):
            out.append(sys.executable)        # 源码运行: 自己就是对的解释器
        cfg_py = (self.cfg.get("downloader_python") or "").strip()
        if cfg_py:
            out.append(cfg_py)
        # 本机常见的隔离 venv 位置(steamctl 通常装在这种环境里)
        home = os.path.expanduser("~")
        for pat in (os.path.join(home, ".workbuddy", "binaries", "python", "envs",
                                 "*", "Scripts", "python.exe"),
                    os.path.join(home, ".workbuddy", "binaries", "python", "envs",
                                 "*", "bin", "python")):
            out.extend(sorted(glob.glob(pat)))
        for name in ("python", "python3", "py"):     # 最后才看 PATH
            p = shutil.which(name)
            if p:
                out.append(p)
        seen, uniq = set(), []
        for p in out:
            k = os.path.normcase(p)
            if k not in seen:
                seen.add(k)
                uniq.append(p)
        return uniq

    def downloader_paths(self):
        """找出「能 import steamctl 的 Python」与下载脚本, 找不到就给出可操作提示"""
        script = os.path.join(TOOLS_DIR, "ws_download.py")
        if not os.path.isfile(script):
            raise RuntimeError("缺少下载脚本: " + script)

        cfg_py = (self.cfg.get("downloader_python") or "").strip()
        tried = []
        for py in self._python_candidates():
            if not py or not os.path.isfile(py):
                continue
            tried.append(py)
            try:
                r = subprocess.run([py, "-c", "import steamctl, gevent, steam"],
                                   capture_output=True, timeout=90)
            except Exception:
                continue
            if r.returncode == 0:
                if py != cfg_py:
                    self.cfg["downloader_python"] = py
                    # 必须用 self.save(): 它写到本实例的 config_path。
                    # 直接调模块级 save_config(cfg) 会写到全局 CONFIG_PATH,
                    # 测试(用临时 config)时就会污染主人的真实配置 —— 踩过一次。
                    self.save()
                return py, script
        raise RuntimeError(
            "找不到装了 steamctl 的 Python 环境(已试 %d 个)。\n"
            "解决: 在命令行执行 pip install steamctl 后重开本程序。" % len(tried))

    def ws_job_state(self):
        j = getattr(self, "ws_job", None) or {}
        return {"running": bool(j.get("running")),
                "id": j.get("id"),
                "state": j.get("state", "idle"),
                "msg": j.get("msg", "空闲"),
                "lines": (j.get("lines") or [])[-8:]}

    def steam_client_running(self):
        """Steam 客户端是否在运行 —— 工坊内容必须由它下载"""
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
                                 capture_output=True).stdout or b""
            return b"steam.exe" in out.lower()
        except Exception:
            return True          # 检测手段失败时不挡功能

    def _open_steam_workshop_page(self, pid):
        """唤起 Steam 客户端的工坊页面(不弹控制台窗口)"""
        url = "steam://url/CommunityFilePage/%s" % pid
        try:
            os.startfile(url)            # Windows: 交给系统协议处理器
            return True
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", url])
                return True
            except Exception:
                return False

    def start_workshop_download(self, pid):
        """下载一个工坊 mod 到仓库(后台线程), 前端轮询 /api/workshop/job 看进度

        为什么要借 Steam 客户端: 以撒的工坊内容是"多文件 UGC", Steam 不提供
        匿名直链(GetPublishedFileDetails 的 file_url 恒为空), 也没有任何 Web API
        能直接取到内容 —— 只有"拥有该游戏的 Steam 客户端"才被授权下载。
        所以这里的做法是: 唤起 Steam 工坊页 -> 守候下载结果 -> 自动导入仓库。
        """
        pid = str(pid).strip()
        if not pid.isdigit():
            raise RuntimeError("创意工坊 ID 不合法: " + pid)
        j = getattr(self, "ws_job", None)
        if j and j.get("running"):
            raise RuntimeError("已有下载任务在进行: " + str(j.get("id")))

        wdir = self.workshop_content_dir()
        if not wdir:
            raise RuntimeError("找不到 Steam 工坊目录 —— mods 路径看起来不在 Steam 库里")
        src = os.path.join(wdir, pid)

        # 快路径: Steam 已经下好了, 直接导入, 不用再折腾订阅
        if os.path.isdir(src) and os.listdir(src):
            self.ws_job = {"running": True, "id": pid, "state": "running",
                           "msg": "Steam 里已有这个 mod, 正在导入仓库…",
                           "lines": [], "t0": time.time()}
            threading.Thread(target=self._ws_import_worker, args=(pid,),
                             daemon=True).start()
            return self.ws_job_state()

        if not self.steam_client_running():
            raise RuntimeError(
                "没检测到 Steam 客户端在运行, 没法下载。\n"
                "以撒的工坊内容只能由 Steam 客户端下载(Steam 不提供直链),\n"
                "请先启动 Steam 并登录, 再回来点「下载」。")

        self.ws_job = {"running": True, "id": pid, "state": "running",
                       "msg": "正在唤起 Steam 工坊页面…",
                       "lines": [], "t0": time.time()}
        threading.Thread(target=self._ws_steam_worker, args=(pid,),
                         daemon=True).start()
        return self.ws_job_state()

    @staticmethod
    def _human_size(n):
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return ("%.1f %s" % (n, unit)) if unit != "B" else ("%d B" % n)
            n /= 1024.0
        return "%.1f GB" % n

    def _ws_import_worker(self, pid):
        """把 Steam 已下好的工坊内容导入仓库"""
        j = self.ws_job
        try:
            name = self.import_workshop_item(pid)
            j.update(running=False, state="done", msg="已导入并启用到仓库: " + name)
        except Exception as exc:
            j.update(running=False, state="error", msg=str(exc)[:300])

    def _ws_steam_worker(self, pid):
        """守候 Steam 下载: 目录出现 -> 大小稳定 -> 自动导入"""
        j = self.ws_job
        if not self._open_steam_workshop_page(pid):
            j.update(running=False, state="error", msg="打不开 Steam 工坊页面")
            return
        j["lines"] = [
            "已在 Steam 中打开这个 mod 的工坊页面",
            "请在 Steam 窗口里点一下「订阅」按钮",
            "订阅后 Steam 会自动下载, 下载完这里会自动导入并启用",
        ]
        j["msg"] = "已唤起 Steam 工坊页, 请在 Steam 里点「订阅」"
        # 若 Steam 尚未启动, 上一步会把它拉起来, 这里等它就绪
        wdir = self.workshop_content_dir()
        src = os.path.join(wdir, pid)
        deadline = time.time() + 1800          # 最多等 30 分钟
        last_size, stable, seen = -1, 0, False
        while time.time() < deadline:
            time.sleep(2)
            if not os.path.isdir(src):
                continue
            try:
                size = dir_size(src)
            except Exception:
                continue
            if size <= 0:
                continue
            if size == last_size:
                stable += 1
                if stable >= 3:                # 连续 ~6 秒大小不变 => 下完了
                    break
            else:
                stable = 0
            last_size = size
            j["msg"] = ("Steam 正在下载… (%s)" % self._human_size(size)) if seen \
                else ("Steam 已开始下载 (%s)" % self._human_size(size))
            seen = True
        else:
            j.update(running=False, state="error",
                     msg="等待超时(30 分钟)。可能还没在 Steam 里点「订阅」, "
                         "或 Steam 没有在下载。")
            return
        j["msg"] = "下载完成, 正在导入仓库…"
        try:
            name = self.import_workshop_item(pid)
            j.update(running=False, state="done",
                     msg="已下载并启用到仓库: %s" % name)
        except Exception as exc:
            j.update(running=False, state="error",
                     msg="Steam 已下载完成, 但导入失败: %s" % exc)

    def _ws_worker(self, py, script, pid):
        """后台执行下载脚本, 把输出逐行收集成进度"""
        j = self.ws_job
        try:
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen([py, "-u", script, "--no-prompt",
                                     pid, self.library_path],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL,   # 绝不等待输入
                                    env=env, cwd=APP_DIR)
            # 看门狗: 万一网络卡死, 也别让任务永远挂着
            killer = threading.Timer(1800, lambda: proc.kill()
                                     if proc.poll() is None else None)
            killer.start()
            lines = []
            try:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip()
                    if not line.strip():
                        continue
                    lines.append(line)
                    j["lines"] = lines[-60:]
                    j["msg"] = line
                proc.wait(timeout=60)
                rc = proc.returncode
            finally:
                killer.cancel()
        except Exception as exc:
            j.update(running=False, state="error", msg="下载异常: %s" % exc)
            return

        if rc != 0:
            # 取「根因那行」: 带 [失败]/[错误] 前缀的是关键信息,
            # 直接取最后一行会拿到收尾的清理提示, 没有价值
            cause = None
            for l in lines:
                if l.startswith(("[失败]", "[错误]", "[需要登录]", "[中断]", "[说明]")):
                    cause = l
                    break
            if cause is None:
                cause = lines[-1] if lines else "下载失败"
            j.update(running=False, state="error",
                     msg="%s  (退出码 %s)" % (cause, rc))
            return

        # 成功: 脚本已把内容放进 "<标题>_<ID>", 找到它并启用
        self.scan()
        target = next((d for d in self.mods if d.endswith("_" + pid)), None)
        msg = "下载完成"
        if target:
            try:
                self.set_enabled(target, True)
                self.scan()
                msg = "下载并启用完成: " + target
            except Exception as exc:
                msg = "已下载 %s, 但启用失败: %s" % (target, exc)
        else:
            msg = "下载完成, 但没在仓库里找到对应文件夹"
        j.update(running=False, state="done", msg=msg)
        self.log(msg)

    def steam_login(self, user, password, code="", mode="", action=""):
        """界面内登录 —— 不弹任何控制台窗口。

        走 Steam 的网页认证流程(steam_auth): RSA 加密凭据 -> 拿 client_id/request_id
        -> 必要时提交验证码 -> 轮询拿到 refresh_token。密码只经 stdin 交给子进程,
        不写命令行、不写环境变量、不落文件; 落盘的只有 refresh_token。

        返回:
            {"ok": True, "user": ...}                  登录成功
            {"need_code": "device"|"email"}            要验证码(手输)
            {"need_confirm": True, "user": ...}        要在手机 Steam 里点「批准」
        失败抛 RuntimeError。
        """
        if not action:
            action = "code" if (code or mode in ("confirm", "poll")) else "begin"
        if action == "begin":
            user = (user or "").strip()
            password = password or ""
            if not user or not password:
                raise RuntimeError("Steam 账号和密码都要填")
        else:
            user, password = (user or "").strip(), password or ""

        script = os.path.join(TOOLS_DIR, "ws_login.py")
        if not os.path.isfile(script):
            raise RuntimeError("缺少登录脚本: " + script)
        py, _dl = self.downloader_paths()      # 复用「找解释器」的逻辑

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        payload = json.dumps({"action": action, "user": user, "password": password,
                              "code": code, "mode": mode}).encode("utf-8")
        try:
            r = subprocess.run([py, "-u", script], input=payload,
                               capture_output=True, timeout=150,
                               env=env, cwd=APP_DIR)
        except subprocess.TimeoutExpired:
            raise RuntimeError("登录超时(网络或 Steam 无响应), 请重试")

        out = (r.stdout or b"").decode("utf-8", "replace")
        res = None
        for line in reversed(out.splitlines()):      # 取最后一行能解析的 JSON
            line = line.strip()
            if line.startswith("{"):
                try:
                    res = json.loads(line)
                    break
                except ValueError:
                    continue
        if res is None:
            tail = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError("登录没有返回结果: "
                               + (tail[-1][:160] if tail else "未知错误"))

        st = res.get("status")
        if st == "ok":
            self.log("已登录 Steam: " + str(res.get("account") or user))
            return {"ok": True, "user": res.get("account") or user}
        if st == "code":
            return {"need_code": res.get("mode") or "device",
                    "result_name": res.get("error") or ""}
        if st == "confirm_wait":
            return {"need_confirm": True, "user": res.get("account") or user,
                    "error": res.get("error") or ""}
        raise RuntimeError(res.get("error") or "登录失败")

    def apply_settings(self, data):
        if "strip_workshop_id" in data:
            self.cfg["strip_workshop_id"] = bool(data.get("strip_workshop_id"))
        if data.get("strip_id_mode") in ("zero", "remove"):
            self.cfg["strip_id_mode"] = data.get("strip_id_mode")
        if "backup_enabled" in data:
            self.cfg["backup_enabled"] = bool(data.get("backup_enabled"))
        if "backup_keep" in data:
            try:
                self.cfg["backup_keep"] = max(1, min(99, int(data.get("backup_keep") or 10)))
            except (TypeError, ValueError):
                pass
        if data.get("mods_path"):
            self.cfg["mods_path"] = os.path.normpath(data["mods_path"].strip())
        if "library_path" in data:
            self.cfg["library_path"] = (data["library_path"] or "").strip()
        for key, lo, hi in (("bg_opacity", 0.0, 1.0), ("card_opacity", 0.0, 1.0)):
            if key in data and data[key] is not None:
                self.cfg[key] = max(lo, min(hi, float(data[key])))
        if "bg_enabled" in data:
            self.cfg["bg_enabled"] = bool(data["bg_enabled"])
        self.save()
        self.scan()
        self.log("设置已保存")
        return "设置已保存"


# ======================================================================
#  HTTP 服务
# ======================================================================
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon",
        ".webp": "image/webp", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    lib = None                       # 由 main 注入
    server_version = "IsaacModManager/2.0"

    def log_message(self, fmt, *args):
        pass                          # 静音访问日志

    # ---------- 工具 ----------
    def _bytes(self, data, ctype, cache=True, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control",
                         "max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------- 工坊预览图 ----------
    def workshop_thumb(self, url):
        """把工坊预览图取回来做小缩略图并缓存。

        为什么必须走这里(不能让浏览器直连):
            Steam 的图片 CDN 会拒绝来自非 steam 站点的热链请求 ——
            浏览器里 <img> 带上 Referer: http://127.0.0.1:xxxx 就会被挡,
            表现为一片"碎图"图标。由后端带正确的 UA/Referer 去取就正常。
        顺带好处: 缩略图转成 JPEG 小图并落盘缓存, 第二次打开秒开。
        """
        url = urllib.parse.unquote(url or "")
        parts = urllib.parse.urlparse(url)
        host = (parts.netloc or "").lower()
        allowed = ("steamusercontent.com", "steamstatic.com",
                   "akamaihd.net", "steamcommunity.com")
        if parts.scheme != "https" or not host.endswith(allowed):
            return self._json({"ok": False, "error": "不允许的图片地址"}, 400)

        cache_dir = os.path.join(USER_DIR, "thumbs")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except OSError:
            cache_dir = None
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()

        if cache_dir:
            for ext, ctype in ((".jpg", "image/jpeg"), (".raw", "application/octet-stream")):
                p = os.path.join(cache_dir, key + ext)
                if os.path.isfile(p) and os.path.getsize(p) > 0:
                    with open(p, "rb") as fp:
                        return self._bytes(fp.read(), ctype)

        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent",
                           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36")
            req.add_header("Referer", "https://steamcommunity.com/")
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "image/gif")
        except Exception as exc:
            return self._json({"ok": False, "error": "取图失败: %s" % str(exc)[:80]}, 502)

        out, out_type, ext = raw, ctype, ".raw"
        try:                                   # 有 Pillow 就顺手压成小 JPEG
            from PIL import Image
            im = Image.open(io.BytesIO(raw))
            try:
                im.seek(0)                     # GIF 只取第一帧
            except Exception:
                pass
            im = im.convert("RGB")
            if im.width > 480:
                im = im.resize((480, max(1, int(im.height * 480 / im.width))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=82)
            out, out_type, ext = buf.getvalue(), "image/jpeg", ".jpg"
        except Exception:
            pass

        if cache_dir:
            try:
                with open(os.path.join(cache_dir, key + ext), "wb") as fp:
                    fp.write(out)
            except OSError:
                pass
        return self._bytes(out, out_type)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        if not os.path.isfile(path):
            self._json({"ok": False, "error": "not found: " + path}, 404)
            return
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return {}

    def _safe_join(self, root, rel):
        p = os.path.normpath(os.path.join(root, rel.lstrip("/\\")))
        return p if _norm(p).startswith(_norm(root)) else None

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/workshop/thumb":
            # 预览图代理(浏览器直连会被 Steam CDN 拒绝热链)
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            return self.workshop_thumb((qs.get("u") or [""])[0])
        if path == "/api/ping":
            # 单实例探测: 用来判断端口上跑的是不是本程序
            return self._json({"ok": True, "app": APP_TAG, "pid": os.getpid()})
        if path == "/api/state":
            self.lib.scan()
            return self._json({"ok": True, **self.lib.state()})
        if path in ("/", "/index.html"):
            return self._file(os.path.join(WEB_DIR, "index.html"))
        for root, prefix in ((WEB_DIR, "/web/"), (ASSETS_DIR, "/assets/")):
            if path.startswith(prefix):
                p = self._safe_join(root, path[len(prefix):])
                if p:
                    return self._file(p)
        if path == "/favicon.ico":
            return self._json({"ok": False}, 404)
        # 其他静态文件 (css/js 直接放 web 根下)
        p = self._safe_join(WEB_DIR, path)
        if p:
            return self._file(p)
        self._json({"ok": False, "error": "unknown path"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        path = self.path.split("?")[0]
        d = self._body()
        lib = self.lib
        lib.scan()          # 动作前先刷新状态, 避免用过期数据做判断
        try:
            if path == "/api/mods/toggle":
                lib.set_dirs([d["dir"]], bool(d.get("enable")), raise_on_fail=True)
            elif path == "/api/mods/batch":
                if d.get("dirs"):
                    lib._auto_backup("before-batch")       # 批量操作前留个快照
                lib.set_dirs(list(d.get("dirs") or []), bool(d.get("enable")))
            elif path == "/api/mods/import":
                lib.import_all()
            elif path == "/api/mods/sync":
                lib._auto_backup("before-sync")
                lib.sync_to_game()
            elif path == "/api/mods/clean_dead":
                lib.clean_dead_link(d["dir"])
            elif path == "/api/groups/create":
                lib.group_create(d.get("name", ""))
            elif path == "/api/groups/delete":
                lib.group_delete(d.get("name", ""))
            elif path == "/api/groups/assign":
                lib.group_assign(d.get("name", ""), list(d.get("dirs") or []))
            elif path == "/api/groups/remove":
                lib.group_remove(list(d.get("dirs") or []))
            elif path == "/api/groups/move":
                lib.group_move(d.get("name", ""), d.get("delta", 0))
            elif path == "/api/groups/switch":
                lib.group_switch(d.get("name", ""))
            elif path == "/api/groups/enable":
                lib.group_enable(d.get("name", ""))
            elif path == "/api/groups/disable":
                lib.group_disable(d.get("name", ""))
            elif path == "/api/settings":
                lib.apply_settings(d)
            elif path == "/api/mods/strip_ids":
                self._extra = {"strip": lib.strip_all_ids(d.get("mode"))}
            elif path == "/api/mods/restore_ids":
                self._extra = {"strip": lib.restore_all_ids()}
            elif path == "/api/mods/strip_state":
                self._extra = {"strip": lib.strip_state()}
            elif path == "/api/conflicts":
                detail = lib.ensure_conflicts(force=bool(d.get("force")))
                self._extra = {"conflicts": {"items": detail,
                                             "mods": len(lib.conflict_map),
                                             "count": len(detail)}}
            elif path == "/api/updates/check":
                self._extra = {"ws_updates": lib.check_updates(
                    mark_seen=bool(d.get("mark_seen")))}
            elif path == "/api/sort/preview":
                self._extra = {"sort": lib.auto_sort()}
            elif path == "/api/sort/apply":
                order = lib.auto_sort()["order"]
                lib._auto_backup("before-sort")
                res = lib.apply_load_order(order)
                res["order"] = order
                self._extra = {"sort": res}
            elif path == "/api/sort/clear":
                self._extra = {"sort": lib.clear_load_order()}
            elif path == "/api/sort/rules":
                if d.get("rules") is not None:
                    self._extra = {"rules": lib.set_sort_rules(d.get("rules"))}
                else:
                    self._extra = {"rules": lib.sort_rules()}
            elif path == "/api/app/update":
                self._extra = {"app_update": check_self_update()}
            elif path == "/api/backup/create":
                self._extra = {"backup": lib.backup_now(d.get("reason") or "manual")}
            elif path == "/api/backup/list":
                self._extra = {"backups": lib.list_backups()}
            elif path == "/api/open_dir":
                lib.open_dir(d.get("which", "library"), d.get("path"))
            elif path == "/api/launch":
                lib.launch_game()
            elif path == "/api/workshop/lookup":
                # 解析剪贴板里的工坊链接 → 查信息 → 报告本地状态
                pid = parse_workshop_id(d.get("text") or "")
                if not pid:
                    raw = (d.get("text") or "").strip()
                    if re.fullmatch(r"\d{15,20}", raw):
                        # 常见误用: 黏上的是个人资料页里的 SteamID64
                        raise RuntimeError(
                            "这串是 Steam 账号 ID(SteamID64), 不是创意工坊 mod 的 ID。\n"
                            "请打开 mod 的创意工坊页面, 复制地址栏里的链接\n"
                            "(形如 .../sharedfiles/filedetails/?id=2900345009), 或直接填那串 9~10 位数字。")
                    raise RuntimeError("没识别出创意工坊 ID。请复制创意工坊页面链接，"
                                       "或直接填 9~10 位的数字 ID。")
                st = lib.workshop_status(pid)
                extra = {"ws_id": pid, "ws_status": st}
                if not d.get("local_only"):     # 轮询时只查本地状态, 不反复打 Steam API
                    extra["ws_info"] = fetch_workshop_info([pid]).get(pid) or {}
                self._extra = extra
            elif path == "/api/workshop/add":
                pid = str(d.get("id") or "")
                if not pid:
                    raise RuntimeError("缺少创意工坊 ID")
                dirname = lib.import_workshop_item(pid)
                self._extra = {"ws_added": dirname}
            elif path == "/api/workshop/subscribed":
                self._extra = {"ws_subscribed": lib.list_subscribed_workshop()}
            elif path == "/api/workshop/download":
                pid = str(d.get("id") or "")
                if not pid:
                    raise RuntimeError("缺少创意工坊 ID")
                self._extra = {"ws_job": lib.start_workshop_download(pid)}
            elif path == "/api/workshop/job":
                self._extra = {"ws_job": lib.ws_job_state()}
            elif path == "/api/workshop/search":
                # 应用内搜索创意工坊: 读社区浏览页里内嵌的官方 JSON,
                # 不需要 WebAPI key, 也不需要登录
                if TOOLS_DIR not in sys.path:
                    sys.path.insert(0, TOOLS_DIR)
                import ws_search
                res = ws_search.search(d.get("text") or "",
                                       d.get("sort") or "trend",
                                       d.get("page") or 1)
                for it in res.get("items") or []:      # 标注本地状态, 界面上好区分
                    st = lib.workshop_status(it["id"])
                    it["in_library"] = st["in_library"]
                    it["downloaded"] = st["downloaded"]
                res["logged_in"] = bool(lib.steam_logged_in())
                self._extra = {"ws_search": res}
            elif path == "/api/workshop/status":
                who = lib.steam_logged_in()
                self._extra = {"logged_in": bool(who), "steam_user": who,
                               "login_action": d.get("action") or ""}
            elif path == "/api/workshop/login":
                # 界面内直接登录: 不再弹控制台窗口, 也不再让主人自己去敲命令。
                # 密码只在本进程内存里转交给子进程的 stdin, 不写日志/不写配置。
                self._extra = {"login": lib.steam_login(
                    d.get("user"), d.get("password"),
                    d.get("code") or "", d.get("mode") or "")}
            elif path == "/api/shutdown":
                # exe 是无控制台窗口的, 没有 Ctrl+C 可用, 必须给一个退出入口
                lib.log("正在退出程序…")
                threading.Timer(0.35, lambda: os._exit(0)).start()
                return self._json({"ok": True, "bye": True})
            else:
                return self._json({"ok": False, "error": "unknown api: " + path}, 404)
        except Exception as e:
            lib.log("失败: " + str(e))
            lib.scan()
            return self._json({"ok": False, "error": str(e), **lib.state()}, 400)
        lib.scan()
        body = {"ok": True, **lib.state()}
        extra = getattr(self, "_extra", None)
        if extra:
            body.update(extra)
            self._extra = None
        return self._json(body)


def serve(lib, port=8760, host="127.0.0.1", max_tries=20):
    Handler.lib = lib
    for i in range(max_tries):
        try:
            httpd = ThreadingHTTPServer((host, port + i), Handler)
            return httpd, port + i
        except OSError:
            continue
    raise RuntimeError("端口被占用: %d ~ %d" % (port, port + max_tries))


def find_running_instance(port, tries=20):
    """探测本程序是否已经在跑 (扫描端口, 用 /api/ping 的身份标识确认)。

    返回已运行实例的 URL, 没找到返回 None。
    目的: 重复双击 exe 时不再起第二份服务, 只把浏览器带到已有界面。
    """
    import urllib.request
    for i in range(tries):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % (port + i),
                                       timeout=0.4) as r:
                if json.loads(r.read().decode("utf-8")).get("app") == APP_TAG:
                    return "http://127.0.0.1:%d/" % (port + i)
        except Exception:
            continue
    return None


class _Tee(object):
    """把输出同时写进控制台和日志文件 (exe 无控制台时只写文件)"""

    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


def redirect_log():
    """exe 模式没有控制台, 把 print/异常输出转到 exe 旁边的日志文件"""
    try:
        f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        f.write("\n===== %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        sys.stdout = _Tee(sys.stdout, f)
        sys.stderr = _Tee(sys.stderr, f)
        return f
    except Exception:
        return None


def fatal(msg, title="以撒 Mod 管理器"):
    """致命错误提示: exe 无控制台, 用系统弹窗告知, 否则打印"""
    print(msg)
    if FROZEN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(msg), title, 0x10)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="以撒 Mod 管理器 (Web UI)")
    ap.add_argument("--port", type=int, default=8760)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--config", default=None,
                    help="指定配置文件 (默认: 用户数据目录里的 config.json)")
    ap.add_argument("--portable", action="store_true",
                    help="便携模式: 配置写在程序旁边的 config.json")
    ap.add_argument("--force-new", action="store_true",
                    help="即使已有实例在运行, 也再起一个 (默认复用已有实例)")
    ap.add_argument("--no-log", action="store_true", help="不写日志文件")
    args = ap.parse_args()

    # 配置走用户数据目录(或便携模式), 顺带把老版本写在程序旁边的配置迁过来
    args.config = resolve_config_path(args.config, args.portable)

    logfile = None
    if FROZEN and not args.no_log:
        logfile = redirect_log()

    # ---- 单实例: 已在跑就只把浏览器带过去, 不再起第二份服务 ----
    if not args.force_new:
        url = find_running_instance(args.port)
        if url:
            print("检测到已有实例在运行: %s" % url)
            if not args.no_browser:
                webbrowser.open(url)
            sys.exit(0)

    try:
        lib = ModLibrary(config_path=args.config)
        httpd, port = serve(lib, args.port)
    except Exception as e:
        fatal("启动失败: %s\n\n日志: %s" % (e, LOG_PATH if FROZEN else "(控制台)"))
        sys.exit(1)

    url = "http://127.0.0.1:%d/" % port
    print("以撒 Mod 管理器已启动: %s" % url)
    print("mods: %s" % lib.mods_path)
    print("仓库: %s" % lib.library_path)
    print("资源: web=%s, assets=%s" % (WEB_DIR, ASSETS_DIR))
    print("数据: %s" % os.path.dirname(os.path.abspath(lib.config_path)))
    print("(关闭窗口 / 界面左下角「退出程序」可结束本程序)")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        if logfile:
            logfile.close()


if __name__ == "__main__":
    main()
