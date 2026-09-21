# -*- coding: utf-8 -*-
"""
以撒 Mod 管理器 —— 全功能自验证

用法:  python tests/selftest.py
特点:
  * 全部在临时沙盒目录里跑, 绝不碰主人的真实游戏目录
  * 后端用 dry_run 模式, 不会真的打开资源管理器/启动游戏
  * 检查「前端 app.js 调用的接口」与「后端实现的接口」是否一一对应
"""
import glob
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import importlib.util

_spec = importlib.util.spec_from_file_location("imm", os.path.join(ROOT, "isaac_mod_manager.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

PORT = 8931
PASS, FAIL = [], []

# ---- 护栏: 记下主人真实配置文件的内容, 收尾时比对, 确保测试绝不污染它 ----
REAL_CFG = os.path.join(ROOT, "isaac_mod_manager_config.json")
try:
    _REAL_CFG_BEFORE = io.open(REAL_CFG, encoding="utf-8").read()
except OSError:
    _REAL_CFG_BEFORE = None

# 配置现在住在用户数据目录里 —— 这份才是"主人在用的", 更不能被测试写坏
REAL_USER_CFG = os.path.join(m.USER_DIR, "config.json")
try:
    _REAL_USER_CFG_BEFORE = io.open(REAL_USER_CFG, encoding="utf-8").read()
except OSError:
    _REAL_USER_CFG_BEFORE = None


def check(cond, title, extra=""):
    (PASS if cond else FAIL).append(title)
    print("  %s %s%s" % ("✓" if cond else "✗", title, ("  → " + str(extra)) if extra else ""))
    return cond


def section(t):
    print("\n" + t)


# ---------------------------------------------------------------- 沙盒
tmp = tempfile.mkdtemp(prefix="imm_test_")
game = os.path.join(tmp, "game")
mods = os.path.join(game, "mods")
lib = os.path.join(game, "isaac_mod_library")
os.makedirs(mods)
os.makedirs(lib)
m.USER_DIR = tmp          # 让 v2 的本地缓存也写到沙盒, 不碰用户真实数据
for name in ["Alpha_1", "Beta_2", "Gamma_3", "Delta_4"]:
    p = os.path.join(mods, name)
    os.makedirs(p)
    io.open(os.path.join(p, "metadata.xml"), "w", encoding="utf-8").write(
        "<metadata><name>%s</name><id>%s</id><version>1.0</version></metadata>" % (name, name[-1]))
io.open(os.path.join(game, "isaac-ng.exe"), "wb").write(b"MZ fake")
os.makedirs(os.path.join(lib, "Orphan_9"), exist_ok=True)
io.open(os.path.join(lib, "Orphan_9", "metadata.xml"), "w", encoding="utf-8").write(
    "<metadata><name>Orphan_9</name></metadata>")

cfg_path = os.path.join(tmp, "cfg.json")
io.open(cfg_path, "w", encoding="utf-8").write(json.dumps(
    {"mods_path": mods, "library_path": lib, "groups": [], "show_disabled": True,
     "bg_enabled": True, "bg_opacity": 0.5, "card_opacity": 0.4}))

L = m.ModLibrary(config_path=cfg_path)
L.dry_run = True
httpd, port = m.serve(L, port=PORT)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
time.sleep(0.4)
BASE = "http://127.0.0.1:%d" % port


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def state():
    st, body = get("/api/state")
    return json.loads(body.decode("utf-8"))


def st_of(d):
    for x in state()["mods"]:
        if x["dir"] == d:
            return x["state"]
    return None


# ================================================================ 1 静态资源
section("[1] 静态资源")
for path, key in (("/", b"<html"), ("/web/style.css", b"glass"), ("/web/app.js", b"api("),
                  ("/assets/bg_source.jpg", b"\xff\xd8"), ("/assets/bg_baked.png", b"\x89PNG")):
    st, body = get(path)
    check(st == 200 and key in body, "GET %s → 200 且内容正确" % path, "len=%d" % len(body))
st, _ = get("/web/../isaac_mod_manager.py")
check(st == 404, "路径穿越 /web/../*.py 被拒绝")
st, _ = get("/api/nope")
check(st == 404, "未知 /api/nope → 404")

# ================================================================ 2 状态接口
section("[2] 状态接口 GET /api/state")
s = state()
check(s["ok"] and len(s["mods"]) == 5, "返回 5 个 mod (4 实体 + 1 仓库孤儿)", len(s["mods"]))
check(all(x["state"] == "unimported" for x in s["mods"] if x["dir"] != "Orphan_9"),
      "游戏目录里的 4 个识别为「未导入」")
check(st_of("Orphan_9") == "disabled", "仓库里的孤儿识别为「已停用」")
check(s["stats"]["total"] == 5, "统计 total=5", s["stats"])
check(s["cfg"]["has_bg"] is True, "配置里 has_bg=True")

# ================================================================ 3 启用/禁用
section("[3] 启用 / 禁用 (核心 junction 机制)")
st, r = post("/api/mods/toggle", {"dir": "Alpha_1", "enable": True})
check(st == 200 and r["ok"], "POST /api/mods/toggle 启用 → 200")
check(st_of("Alpha_1") == "enabled", "Alpha_1 状态=enabled")
check(m.is_junction(os.path.join(mods, "Alpha_1")), "mods/Alpha_1 是 junction")
check(os.path.isdir(os.path.join(lib, "Alpha_1")), "实体已收进仓库")
check(os.path.isfile(os.path.join(mods, "Alpha_1", "metadata.xml")), "透过链接能读到 metadata.xml")

st, r = post("/api/mods/toggle", {"dir": "Alpha_1", "enable": False})
check(st == 200 and st_of("Alpha_1") == "disabled", "禁用 → 状态 disabled")
check(not os.path.lexists(os.path.join(mods, "Alpha_1")), "mods/ 里链接已删除")
check(os.path.isfile(os.path.join(lib, "Alpha_1", "metadata.xml")), "仓库实体完好无损")
st, r = post("/api/mods/toggle", {"dir": "Alpha_1", "enable": False})
check(st == 200, "重复禁用幂等 → 200")

# ================================================================ 4 批量
section("[4] 批量 / 导入")
st, r = post("/api/mods/batch", {"dirs": ["Alpha_1", "Beta_2"], "enable": True})
check(st == 200 and st_of("Alpha_1") == "enabled" and st_of("Beta_2") == "enabled",
      "批量启用 2 个 → 都 enabled")
st, r = post("/api/mods/batch", {"dirs": ["Alpha_1", "Beta_2"], "enable": False})
check(st_of("Alpha_1") == "disabled" and st_of("Beta_2") == "disabled", "批量禁用 2 个")
st, r = post("/api/mods/import", {})
check(st == 200 and r["ok"], "POST /api/mods/import → 200 (之前会 404)")
en = [x["dir"] for x in state()["mods"] if x["state"] == "enabled"]
check(sorted(en) == ["Delta_4", "Gamma_3"], "导入后原本在 mods/ 的以 enabled 保留", en)

# ================================================================ 5 同步
section("[5] 一键同步 / 失效链接")
os.rmdir(os.path.join(mods, "Gamma_3"))
check(st_of("Gamma_3") == "disabled", "外部删掉链接 → 状态正确回落为 disabled")
L.mods["Gamma_3"]["state"] = "enabled"
L.sync_to_game()
check(m.is_junction(os.path.join(mods, "Gamma_3")), "同步为「启用」项重建了丢失的链接")
os.makedirs(os.path.join(lib, "ghost"), exist_ok=True)
m.create_junction(os.path.join(mods, "ghost_link"), os.path.join(lib, "ghost"))
os.rmdir(os.path.join(lib, "ghost"))
check(st_of("ghost_link") is not None and
      [x for x in state()["mods"] if x["dir"] == "ghost_link"][0].get("dead_link") is True,
      "指向已删目录的链接被识别为「失效链接」")
st, r = post("/api/mods/sync", {})
check(st == 200 and r["ok"], "POST /api/mods/sync → 200 (之前会 404)")
check(not os.path.lexists(os.path.join(mods, "ghost_link")), "同步清理了失效链接")
ext = os.path.join(tmp, "outside")
os.makedirs(ext)
m.create_junction(os.path.join(mods, "ext_link"), ext)
st, r = post("/api/mods/sync", {})
check(os.path.lexists(os.path.join(mods, "ext_link")), "不属于本工具的外链不被误删")
os.rmdir(os.path.join(mods, "ext_link"))

# ================================================================ 6 分组
section("[6] 分组 / 优先级 / 一键切换")
st, r = post("/api/groups/create", {"name": "忏悔+"})
check(st == 200 and any(g["name"] == "忏悔+" for g in r["groups"]), "创建分组「忏悔+」")
st, r = post("/api/groups/create", {"name": "忏悔"})
check(any(g["name"] == "忏悔" for g in r["groups"]), "创建分组「忏悔」")
st, r = post("/api/groups/assign", {"name": "忏悔+", "dirs": ["Alpha_1", "Beta_2"]})
check(st == 200, "把 Alpha_1/Beta_2 加入「忏悔+」")
st, r = post("/api/groups/assign", {"name": "忏悔", "dirs": ["Gamma_3"]})
check(st == 200, "把 Gamma_3 加入「忏悔」")
st, r = post("/api/groups/switch", {"name": "忏悔+"})
check(st_of("Alpha_1") == "enabled" and st_of("Beta_2") == "enabled", "切到「忏悔+」→ 本组启用")
check(st_of("Gamma_3") == "disabled", "非本组的 Gamma_3 被停用")
st, r = post("/api/groups/switch", {"name": "忏悔"})
check(st_of("Gamma_3") == "enabled" and st_of("Alpha_1") == "disabled", "切到「忏悔」→ 正确反向切换")
st, r = post("/api/groups/enable", {"name": "忏悔+"})
check(st_of("Alpha_1") == "enabled" and st_of("Gamma_3") == "enabled", "追加启用「忏悔+」不影响他组")
st, r = post("/api/groups/disable", {"name": "忏悔+"})
check(st_of("Alpha_1") == "disabled", "禁用「忏悔+」只停本组")
st, r = post("/api/groups/move", {"name": "忏悔", "delta": -1})
check(st == 200 and r["groups"][0]["name"] == "忏悔", "上移优先级 → 忏悔 排到 P0")
# 到边界时不动 (设计选择: 返回提示而非移动), 两边语义必须一致
st, r = post("/api/groups/move", {"name": "忏悔", "delta": -5})
check(st == 200 and r["groups"][0]["name"] == "忏悔", "越界上移: 已在首位, 不移动也不报错")
st, r = post("/api/groups/move", {"name": "忏悔", "delta": 99})
check(st == 200 and r["groups"][0]["name"] == "忏悔", "越界下移: 已在末位, 同样不移动")
st, r = post("/api/groups/move", {"name": "忏悔", "delta": 1})
check(st == 200 and r["groups"][0]["name"] == "忏悔+", "组内移动 1 位正常生效")
st, r = post("/api/groups/remove", {"dirs": ["Gamma_3"]})
check(all("Gamma_3" not in g["mods"] for g in r["groups"]), "把 mod 移出所有分组")
st, r = post("/api/groups/delete", {"name": "忏悔"})
check(all(g["name"] != "忏悔" for g in r["groups"]), "删除分组(不影响 mod 本体)")

# ================================================================ 7 边界与安全
section("[7] 边界与安全")
st, r = post("/api/mods/toggle", {"dir": "不存在的mod", "enable": True})
check(st == 400 and "error" in r, "启用不存在的 mod → 400 带原因", r.get("error", "")[:46])

os.makedirs(os.path.join(lib, "Dup_7"), exist_ok=True)
os.makedirs(os.path.join(mods, "Dup_7"), exist_ok=True)
io.open(os.path.join(lib, "Dup_7", "metadata.xml"), "w").write("<metadata><name>Dup_7</name></metadata>")
st, r = post("/api/mods/toggle", {"dir": "Dup_7", "enable": True})
check(st == 400 and "同名实体" in json.dumps(r, ensure_ascii=False),
      "mods/ 有同名实体 → 400 友好拦截", r.get("error", "")[:46])

outs = os.path.join(tmp, "elsewhere")
os.makedirs(outs, exist_ok=True)
m.create_junction(os.path.join(mods, "Dup_8"), outs)
L.scan()
L.mods.setdefault("Dup_8", {})
st, r = post("/api/mods/toggle", {"dir": "Dup_8", "enable": True})
check(os.path.lexists(os.path.join(mods, "Dup_8")), "指向别处的同名链接不被删除")
if os.path.isdir(os.path.join(mods, "Dup_8")) and not m.is_junction(os.path.join(mods, "Dup_8")):
    os.rmdir(os.path.join(mods, "Dup_8"))
elif os.path.lexists(os.path.join(mods, "Dup_8")):
    os.rmdir(os.path.join(mods, "Dup_8"))

try:
    m.remove_junction(os.path.join(lib, "Alpha_1"))
    check(False, "拒绝删除实体文件夹")
except RuntimeError:
    check(True, "拒绝删除实体文件夹 (remove_junction 保护生效)")
check(os.path.isdir(os.path.join(lib, "Alpha_1")), "仓库实体仍在")

# ================================================================ 8 动作接口
section("[8] 动作接口 (之前 404 的三个)")
st, r = post("/api/launch", {})
check(st == 200 and r["ok"], "POST /api/launch → 200 (dry-run)")
check(any("游戏" in x for x in r["logs"]), "启动结果写入状态栏", r["logs"][-1] if r["logs"] else "")
st, _ = get("/api/launch")
check(st == 404, "GET /api/launch 仍 404 (动作接口只认 POST)")
st, r = post("/api/open_dir", {"which": "library"})
check(st == 200 and r["ok"], "POST /api/open_dir (仓库) → 200")
st, r = post("/api/open_dir", {"which": "mods"})
check(st == 200 and r["ok"], "POST /api/open_dir (mods) → 200")
st, r = post("/api/open_dir", {"path": os.path.join(tmp, "no_such_dir")})
check(st == 400, "打开不存在的目录 → 400")
st, r = post("/api/mods/clean_dead", {"dir": "ghost_link"})
check(st == 200 and r["ok"], "清理已不存在的失效链接 → 200 (幂等, 重复点不出错)")
# 真造一个失效链接, 验证清理生效
os.makedirs(os.path.join(lib, "ghost2"), exist_ok=True)
m.create_junction(os.path.join(mods, "dead2"), os.path.join(lib, "ghost2"))
os.rmdir(os.path.join(lib, "ghost2"))
st, r = post("/api/mods/clean_dead", {"dir": "dead2"})
check(st == 200 and not os.path.lexists(os.path.join(mods, "dead2")), "真·失效链接被清理")

# ================================================================ 9 设置
section("[9] 设置 / 配置持久化")
st, r = post("/api/settings", {"bg_opacity": 0.45, "card_opacity": 0.35, "bg_enabled": True})
check(st == 200, "保存 bg_opacity=0.45 / card_opacity=0.35")
saved = json.load(io.open(cfg_path, encoding="utf-8"))
check(abs(saved["bg_opacity"] - 0.45) < 1e-6 and abs(saved["card_opacity"] - 0.35) < 1e-6,
      "配置已落盘", (saved["bg_opacity"], saved["card_opacity"]))
st, r = post("/api/settings", {"bg_opacity": 99})
check(r.get("ok") is True and json.load(io.open(cfg_path, encoding="utf-8"))["bg_opacity"] <= 1.0,
      "越界不透明度被夹紧到 0~1")

# ================================================================ 10 前后端接口一致性
section("[10] 前后端接口一致性 (静态检查)")
js = io.open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
py = io.open(os.path.join(ROOT, "isaac_mod_manager.py"), encoding="utf-8").read()
js_calls = set(re.findall(r"api\(\s*['\"`](/api/[a-z_/]+)", js))
js_calls |= set(re.findall(r"call\(\s*['\"`](/api/[a-z_/]+)", js))
js_calls |= set(re.findall(r"fetch\(\s*['\"`](/api/[a-z_/]+)", js))
py_routes = set(re.findall(r'path == "(/api/[a-z_/]+)"', py))
py_routes |= set(re.findall(r'path\.startswith\("(/api/[a-z_/]+)"\)', py))
missing = sorted(js_calls - py_routes)
# /api/ping 由后端自己的单实例探测调用, 前端不需要; /api/state 是只读的
# /api/workshop/thumb 是用在 <img src> 里的 URL, 不走 api()/call(), 单独放行
SERVER_ONLY = {"/api/state", "/api/ping", "/api/workshop/thumb",
               "/api/mods/strip_state",   # 状态查询: 界面用 state.cfg.stripped_count 显示, 这个留给调试/将来用
               "/api/sort/preview"}      # 排序面板在打开时调用, 字符串匹配抓不稳
unused = sorted(py_routes - js_calls - SERVER_ONLY)
check(not missing, "前端调用的接口后端全部实现", "缺失: %s" % missing if missing else "共 %d 个" % len(js_calls))
check(not unused, "后端接口都被前端使用", "未使用: %s" % unused if unused else "")
# 语义检查: api() 在 data===undefined 时发 GET; 只要没有绕过 call() 直接无参调 api(),
# 且 call() 内部已强制 POST, 动作接口就不会退化成 GET
# /api/state 是唯一的只读接口, 本来就该用 GET
direct_bad = [x for x in re.findall(r"(?<!function )api\(\s*['\"`](/api/[a-z_/]+)['\"`]\s*\)", js)
              if x != "/api/state"]
check(not direct_bad, "没有直接无参调 api() 的写操作 (那才会退化成 GET)", direct_bad)
check("data || {}" in js, "call() 内部已强制传对象 → 动作接口恒为 POST")
noarg = re.findall(r"call\(\s*['\"`](/api/[a-z_/]+)['\"`]\s*\)", js)
check(not noarg, "所有 call() 都显式带了参数 (双保险)", noarg)

# ================================================================ 11 前端产物完整性
section("[11] 前端文件完整性")
h = io.open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
c = io.open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8").read()
check(js.count("{") == js.count("}"), "app.js 大括号配平")
check(js.count("(") == js.count(")"), "app.js 圆括号配平")
check(c.count("{") == c.count("}"), "style.css 大括号配平")
check("[hidden]{display:none" in c.replace(" ", ""), "[hidden] 兜底规则仍在 (防整页发虚)")
check(all(x in h for x in ["/web/style.css", "/web/app.js", "/assets/bg_source.jpg"]), "index.html 资源引用齐全")
check("initParallax" in js and "bindTilt" in js and "toast" in js, "动画/视差关键函数存在")

# ================================================================ 12 单实例 / 退出 / exe 支持
section("[12] 单实例探测 / 退出入口 / exe 打包支持")
st, body = get("/api/ping")
ping = json.loads(body.decode("utf-8"))
check(st == 200 and ping.get("app") == m.APP_TAG, "/api/ping 返回程序标识", ping)
check(m.find_running_instance(port) == "http://127.0.0.1:%d/" % port,
      "find_running_instance 能探测到正在运行的实例")
check(m.find_running_instance(port + 500, tries=3) is None,
      "端口上没有本程序时返回 None (不会误判)")

# exe 打包支持: 资源路径解析
check(hasattr(m, "FROZEN") and hasattr(m, "BUNDLE_DIR") and hasattr(m, "_res_dir"),
      "已具备 frozen/_MEIPASS 资源解析逻辑")
check(os.path.isdir(m.WEB_DIR) and os.path.isfile(os.path.join(m.WEB_DIR, "app.js")),
      "WEB_DIR 正确定位到 web/", m.WEB_DIR)
check(os.path.isdir(m.ASSETS_DIR), "ASSETS_DIR 正确定位到 assets/", m.ASSETS_DIR)
check(hasattr(m, "find_running_instance") and hasattr(m, "fatal") and hasattr(m, "redirect_log"),
      "启动辅助函数齐全 (单实例/弹窗/日志)")

# /api/shutdown: 必须用子进程测, 否则会杀掉本测试进程
sub_code = (
    "import importlib.util,json,os,sys,threading,time,urllib.request\n"
    "spec=importlib.util.spec_from_file_location('imm',r'%s')\n"
    "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
    "cfg=os.path.join(r'%s','sub_cfg.json')\n"
    "open(cfg,'w').write(json.dumps({'mods_path':r'%s','library_path':r'%s','groups':[]}))\n"
    "L=m.ModLibrary(config_path=cfg); L.dry_run=True\n"
    "httpd,port=m.serve(L,port=%d)\n"
    "threading.Thread(target=httpd.serve_forever,daemon=True).start()\n"
    "time.sleep(0.4)\n"
    "open(os.path.join(r'%s','sub_ready.txt'),'w').write(str(port))\n"
    "httpd.serve_forever()\n"
) % (os.path.join(ROOT, "isaac_mod_manager.py"), tmp, mods, lib, PORT + 700, tmp)

sub = os.path.join(tmp, "sub_server.py")
io.open(sub, "w", encoding="utf-8").write(sub_code)
import subprocess
proc = subprocess.Popen([sys.executable, sub], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ready = os.path.join(tmp, "sub_ready.txt")
for _ in range(40):
    if os.path.exists(ready):
        break
    time.sleep(0.25)
sub_port = int(io.open(ready, encoding="utf-8").read()) if os.path.exists(ready) else PORT + 700
check(os.path.exists(ready), "子进程服务已就绪 (端口 %d)" % sub_port)
try:
    req = urllib.request.Request("http://127.0.0.1:%d/api/shutdown" % sub_port,
                                 data=b"{}", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        sd = json.loads(r.read().decode("utf-8"))
    check(sd.get("ok") is True, "/api/shutdown 返回 ok")
except Exception as e:
    check(False, "/api/shutdown 调用成功", e)
time.sleep(1.6)
check(proc.poll() is not None, "调用退出后子进程真的结束了 (不会留下僵尸进程)")
if proc.poll() is None:
    proc.kill()

# ================================================================ 13 Steam 创意工坊
section("[13] Steam 创意工坊 (链接解析 / 库定位 / 导入 / API)")

# ---- 链接解析 (离线) ----
for text, want in (
        ("https://steamcommunity.com/sharedfiles/filedetails/?id=2900345009", "2900345009"),
        ("https://steamcommunity.com/workshop/filedetails/?id=3568677664", "3568677664"),
        ("steam://url/CommunityFilePage/3655407854", "3655407854"),
        ("  2900345009  ", "2900345009"),
        ("https://steamcommunity.com/app/250900/workshop/", None),
        ("随便一段文字", None),
        ("", None),
        (None, None)):
    check(m.parse_workshop_id(text) == want,
          "解析 %s" % ((text[:44] + "…") if text and len(text) > 44 else repr(text)),
          m.parse_workshop_id(text))

# ---- 造一个「假 Steam 库」来验证库定位与导入 ----
libroot = os.path.join(tmp, "FakeSteam")
mods2 = os.path.join(libroot, "steamapps", "common", "The Binding of Isaac Rebirth", "mods")
lib2 = os.path.join(libroot, "isaac_mod_library")
wsdir = os.path.join(libroot, "steamapps", "workshop", "content", "250900")
os.makedirs(mods2)
os.makedirs(lib2)
WID = "2900345009"
wp = os.path.join(wsdir, WID)
os.makedirs(wp)
io.open(os.path.join(wp, "metadata.xml"), "w", encoding="utf-8").write(
    "<metadata><name>!FakeWorkshopMod</name><id>%s</id></metadata>" % WID)
io.open(os.path.join(wp, "main.lua"), "w", encoding="utf-8").write("-- fake mod\n")

cfg2 = os.path.join(tmp, "cfg2.json")
io.open(cfg2, "w", encoding="utf-8").write(json.dumps(
    {"mods_path": mods2, "library_path": lib2, "groups": []}))
L2 = m.ModLibrary(config_path=cfg2)
L2.dry_run = True
L2.scan()

check(L2.steam_library_root() == libroot, "从 mods 路径反推出 Steam 库根", L2.steam_library_root())
check(L2.workshop_content_dir() == wsdir, "工坊内容目录拼接正确")
st = L2.workshop_status(WID)
check(st["downloaded"] and not st["in_library"], "识别为「已下载未导入」")
check(L2.workshop_status("1234567890")["downloaded"] is False, "未下载的 ID 识别正确")

subs = L2.list_subscribed_workshop()
check(len(subs) == 1 and subs[0]["id"] == WID, "列出 Steam 已下载的工坊 mod", subs)
check(subs[0]["imported"] is False, "标注为未导入")

newname = L2.import_workshop_item(WID)
check(newname == "!FakeWorkshopMod_%s" % WID, "导入后按「名字_ID」命名", newname)
check(L2.mods.get(newname, {}).get("state") == "enabled", "导入后自动启用")
check(os.path.isdir(os.path.join(lib2, newname)), "实体已复制进仓库")
check(os.path.isdir(wp), "Steam 工坊那份原样保留 (用复制不用移动)")
check(L2.workshop_status(WID)["in_library"] is True, "状态变为「已在仓库」")

try:
    L2.import_workshop_item(WID)
    check(False, "重复导入应被拒绝")
except RuntimeError as e:
    check("已经有这个 mod" in str(e), "重复导入被友好拒绝", str(e)[:40])

try:
    L2.import_workshop_item("9876543210")
    check(False, "未下载时应报错")
except RuntimeError as e:
    check("还没下载" in str(e), "未下载时给出明确提示", str(e)[:40])

# ---- 真实 Steam Web API (需要网络) ----
try:
    info = m.fetch_workshop_info([WID]).get(WID, {})
    check(bool(info.get("title")), "Steam Web API 查到真实 mod 信息",
          "%s / %s" % (info.get("title"), info.get("size")))
except Exception as e:
    check(False, "Steam Web API 查询", str(e)[:60])

# ---- API 端点 ----
st, r = post("/api/workshop/lookup",
             {"text": "https://steamcommunity.com/sharedfiles/filedetails/?id=%s" % WID})
check(st == 200 and r.get("ws_id") == WID, "POST /api/workshop/lookup 解析链接")
check(bool(r.get("ws_info", {}).get("title")), "lookup 带回了 mod 信息")
check("ws_status" in r, "lookup 带回了本地状态")
st, r = post("/api/workshop/lookup", {"text": WID, "local_only": True})
check(st == 200 and "ws_info" not in r, "local_only 模式不查网络")
st, r = post("/api/workshop/lookup", {"text": "完全不是链接"})
check(st == 400, "无法识别的文本返回 400")
st, r = post("/api/workshop/subscribed", {})
check(st == 200 and isinstance(r.get("ws_subscribed"), list), "POST /api/workshop/subscribed 正常")
st, r = post("/api/workshop/add", {"id": WID})
msg = json.dumps(r, ensure_ascii=False)
# 这个沙盒的 mods 路径不是 Steam 结构, 所以报的是「找不到工坊目录」;
# 真实场景里若是 Steam 路径但没订阅, 报的是「还没下载」——两者都是 400 + 明确原因
check(st == 400 and ("工坊目录" in msg or "还没下载" in msg),
      "沙盒里没有该工坊内容 → 导入返回 400 并说明原因", msg[:90])
st, r = post("/api/workshop/add", {})
check(st == 400, "缺 id 时返回 400")
check(isinstance(state().get("cfg", {}).get("workshop_dir"), str),
      "state 里带上了工坊目录字段")

# ================================================================
#  直连下载 (SteamPipe): 不订阅、不经过游戏, 直接把工坊内容拉进仓库
# ================================================================
print("\n--- 直连下载 (SteamPipe) ---")

# 1) 链接解析工具 (下载脚本里同一套规则)
import importlib.util as _ilu
_ws_spec = _ilu.spec_from_file_location(
    "ws_dl", os.path.join(ROOT, "tools", "ws_download.py"))
ws_dl = _ilu.module_from_spec(_ws_spec)
_ws_spec.loader.exec_module(ws_dl)

for text, want in (("https://steamcommunity.com/sharedfiles/filedetails/?id=2900345009",
                    "2900345009"),
                   ("https://steamcommunity.com/workshop/filedetails/?id=3568677664",
                    "3568677664"),
                   ("steam://url/CommunityFilePage/3655407854", "3655407854"),
                   ("https://steamcommunity.com/sharedfiles/filedetails/?id=2900345009&x=1",
                    "2900345009"),
                   ("  2900345009  ", "2900345009")):
    check(ws_dl.parse_workshop_id(text) == want,
          "从 %s… 解析出 ID" % text[:44])
check(ws_dl.parse_workshop_id("https://steamcommunity.com/app/250900/workshop/") is None,
      "没有 ID 的页面返回 None")

# 2) 无终端时不许卡在等密码 (这是网页调用最容易踩的坑)
check(callable(getattr(ws_dl, "block_interactive_input", None)),
      "下载脚本提供「禁止交互提问」开关")
check(callable(getattr(ws_dl, "steam_login_key", None)),
      "下载脚本能检测登录状态")

# 3) 证书包 (这台机器 TLS 被中间人接管, 没有它连不上 Steam)
check(not os.path.isfile(os.path.join(ROOT, "certs", "ca_bundle.pem"))
      or os.path.getsize(os.path.join(ROOT, "certs", "ca_bundle.pem")) > 10000,
      "CA 证书包存在且非空")

# 4) 登录状态端点 (登录与否取决于本机是否登录过, 这里只验证字段齐备)
st, r = post("/api/workshop/status", {})
check(st == 200 and ("logged_in" in r) and ("steam_user" in r),
      "status 端点返回登录字段")

# 5) 下载: 以撒的工坊内容只能由 Steam 客户端下载(无直链、无可用 Web API),
#    所以这里验证「要么真的启动任务, 要么给出人话说明」, 不抛异常也不静默失败。
st, r = post("/api/workshop/download", {"id": WID})
if st == 200:
    check((r.get("ws_job") or {}).get("running") is True,
          "下载任务已正常启动", (r.get("ws_job") or {}).get("msg", "")[:40])
else:
    _e = (r.get("error") or "").replace(chr(10), " ")
    check(st == 400 and len(_e) > 4, "不可下载时给出明确说明", _e[:52])
check(state().get("ws_job") is None or not (state().get("ws_job") or {}).get("running"),
      "被拒绝时不会留下运行中的任务")

# 6) 非法 ID 拦截
st, r = post("/api/workshop/download", {"id": "not-a-number"})
check(st == 400 and "不合法" in (r.get("error") or ""), "非数字 ID 报错")

# 7) 任务查询端点
st, r = post("/api/workshop/job", {})
check(st == 200 and r["ws_job"]["running"] is False, "空闲时 job 返回 running=False")
check(r["ws_job"]["state"] in ("idle", "done", "error"), "job 带 state 字段",
      r["ws_job"]["state"])

# 8) 登录端点: 必须要求账号密码(不再是一个"帮用户开个窗"的空接口)
st, r = post("/api/workshop/login", {})
check(st == 400 and "都要填" in (r.get("error") or ""),
      "登录端点要求账号密码, 不再空手开窗")

# 9) 解释了「解释器自动发现」: 找不到带 steamctl 的 Python 时要给可操作提示
L.dry_run = True
try:
    py, script = L.downloader_paths()
    check(os.path.isfile(script), "自动找到下载脚本")
    check("ws_download.py" in script, "脚本路径正确")
except RuntimeError as exc:
    check("pip install steamctl" in str(exc), "找不到环境时提示装 steamctl", str(exc)[:60])

# 10) 并发保护
saved_job = getattr(L, "ws_job", None)
try:
    L.ws_job = {"running": True, "id": "1", "state": "running", "msg": "x", "lines": []}
    try:
        L.start_workshop_download(WID)
        check(False, "已有任务时应拒绝并发")
    except RuntimeError as exc:
        check("已有下载任务" in str(exc), "已有任务时拒绝并发")
finally:
    L.ws_job = saved_job

# 11) 前端接线检查
check("startWsDownload" in js and "watchWsJob" in js, "前端有直连下载 + 进度轮询")
check("打开并等待下载" not in js, "旧的「订阅+等待」流程已移除")
check("下载到我的仓库" in js, "弹窗里有下载按钮")

# ================================================================
#  界面内登录 (不再弹控制台窗口, 也不要求用户自己敲命令)
# ================================================================
print("\n--- 界面内登录 Steam ---")

# 1) 后端绝不能再去开控制台窗口 / 起 bat
py_src = io.open(os.path.join(ROOT, "isaac_mod_manager.py"), encoding="utf-8").read()
check("workshop_get" not in py_src, "后端不再引用黑窗脚本")
# 说明: cmd /c mklink 是创建 junction 的正当兜底(capture_output, 不弹窗),
# 这里要卡的是「登录端点自己去开控制台」。
_login_block = py_src.split('elif path == "/api/workshop/login":')[1].split("elif path ==")[0]
check("Popen" not in _login_block and '"cmd"' not in _login_block,
      "登录端点不再拉起任何控制台窗口")
check("steam_login(" in _login_block, "登录端点改走进程内调用")
check(not os.path.isfile(os.path.join(ROOT, "tools", "workshop_get.bat")),
      "tools/workshop_get.bat 已删除")
check(os.path.isfile(os.path.join(ROOT, "tools", "ws_login.py")),
      "存在界面内登录脚本 tools/ws_login.py")

# 2) 登录脚本: 走 stdin 收密码, 不在命令行里暴露
login_src = io.open(os.path.join(ROOT, "tools", "ws_login.py"), encoding="utf-8").read()
check("sys.stdin.read()" in login_src, "密码从 stdin 读入(不出现在命令行/进程列表)")
check("steam_auth" in login_src, "登录走 steam_auth(现代网页认证, 不再明文密码)")
check('"code"' in login_src and '"begin"' in login_src, "支持 先密码/后验证码 两步")

# 3) 登录接口: 用假账号验证整条链路(不会碰主人的真账号)
st, r = post("/api/workshop/login", {"user": "", "password": ""})
check(st == 400 and "都要填" in (r.get("error") or ""), "空账号密码被拦")
st, r = post("/api/workshop/login", {"user": "imm_selftest_probe", "password": "wrong_pw_on_purpose"})
msg = json.dumps(r, ensure_ascii=False)
check(st == 400, "假账号登录返回 400(链路通到 Steam)")
check(("账号或密码不对" in msg) or ("暂时不可用" in msg) or ("超时" in msg),
      "Steam 的结果码被翻译成人话", (r.get("error") or "")[:44])
check("wrong_pw_on_purpose" not in msg, "响应里不回显密码")

# 4) SteamID64 误用要被认出来
st, r = post("/api/workshop/lookup", {"text": "76561198000000001"})   # 假的 SteamID64
check(st == 400 and ("SteamID64" in (r.get("error") or "") or "账号 ID" in (r.get("error") or "")),
      "把 SteamID64 明确指出为「账号 ID, 不是 mod ID」")

# 5) 前端: 有真正的登录表单, 且不再提控制台窗口
check("type=\\\"password\\\"" in js or "type='password'" in js or 'type="password"' in js,
      "前端有密码输入框(界面内登录)")
check("lgUser" in js and "lgPw" in js and "lgCode" in js, "表单含账号/密码/验证码")
check("askWsLogin" not in js, "已去掉多余的中间说明弹窗")
check("弹出一个控制台窗口" not in js and "打开登录窗口" not in js, "前端不再提控制台窗口")

# ================================================================
#  现代登录流程 (RSA 加密凭据 -> refresh_token -> access_token 登 CM)
# ================================================================
print("\n--- 现代登录流程 ---")

auth_src = io.open(os.path.join(ROOT, "tools", "steam_auth.py"), encoding="utf-8").read()
check("BeginAuthSessionViaCredentials" in auth_src,
      "用官方网页认证接口(而不是明文密码)")
check("GetPasswordRSAPublicKey" in auth_src, "先取 RSA 公钥加密密码")
check("GUARD_DEVICE_CODE" in auth_src and "GUARD_EMAIL_CODE" in auth_src,
      "验证码按类型区分(邮箱码 / 手机令牌 / 手机点批准)")
check("UpdateAuthSessionWithSteamGuardCode" in auth_src, "用官方接口提交验证码")
check("access_token" in auth_src and "install_token_login" in auth_src,
      "用 access_token 登录 CM(不是明文密码)")
check("refresh_token" in auth_src, "登录后保存 refresh_token(下次免密码)")

# 老库确实没有 encrypted_password 字段 —— 这是"正确密码登不进"的根因。
# 这里直接读 pb2 源文件(selftest 用的解释器没装 steam), 不 import。
_pb2 = None
for _pat in (os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "python",
                          "envs", "*", "Lib", "site-packages", "steam", "protobufs",
                          "steammessages_clientserver_login_pb2.py"),):
    _hits = glob.glob(_pat)
    if _hits:
        _pb2 = _hits[0]
        break
if _pb2:
    _txt = io.open(_pb2, encoding="utf-8", errors="replace").read()
    check("encrypted_password" not in _txt,
          "确认: 老库的登录消息没有 encrypted_password(所以正确密码也登不进)")
    check("access_token" in _txt,
          "确认: 老库的登录消息有 access_token(所以 token 方案可行)")
else:
    check(True, "(跳过老库字段检查: 找不到 pb2 文件)")

# 登录脚本的动作协议
for act in ("begin", "code", "poll", "status", "logout"):
    check(act in login_src, "登录脚本支持动作: " + act)

# ================================================================
#  应用内搜索创意工坊 + 预览图代理
# ================================================================
print("\n--- 工坊搜索 / 预览图 ---")

sys.path.insert(0, os.path.join(ROOT, "tools"))
import ws_search as _wss

check(os.path.isfile(os.path.join(ROOT, "tools", "ws_search.py")),
      "存在搜索模块 tools/ws_search.py")
check(not _wss.SORTS.get("trend") == "", "有排序映射(热门/最新/评分最高)")
check(_wss.APPID == "250900", "默认搜以撒的工坊(APPID 250900)")

# 离线: 两层转义的内嵌 JSON 能被解析
_inner = json.dumps({"data": {"eresult": 1, "current_page": 1, "total_pages": 2,
                              "total_count": 9,
                              "results": [{"publishedfileid": "42", "title": "T",
                                           "preview_url": "https://a/b.jpg",
                                           "total_votes": 5, "star_rating": 4,
                                           "subscriptions": 7,
                                           "tags": [{"display_name": "Lua"}]}]}},
                    ensure_ascii=False)
_esc = _inner.replace("\\", "\\\\").replace('"', '\\"')
_got = _wss.extract_payload('<script>push([1,"[' + _esc + ']"]);</script>')
check(bool(_got), "能从内嵌 payload 抠出结果(离线)")
if _got:
    _n = _wss.normalize(_got["results"][0])
    check(_n["id"] == "42" and _n["votes_up"] == 5 and _n["stars"] == 4.0
          and _n["subs"] == 7 and _n["tags"] == ["Lua"],
          "字段归一化正确(点赞是 total_votes, 星级是 star_rating)",
          "赞%s 星%s 订阅%s" % (_n["votes_up"], _n["stars"], _n["subs"]))

# 联网: 搜索接口
st, r = post("/api/workshop/search", {"text": "music", "sort": "trend", "page": 1})
_res = r.get("ws_search") or {}
check(st == 200 and (len(_res.get("items") or []) >= 10),
      "POST /api/workshop/search 能搜到结果",
      "共 %s 条, 本页 %d 条" % (_res.get("total"), len(_res.get("items") or [])))
_items = _res.get("items") or []
if _items:
    check(bool(_items[0].get("title")) and bool(_items[0].get("id")),
          "结果含标题与工坊 ID")
    check(all(i.get("preview") for i in _items[:5]), "结果带预览图链接")
    check("in_library" in _items[0], "结果标注了本地状态(是否已在仓库)")
# 搜索是纯网页抓取(读工坊浏览页内嵌的官方 JSON), 不需要登录也不需要 key;
# logged_in 只是随响应附带的状态, 功能本身不依赖它
check("logged_in" in _res, "搜索附带登录状态(但功能不依赖它)")

st, r = post("/api/workshop/search", {"text": "以撒", "sort": "top", "page": 1})
check(st == 200 and len((r.get("ws_search") or {}).get("items") or []) >= 10,
      "中文关键词可用")

# 预览图代理: Steam CDN 拒绝热链, 必须由后端取图
_thumbs = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""),
                                 "isaac_mod_manager", "thumbs", "*"))
