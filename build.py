# -*- coding: utf-8 -*-
"""
以撒 Mod 管理器 —— 打包脚本

用法:
    python build.py            # 两种形态都打 (单文件版 + 秒开版)
    python build.py onefile    # 只打单文件版
    python build.py onedir     # 只打秒开版(文件夹)

产物:
    dist/IsaacModManager.exe                 单文件, 拷走就能用
    dist/IsaacModManager-portable/           文件夹, 启动最快(推荐放桌面快捷方式)

要点(踩坑记录):
  * web/ 与 assets/ 用 --add-data 塞进 exe, 运行时从 sys._MEIPASS 读;
    但只要 exe 旁边放了同名 web/ 或 assets/ 目录, 就优先用旁边那份 → 可热改前端不重新打包
  * 必须 --exclude-module tkinter: 新架构已不用 GUI, 打进去白涨体积
  * 配置与日志写在 exe 旁边, 不在解压目录里(否则退出即丢)
"""
import io
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ENTRY = "isaac_mod_manager.py"
APP_NAME = "IsaacModManager"
SEP = ";" if os.name == "nt" else ":"          # PyInstaller 的 --add-data 分隔符

# 白涨体积、且本程序完全用不到的东西
# ⚠️ 千万不要排除 email / html / http.cookies：
#    http.server 依赖 email 解析 HTTP 头，排掉会在启动时炸 ModuleNotFoundError
EXCLUDES = [
    "tkinter", "numpy", "matplotlib", "scipy", "pandas",
    "IPython", "jupyter", "notebook", "setuptools", "pip", "wheel",
    "lib2to3", "pydoc_data", "xmlrpc", "doctest",
]


def build(mode):
    """mode: onefile | onedir"""
    dist = os.path.join(ROOT, "dist")
    work = os.path.join(ROOT, "build")
    name = APP_NAME if mode == "onefile" else APP_NAME + "-portable"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--" + mode,
        "--noconsole",
        "--name", name,
        "--distpath", dist,
        "--workpath", work,
        "--specpath", work,
        # 注意: --specpath 会让 --add-data 的相对路径按 spec 所在目录解析,
        # 所以这里必须用绝对路径, 否则会报 "Unable to find build/web"
        "--add-data", "%s%sweb" % (os.path.join(ROOT, "web"), SEP),
        "--add-data", "%s%sassets" % (os.path.join(ROOT, "assets"), SEP),
        # 工坊直连下载要用: 下载脚本 + 一键登录 bat + 系统根证书包
        "--add-data", "%s%stools" % (os.path.join(ROOT, "tools"), SEP),
        "--add-data", "%s%scerts" % (os.path.join(ROOT, "certs"), SEP),
    ]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    icon = os.path.join(ROOT, "assets", "app.ico")
    if os.path.isfile(icon):
        cmd += ["--icon", icon]
    cmd.append(ENTRY)
    print("\n>>> 打包 [%s]\n    %s\n" % (mode, " ".join(cmd)))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit("打包失败: %s" % mode)

    if mode == "onedir":
        # 把资源也复制到便携文件夹里, 满足"外置优先"分支 (便于热改 + 启动更快)
        out = os.path.join(dist, name)
        for sub in ("web", "assets", "tools", "certs"):
            src = os.path.join(ROOT, sub)
            dst = os.path.join(out, sub)
            if not os.path.isdir(src):
                continue
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        print("    已把 web/ assets/ tools/ certs/ 放到便携目录 (外置优先, 可热改)")
    return os.path.join(dist, name)


def report():
    print("\n" + "=" * 62)
    print("产物清单")
    print("=" * 62)
    dist = os.path.join(ROOT, "dist")
    for name in sorted(os.listdir(dist)):
        p = os.path.join(dist, name)
        if os.path.isfile(p):
            print("  %-34s %7.2f MB" % (name, os.path.getsize(p) / 1048576))
        else:
            total = sum(os.path.getsize(os.path.join(dp, f))
                        for dp, _, fs in os.walk(p) for f in fs)
            print("  %-34s %7.2f MB  (文件夹)" % (name + "/", total / 1048576))


def keep_dirs():
    """打包用的资源目录(被整体重建), 与「必须保住」的用户数据"""
    return [
        os.path.join(ROOT, "dist", "isaac_mod_manager_config.json"),
        os.path.join(ROOT, "dist", APP_NAME + "-portable",
                     "isaac_mod_manager_config.json"),
    ]


def stash_user_configs():
    r"""重建前把 dist 里的老配置文件暂存起来。

    历史原因: 老版本把配置写在 exe 旁边, 而打包会把整个 dist/ 删掉重建 ——
    不做这一步, 分组/不透明度设置会被无声清空(踩过一次)。
    现在配置已经搬到 %LOCALAPPDATA%\IsaacModManager\config.json(与程序目录无关),
    这里保留只是为了兼容还留着老配置文件的目录, 顺带把老文件搬到数据目录。
    """
    stash = {}
    for p in keep_dirs():
        if os.path.isfile(p):
            try:
                stash[p] = io.open(p, encoding="utf-8").read()
            except OSError:
                pass
    if stash:
        print("    已暂存 %d 份用户配置(重建后原样放回)" % len(stash))
    return stash


def restore_user_configs(stash):
    for p, text in (stash or {}).items():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            io.open(p, "w", encoding="utf-8").write(text)
            print("    已还原用户配置:", os.path.basename(os.path.dirname(p))
                  + "/" + os.path.basename(p))
        except OSError as exc:
            print("    !! 还原失败 %s: %s" % (p, exc))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which.startswith("--"):
        which = "both"
    todo = ["onefile", "onedir"] if which == "both" else [which]
    _stash = stash_user_configs()
    for mode in todo:
        build(mode)
    restore_user_configs(_stash)
    report()
    print("")
    print("提示: 用户数据(配置/分组/Steam 凭据)现在在")
    print(r"      %LOCALAPPDATA%\IsaacModManager\  (与程序目录无关, 替换 exe 不会丢分组)")

    # 打完自动冒烟一次 (用沙盒配置跑, 不碰真实游戏目录), 避免交付跑不起来的 exe
    if "--no-smoke" not in sys.argv:
        smoke = os.path.join(ROOT, "tests", "smoke_exe.py")
        target = os.path.join(ROOT, "dist", APP_NAME + ".exe")
        if os.path.isfile(smoke) and os.path.isfile(target):
            print("\n>>> 冒烟测试 %s" % target)
            subprocess.run([sys.executable, smoke, target], cwd=ROOT)
        else:
            print("\n(跳过冒烟测试: 找不到 tests/smoke_exe.py 或产物)")
