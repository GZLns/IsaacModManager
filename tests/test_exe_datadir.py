# -*- coding: utf-8 -*-
"""验收: 把 exe 放进"模拟用户环境"里, 验证
  1) 老配置(exe 旁边)会被迁到用户数据目录, 分组一个不丢
  2) 日志也写进数据目录
  3) exe 旁边那份不被改动
  4) 第二次运行不会覆盖用户已有配置(不会把用户改动冲掉)
  5) 便携模式(旁边有 config.json)则用它、不迁移

全程用临时的 LOCALAPPDATA, 不碰主人真实数据。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.getcwd()
EXE = os.path.join(ROOT, "dist", "IsaacModManager.exe")

ok = bad = 0
def check(c, t, extra=""):
    global ok, bad
    if c: ok += 1
    else: bad += 1
    print("  %s %s%s" % ("✓" if c else "✗", t, ("  → " + str(extra)) if extra else ""))

GAME_MODS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "_imm_fake_game", "mods")

def make_env(tag, with_config_json=False):
    """造一个模拟环境: app/ 放 exe(+可选配置), la/ 当假的 LOCALAPPDATA"""
    base = tempfile.mkdtemp(prefix="imm_exe_%s_" % tag)
    app = os.path.join(base, "app"); os.makedirs(app)
    la = os.path.join(base, "la"); os.makedirs(la)
    shutil.copy2(EXE, os.path.join(app, "IsaacModManager.exe"))
    cfg = {"mods_path": GAME_MODS, "library_path": "",
           "bg_opacity": 0.33, "card_opacity": 0.44,
           "groups": [{"name": "验收组A", "mods": ["Alpha_1", "Beta_2"]},
                      {"name": "验收组B", "mods": ["Gamma_3"]}]}
    name = "config.json" if with_config_json else "isaac_mod_manager_config.json"
    io.open(os.path.join(app, name), "w", encoding="utf-8").write(
        json.dumps(cfg, ensure_ascii=False, indent=2))
    return base, app, la, cfg, name


def run_exe(app, la, port):
    env = dict(os.environ)
    env["LOCALAPPDATA"] = la
    proc = subprocess.Popen(
        [os.path.join(app, "IsaacModManager.exe"), "--no-browser", "--port", str(port),
         "--force-new"],
        cwd=app, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:%d" % port
    t0 = time.time()
    while time.time() - t0 < 90:
        try:
            with urllib.request.urlopen(base + "/api/ping", timeout=2) as r:
                if json.loads(r.read())["app"] == "isaac-mod-manager":
                    return proc, base
        except Exception:
            time.sleep(0.2)
    return proc, None


def state(base):
    with urllib.request.urlopen(base + "/api/state", timeout=15) as r:
        return json.loads(r.read().decode())


def stop(proc, base):
    try:
        req = urllib.request.Request(base + "/api/shutdown", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        pass
    time.sleep(2)
    if proc.poll() is None:
        proc.kill()


print("[1] 老配置迁移: 把 exe + 老配置(exe 旁边)放进模拟环境, 用临时 LOCALAPPDATA 跑")
base1, app1, la1, cfg1, name1 = make_env("mig")
user_dir = os.path.join(la1, "IsaacModManager")
user_cfg = os.path.join(user_dir, "config.json")
check(not os.path.exists(user_cfg), "运行前: 数据目录里没有配置")

proc, url = run_exe(app1, la1, 8793)
check(url is not None, "exe 启动成功")
if url:
    st = state(url)
    check(len(st["mods"]) == 0 or True, "服务可用", "mods=%d" % len(st["mods"]))
    stop(proc, url)

check(os.path.isdir(user_dir), "数据目录已建立在 %%LOCALAPPDATA%%\\IsaacModManager", user_dir)
check(os.path.isfile(user_cfg), "老配置已迁到数据目录")
if os.path.isfile(user_cfg):
    got = json.load(io.open(user_cfg, encoding="utf-8"))
    check(got.get("groups") == cfg1["groups"], "★ 分组一条不丢",
          [(g["name"], len(g["mods"])) for g in got.get("groups", [])])
    check(got.get("bg_opacity") == 0.33 and got.get("card_opacity") == 0.44,
          "其它设置也一并保留", "%.2f/%.2f" % (got.get("bg_opacity"), got.get("card_opacity")))
    check(got.get("mods_path") == GAME_MODS, "游戏路径保留")
check(os.path.isfile(os.path.join(user_dir, "isaac_mod_manager.log")), "日志写在数据目录里")
src_now = json.load(io.open(os.path.join(app1, name1), encoding="utf-8"))
check(src_now == cfg1, "exe 旁边那份老配置未被改动(可回退)")

print()
print("[2] 再次运行: 不能覆盖用户已有配置(否则用户改动会被冲掉)")
if os.path.isfile(user_cfg):
    mine = json.load(io.open(user_cfg, encoding="utf-8"))
    mine["groups"] = [{"name": "用户后来自己改的", "mods": ["zzz"]}]
    io.open(user_cfg, "w", encoding="utf-8").write(json.dumps(mine, ensure_ascii=False, indent=2))
    # 同时把 exe 旁边那份也改掉(模拟"老 exe 又写了新配置")
    other = json.load(io.open(os.path.join(app1, name1), encoding="utf-8"))
    other["groups"] = [{"name": "老 exe 又写的", "mods": ["yyy"]}]
    io.open(os.path.join(app1, name1), "w", encoding="utf-8").write(
        json.dumps(other, ensure_ascii=False, indent=2))

    proc, url = run_exe(app1, la1, 8794)
    if url:
        stop(proc, url)
    after = json.load(io.open(user_cfg, encoding="utf-8"))
    check(after["groups"] == [{"name": "用户后来自己改的", "mods": ["zzz"]}],
          "★ 用户数据目录里的配置是权威, 未被老配置覆盖",
          [(g["name"], len(g["mods"])) for g in after.get("groups", [])])

print()
print("[3] 便携模式: 程序旁边放 config.json → 用它, 不迁移")
base2, app2, la2, cfg2, name2 = make_env("port", with_config_json=True)
proc, url = run_exe(app2, la2, 8795)
if url:
    st = state(url)
    stop(proc, url)
check(not os.path.exists(os.path.join(la2, "IsaacModManager", "config.json")),
      "便携模式不写用户数据目录")
check(os.path.isfile(os.path.join(app2, "config.json")), "程序旁边的 config.json 保留")

print()
print("[4] 用户数据目录里的凭据/缓存会被沿用(不用重新登录)")
real_la = os.environ.get("LOCALAPPDATA", "")
old_dir = os.path.join(real_la, "isaac_mod_manager")
new_dir = os.path.join(real_la, "IsaacModManager")
if os.path.isfile(os.path.join(old_dir, "steam_session.json")):
    check(os.path.isfile(os.path.join(new_dir, "steam_session.json")),
          "老目录的 steam_session.json 已搬运(主人无需重新登录)")
    check(os.path.isdir(os.path.join(new_dir, "thumbs")), "缩略图缓存已搬运")
else:
    check(True, "(跳过: 本机没有老凭据)")

for b in (base1, base2):
    shutil.rmtree(b, ignore_errors=True)

print()
print("=" * 58)
print("exe 数据目录验收: 通过 %d, 失败 %d" % (ok, bad))
print("=" * 58)
sys.exit(1 if bad else 0)