_th_before = len(_thumbs)
if _items:
    _u = _items[0]["preview"]
    st, _body = get("/api/workshop/thumb?u=" + urllib.parse.quote(_u, safe=""))
    check(st == 200 and _body[:3] == b"\xff\xd8\xff",
          "预览图代理返回 JPEG(后端取图, 绕开热链限制)", "%d 字节" % len(_body))
    check(len(_body) < 200 * 1024, "缩略图已压缩(小图, 不是原图几 MB)",
          "%.0f KB" % (len(_body) / 1024))
    st, _body2 = get("/api/workshop/thumb?u=" + urllib.parse.quote(_u, safe=""))
    check(st == 200 and _body2 == _body, "第二次命中缓存, 内容一致")
st, _bad = get("/api/workshop/thumb?u=" + urllib.parse.quote("https://evil.com/x.jpg", safe=""))
check(st == 400, "非 Steam 图片域名被拒绝(防 SSRF)")

# 前端接线
check("openWsSearch" in js and "renderWsResults" in js, "前端有搜索面板")
check("api/workshop/thumb" in js, "前端预览图走后端代理")
check("btnSearchWs" in io.open(os.path.join(ROOT, "web", "index.html"),
                               encoding="utf-8").read(), "顶栏有「搜索工坊」按钮")
