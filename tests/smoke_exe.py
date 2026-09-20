# -*- coding: utf-8 -*-
"""
exe 冒烟测试 —— 验证打包产物真的能跑

用法:
    python tests/smoke_exe.py dist/IsaacModManager.exe
    python tests/smoke_exe.py dist/IsaacModManager-portable/IsaacModManager-portable.exe

做四件事:
  1. 用沙盒配置启动 exe (不碰真实游戏目录、不弹浏览器), 测量「启动到可用」耗时
  2. 验证 /api/ping /api/state / 首页 / 静态资源 都能正常返回
  3. 验证单实例保护: 再启一次 exe, 它应该立刻退出而不是起第二份服务
  4. 用 /api/shutdown 优雅退出, 并确认进程真的结束
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

PORT = 8791


def wait_ready(port, timeout=60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=0.5) as r:
                if json.loads(r.read().decode("utf-8")).get("app") == "isaac-mod-manager":
                    return time.time() - t0
        except Exception:
            time.sleep(0.08)
    return None


def get(port, path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=8) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    if len(sys.argv) < 2:
        print("用法: python tests/smoke_exe.py <exe路径>")
        return 2
    exe = os.path.abspath(sys.argv[1])
    if not os.path.isfile(exe):
        print("找不到 exe: %s" % exe)
        return 2
    print("被测 exe: %s  (%.2f MB)" % (exe, os.path.getsize(exe) / 1048576))

    tmp = tempfile.mkdtemp(prefix="imm_smoke_")
    game = os.path.join(tmp, "game")
    mods = os.path.join(game, "mods")
    lib = os.path.join(game, "isaac_mod_library")
    os.makedirs(mods)
    os.makedirs(lib)
    for n in ("SmokeA_1", "SmokeB_2"):
        os.makedirs(os.path.join(mods, n))
        io.open(os.path.join(mods, n, "metadata.xml"), "w", encoding="utf-8").write(
            "<metadata><name>%s</name><version>1.0</version></metadata>" % n)
    cfg = os.path.join(tmp, "smoke_cfg.json")
    io.open(cfg, "w", encoding="utf-8").write(json.dumps(
        {"mods_path": mods, "library_path": lib, "groups": []}))

    ok, bad = [], []

    def check(c, title, extra=""):
        (ok if c else bad).append(title)
        print("  %s %s%s" % ("✓" if c else "✗", title, ("  → " + str(extra)) if extra else ""))

    # ---- 1) 启动并计时 ----
    proc = subprocess.Popen([exe, "--no-browser", "--port", str(PORT), "--config", cfg],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    took = wait_ready(PORT)
    print("\n[1] 启动")
    check(took is not None, "exe 启动并服务就绪", ("%.2f 秒" % took) if took else "超时")
    if took is None:
        proc.kill()
        return 1

    # ---- 2) 接口与静态资源 ----
    print("\n[2] 接口与资源")
    st, body = get(PORT, "/api/state")
    s = json.loads(body.decode("utf-8"))
    check(st == 200 and s["ok"] and len(s["mods"]) == 2, "GET /api/state 返回 2 个 mod")
    check(s["cfg"]["mods_path"] == mods, "配置按 --config 生效 (没碰真实游戏目录)", s["cfg"]["mods_path"][-24:])
    st, body = get(PORT, "/")
    check(st == 200 and b"<html" in body, "首页 index.html 正常 (打包内资源可读)")
    st, body = get(PORT, "/web/app.js")
    check(st == 200 and b"api(" in body, "/web/app.js 正常")
    st, body = get(PORT, "/assets/bg_source.jpg")
    check(st == 200 and body[:2] == b"\xff\xd8", "/assets/bg_source.jpg 正常 (背景图已打包)")
    st, body = get(PORT, "/assets/bg_baked.png")
    check(st == 200 and body[:4] == b"\x89PNG", "/assets/bg_baked.png 正常")

    # ---- 3) 真动作: 启用一个 mod (junction 机制在 exe 里也要能用) ----
    print("\n[3] 核心动作 (junction 机制)")
    req = urllib.request.Request("http://127.0.0.1:%d/api/mods/toggle" % PORT,
                                 data=json.dumps({"dir": "SmokeA_1", "enable": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        s2 = json.loads(r.read().decode("utf-8"))
    rec = [x for x in s2["mods"] if x["dir"] == "SmokeA_1"][0]
    check(rec["state"] == "enabled", "启用 mod 成功 (exe 内 junction 可用)")
    check(os.path.isdir(os.path.join(lib, "SmokeA_1")), "实体已进入沙盒仓库")

    # ---- 4) 单实例保护 ----
    print("\n[4] 单实例保护")
    st, body = get(PORT, "/api/ping")
    pid_before = json.loads(body.decode("utf-8"))["pid"]

    t0 = time.time()
    r2 = subprocess.run([exe, "--no-browser", "--port", str(PORT), "--config", cfg],
                        capture_output=True, timeout=90)
    dt = time.time() - t0
    check(r2.returncode == 0, "第二次启动正常退出 (返回码 0)", "%.2f 秒" % dt)
    check(dt < 8, "第二次启动很快 (没起第二份服务)", "%.2f 秒" % dt)

    # 注意: onefile 的引导器会 fork 出子进程, 所以不能用 subprocess 的 pid 比对,
    # 要在 HTTP 层面确认「服务实例没被替换」
    st, body = get(PORT, "/api/ping")
    pid_after = json.loads(body.decode("utf-8"))["pid"]
    check(pid_before == pid_after,
          "服务实例没有被替换 (ping 返回的 pid 未变)", "%s → %s" % (pid_before, pid_after))

    # ---- 5) 优雅退出 ----
    print("\n[5] 退出")
    req = urllib.request.Request("http://127.0.0.1:%d/api/shutdown" % PORT, data=b"{}",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            check(json.loads(r.read().decode("utf-8")).get("ok") is True, "/api/shutdown 返回 ok")
    except Exception as e:
        check(False, "/api/shutdown 调用", e)
    for _ in range(40):
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    check(proc.poll() is not None, "exe 进程已退出 (不会留下僵尸)")
    if proc.poll() is None:
        proc.kill()

    # 日志文件应该生成在 exe 旁边
    logp = os.path.join(os.path.dirname(exe), "isaac_mod_manager.log")
    if os.path.isfile(logp):
        size = os.path.getsize(logp)
        check(size > 0, "exe 旁边生成了日志文件", "%d 字节" % size)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 58)
    print("冒烟通过 %d 项" % len(ok) + ("  失败 %d 项 ✗" % len(bad) if bad else "  全部通过 ✓"))
    for f in bad:
        print("  ✗", f)
    print("=" * 58)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