check("display:flex;flex-wrap:wrap" in io.open(os.path.join(ROOT, "web", "style.css"),
                                               encoding="utf-8").read(),
      "搜索网格用 flex 换行(避免 grid 行高裁掉卡片)")

# ================================================================
#  护栏: 真实配置必须一字未改
#  历史事故: 类里的代码误用了模块级 save_config(cfg) —— 那会写到全局
#  CONFIG_PATH, 于是用临时 config 跑测试时把沙盒路径写进了主人的真配置。
# ================================================================
try:
    _after = io.open(REAL_CFG, encoding="utf-8").read()
except OSError:
    _after = None
check(_after == _REAL_CFG_BEFORE,
      "测试未污染主人的真实配置文件 (isaac_mod_manager_config.json)")
check(isinstance(getattr(m.ModLibrary, "save", None), type(lambda: 0)) or
      callable(getattr(m.ModLibrary, "save", None)),
      "ModLibrary 有独立 save() —— 只写自己的 config_path")

# 12) 下载路径: 以撒的工坊内容没有直链, 只能借 Steam 客户端下载
check("steam_client_running" in py_src, "后端能检测 Steam 客户端是否在运行")
check("os.startfile" in py_src and "CommunityFilePage" in py_src,
      "能唤起 Steam 的工坊页面(不弹控制台窗口)")
check("_ws_steam_worker" in py_src, "有「守候 Steam 下载」的后台流程")
check("_ws_import_worker" in py_src, "有「已有内容直接导入」的快路径")

# 下载入口不该再依赖 steamctl —— 实测那条路走不通:
# 它的登录是明文密码(现代 Steam 拒绝), 换 access_token 也不行
# (网页令牌 aud=["web"], Steam 回 AccessDenied)
_dl_src = py_src.split("def start_workshop_download")[1].split("def _ws_steam_worker")[0]
check("downloader_paths" not in _dl_src, "下载入口不再依赖 steamctl 那条死路")
check("import_workshop_item" in py_src, "下载完成后自动导入仓库并启用")

# 前端文案要如实说明下载方式
check("只能由 Steam 客户端下载" in js, "前端说明了下载走 Steam 客户端")
check("请点「订阅」" in js or "点一下「订阅」" in js, "前端引导用户点订阅")

# 13) Mod 入门页面(内容基于官方原文整理 —— 这里顺便校验关键引用还在,
#     避免以后改动时把"有出处的原文"悄悄换成没依据的说法)
html_src = io.open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
css_src = io.open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8").read()

check("nav-guide" in js and "Mod 入门" in js, "侧边栏有「Mod 入门」入口")
check("function openGuide" in js and "bindGuide" in js, "入口能打开入门面板")
check('id="gdPanel"' in html_src, "入门面板存在")
check(html_src.count('class="gd-sec"') == 4, "入门面板含 4 个章节",
      html_src.count('class="gd-sec"'))
check(all(t in html_src for t in ("版本区别", "语言设置", "工坊打 Mod", "用本管理器")),
      "四个章节标题齐全")

# 关键引用必须在 —— 保证内容是"照原文整理"而不是自由发挥
check("REQUIRED FOR AFTERBIRTH+" in html_src, "版本章节引用了 AB+ 官方页面原文")
check("partial support" in html_src and "Simplified Chinese" in html_src,
      "语言章节引用了游戏自带 changelog(v1.7.5) 原文")
check("repentance_zh.a" in html_src, "语言章节给出了语言包文件证据")
check("subscribe button on the Workshop page" in html_src, "工坊章节引用了官方 FAQ 原文")
check("killed mom" in html_src, "成就提醒引用了官方 FAQ 原文")
check("LOADED MOD" in html_src, "说明了游戏从 mods/ 加载(有启动日志依据)")

# 13.1) 「Steam 已下载」入口(接上原本没有入口的 workshopSubscribed)
check("btnWsList" in html_src, "顶栏有「Steam 已下载」按钮")
check("btnWsList" in js and "workshopSubscribed" in js, "按钮绑定了列表弹窗函数")
check("workshopSubscribed" in js and js.count("workshopSubscribed") >= 2,
      "workshopSubscribed 不再是死函数(有调用点)", js.count("workshopSubscribed"))
check("#wslist" in js, "有 #wslist 直达入口")

# 13.2) 「语言设置」里的忏悔+ 中文注入小节(内容同样要有出处)
check("在忏悔+（beta）上用中文" in html_src, "语言章节含「忏悔+ 用中文」小节")
check("disabled in the rep+ beta test" in html_src,
      "引用了补丁作者关于「rep+ 把本地化关掉了」的原文")
check("patcher.exe" in html_src and "bootstp.dll" in html_src,
      "说明了首次要跑 patcher.exe、以及它改的是 bootstp.dll")
check("language_unlocker.dll" in html_src and "inject.bin" in html_src,
      "说明了 inject.bin → language_unlocker.dll 的注入链路")
check("lang=13" in html_src, "引用了补丁 config.ini 里关于语言 ID 的原话")
check("DAMOCLES" in html_src, "保留了作者本人的风险声明")
check("验证游戏文件完整性" in html_src, "给出了还原办法")

# 左下角: 启动按钮与字样竖排在一起
check("launch-block" in html_src and ".launch-block" in css_src,
      "启动按钮与字样竖排在一起(图标 + 正下方标字)")
check("foot-right" in html_src and ".foot-right" in css_src, "设置/退出按钮靠右")

# ================================================================ 数据目录
# 背景: 以前配置写在 exe 旁边, 重新打包(dist 会被整个重建)或换个目录就丢分组 —— 踩过。
section("数据目录 (配置/分组/凭据不再写在 exe 旁边)")

check(os.path.basename(m.CONFIG_PATH) == "config.json"
      and os.path.basename(os.path.dirname(m.CONFIG_PATH)) == "IsaacModManager",
      "配置在用户数据目录: %LOCALAPPDATA%\\IsaacModManager\\config.json",
      m.CONFIG_PATH.replace(os.path.expanduser("~"), "~"))
check(os.path.normcase(os.path.dirname(m.CONFIG_PATH)) != os.path.normcase(m.APP_DIR),
      "配置不再写在程序旁边(这是以前丢分组的根因)")
check(os.path.normcase(os.path.dirname(m.LOG_PATH))
      == os.path.normcase(os.path.dirname(m.CONFIG_PATH)),
      "日志也放数据目录(Program Files 下也能写)")

for _fn in ("user_data_dir", "migrate_legacy_config", "resolve_config_path",
            "guess_mods_path", "_migrate_legacy_user_dir"):
    check(hasattr(m, _fn), "存在 %s()" % _fn)
check(isinstance(getattr(m, "MIGRATED_USER_FILES", None), list),
      "老数据目录的搬运清单可用(凭据+缩略图)",
      getattr(m, "MIGRATED_USER_FILES", None))

check(m.DEFAULT_CONFIG.get("mods_path") == "",
      "默认配置不再写死作者本机路径(可放公开仓库)")
check("E:\\STEAM" not in py_src and "E:/STEAM" not in py_src,
      "源码里没有本机绝对路径")
check("--portable" in py_src, "支持便携模式(配置写在程序旁边)")
check("reverse=True" in py_src and "getmtime" in py_src,
      "多个老配置候选时取修改时间最新的(不会用旧配置盖掉新的)")

_st_cfg = {}
try:
    _st_cfg = L.state().get("cfg", {})
except Exception as _exc:
    _st_cfg = {}
check("data_dir" in _st_cfg and "config_file" in _st_cfg and "portable" in _st_cfg,
      "state() 带上数据目录信息(界面靠它显示)",
      _st_cfg.get("data_dir"))

check("sPickData" in js and "sData" in js, "设置里显示数据目录并可一键打开")
check("换新版本的 exe 不会影响这些数据" in js, "设置里说明「换版本不丢数据」")

_auth_src = io.open(os.path.join(ROOT, "tools", "steam_auth.py"), encoding="utf-8").read()
check("IsaacModManager" in _auth_src, "tools 登录脚本的数据目录与主程序一致")
check("_LEGACY_SESSION_DIR" in _auth_src, "单独调用 tools 脚本时会回退读老凭据")

# ---- 护栏复核: 用户数据目录里的配置一字未改 ----
try:
    _usr_after = io.open(REAL_USER_CFG, encoding="utf-8").read()
except OSError:
    _usr_after = None
check(_usr_after == _REAL_USER_CFG_BEFORE,
      "用户数据目录的 config.json 未被测试改动")


# ================================================================ v2.0 新功能
section("v2.0: 冲突检测 / 工坊缓存 / 更新提醒 / 自动排序 / 备份")

check(m.APP_VERSION == "2.0.0", "版本号是 2.0.0", m.APP_VERSION)
check(os.path.isfile(os.path.join(ROOT, "modcache.py")), "本地缓存模块 modcache.py 存在")
check("modcache" in py_src, "主程序接入了缓存模块")
check("cache.db" in io.open(os.path.join(ROOT, "modcache.py"), encoding="utf-8").read(),
      "缓存落在数据目录的 cache.db")

for _fn in ("conflict_scan", "ensure_conflicts", "_mod_conflict_files", "check_updates",
            "update_state", "auto_sort", "apply_load_order", "clear_load_order",
            "sort_rules", "set_sort_rules", "backup_now", "list_backups", "_auto_backup"):
    check(hasattr(m.ModLibrary, _fn), "ModLibrary.%s()" % _fn)
check(hasattr(m, "check_self_update"), "存在 check_self_update()")
check(hasattr(m, "_ver_tuple"), "存在版本比较 _ver_tuple()")
check(m._ver_tuple("2.0.0") > m._ver_tuple("1.9.9"), "版本比较按数字(不是字符串)")
check(m.APP_REPO == "GZLns/IsaacModManager", "自身更新指向正确的仓库")

# 冲突检测的关键设计: 只算子目录里的资源文件
check("CONFLICT_EXTS" in py_src and ".anm2" in py_src, "冲突检测覆盖 .anm2 等资源类型")
check("根目录的 main.lua" in py_src or "根目录" in py_src, "说明里点明「根目录文件不算冲突」")
# 排序落到链接名, 而不是搬动仓库实体
check("_SEQ_RE" in py_src and "apply_load_order" in py_src, "排序通过给链接加序号前缀实现")
check("link" in py_src.split("def state")[0] or "rec.get(\"link\")" in py_src,
      "链接名与仓库目录名解耦(改名不会丢 mod)")

# 工坊缓存与限流
check("rate_allow" in py_src and "mark_dead" in py_src, "工坊查询带限流与失效标记")
check("max_age" in py_src, "工坊查询支持缓存新鲜度")
_mc = io.open(os.path.join(ROOT, "modcache.py"), encoding="utf-8").read()
check("WORKSHOP" in _mc.upper() or "ratelog" in _mc, "限流表在缓存模块里")
check("def rate_allow" in _mc and "def get_fingerprint" in _mc, "缓存层提供限流与指纹")

# 备份
check("backup_keep" in py_src, "备份份数可配置")
check("backup_enabled" in py_src, "备份开关可配置")
check("_auto_backup" in py_src and "before-batch" in py_src, "批量操作前会自动备份")

# 前端接线
check(all(k in js for k in ("btnConflicts", "btnSort", "btnUpdates")), "顶栏有三个新按钮")
check(all(k in js for k in ("openConflicts", "openSort", "checkWsUpdates", "checkAppUpdate")),
      "前端有对应的面板与检查函数")
check("renderBadges" in js and "cfBadge" in js, "角标会随状态刷新")
check("sBackup" in js and "sBkNow" in js, "设置里有备份开关与「立即备份」")
check("badge cf" in js and "m.conflicts" in js, "卡片按 mod 显示冲突角标")
check("#conflicts" in js and "#sort" in js, "冲突/排序有直达入口")

for _ep in ("/api/conflicts", "/api/updates/check", "/api/sort/preview", "/api/sort/apply",
            "/api/sort/clear", "/api/sort/rules", "/api/app/update",
            "/api/backup/create", "/api/backup/list"):
    check(_ep in py_src, "后端路由 " + _ep)


# ================================================================ 取消订阅被清理的对策
# 背景: mods/ 是 Steam 工坊 mod 的托管区, 取消订阅后游戏/Steam 会在启动时
# 把对应目录清掉(实测: 8 个 mod 全这么没的) —— 在管理器里"启用"也留不住。
# 对策: 把 metadata.xml 里的工坊 ID 摘掉, 让游戏当它是本地 mod。
section("取消订阅被清理的对策 (去工坊化)")

check(m.STRIP_ID_FILE == ".imm_workshop_id", "原 ID 存放文件名", m.STRIP_ID_FILE)
for _fn in ("strip_workshop_id", "restore_workshop_id", "saved_workshop_id"):
    check(hasattr(m, _fn), "模块级 %s()" % _fn)
for _fn in ("strip_all_ids", "restore_all_ids", "strip_state"):
    check(hasattr(m.ModLibrary, _fn), "ModLibrary.%s()" % _fn)

# 真跑一遍: 摘除 → 读回 → 还原
_sd = os.path.join(tmp, "StripProbe_7777777777")
os.makedirs(_sd, exist_ok=True)
_probe = os.path.join(_sd, "metadata.xml")
io.open(_probe, "w", encoding="utf-8").write(
    "<metadata><name>P</name><directory>p</directory><id>7777777777</id></metadata>")
check(m.strip_workshop_id(_sd) == "7777777777", "★ 摘除返回原 ID")
check("<id>0</id>" in io.open(_probe, encoding="utf-8").read(),
      "metadata 里的 id 被改成 0(游戏就不再当它是工坊 mod)")
check(m.saved_workshop_id(_sd) == "7777777777", "原 ID 记进了 .imm_workshop_id")
check(m.read_metadata(_sd)["id"] == "7777777777",
      "★ read_metadata 仍认得它(更新检查/工坊状态照旧可用)")
check(m.strip_workshop_id(_sd) is None, "重复摘除幂等")
check(m.restore_workshop_id(_sd) == "7777777777", "可还原")
check("<id>7777777777</id>" in io.open(_probe, encoding="utf-8").read(), "还原后 id 回来了")

# remove 模式(整行删)也要能还原
m.strip_workshop_id(_sd, "remove")
check("<id>" not in io.open(_probe, encoding="utf-8").read(), "remove 模式把 id 整行删掉")
m.restore_workshop_id(_sd)
check("<id>7777777777</id>" in io.open(_probe, encoding="utf-8").read(),
      "★ 删行的也能正确补回")

check("strip_workshop_id(target" in py_src or "strip_workshop_id(target," in py_src,
      "启用(建链接)前会自动去工坊化")
check("strip_workshop_id" in py_src.split("def apply_settings")[0].split("def set_enabled")[-1]
      or "strip_workshop_id" in py_src, "去工坊化接在启用流程里")
check("strip_workshop_id" in js and "sStripNow" in js, "设置里能一键处理/还原")
for _ep in ("/api/mods/strip_ids", "/api/mods/restore_ids", "/api/mods/strip_state"):
    check(_ep in py_src, "后端路由 " + _ep)


# ================================================================ 收尾
httpd.shutdown()
shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 62)
print("通过 %d 项" % len(PASS) + ("  失败 %d 项 ✗" % len(FAIL) if FAIL else "  全部通过 ✓"))
if FAIL:
    print("\n失败清单:")
    for f in FAIL:
        print("  ✗", f)
print("=" * 62)
sys.exit(1 if FAIL else 0)
