# -*- coding: utf-8 -*-
"""制作 Release 资产。

规则(主人定的):
  一台电脑上跑起来所需的文件 —— 如果只有 exe, 那就直接给 exe;
  如果需要多个文件(便携版是文件夹), 就打成 portable.zip。

产物放在 release/ 下:
    IsaacModManager.exe               单文件版(自包含, 双击即用)
    IsaacModManager-portable.zip      便携版(整个文件夹压成一个包)

注意: 压缩包里**不会**带上用户的配置/日志(那属于用户数据, 不该对外分发)。
用法:  python tools/make_release.py [版本号]
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(ROOT, "release")

EXE = "IsaacModManager.exe"
PORTABLE_DIR = "IsaacModManager-portable"
ZIP_NAME = "IsaacModManager-portable.zip"

# 不该进分发包的文件(用户数据 / 日志 / 打包残留)
SKIP_NAMES = {"isaac_mod_manager_config.json", "isaac_mod_manager.log", "cache.db"}
SKIP_EXT = {".pyc", ".pyo"}
SKIP_DIRS = {"__pycache__", "backups", "thumbs"}


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.2f %s" % (n, unit)
        n /= 1024.0


def main():
    ver = sys.argv[1] if len(sys.argv) > 1 else "v1.0.0"
    os.makedirs(OUT, exist_ok=True)

    src_exe = os.path.join(DIST, EXE)
    if not os.path.isfile(src_exe):
        print("找不到 %s, 先执行 python build.py" % src_exe)
        return 1

    # ---- 单文件版: 直接给 exe(它自带全部资源) ----
    dst_exe = os.path.join(OUT, EXE)
    with open(src_exe, "rb") as f:
        data = f.read()
    with open(dst_exe, "wb") as f:
        f.write(data)
    print("已就绪: %-34s %s" % (EXE, human(len(data))))

    # ---- 便携版: 文件夹 → 一个 zip ----
    src_dir = os.path.join(DIST, PORTABLE_DIR)
    if os.path.isdir(src_dir):
        dst_zip = os.path.join(OUT, ZIP_NAME)
        n = 0
        with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for dp, dn, fn in os.walk(src_dir):
                dn[:] = [d for d in dn if d not in SKIP_DIRS]
                for name in sorted(fn):
                    if name in SKIP_NAMES or os.path.splitext(name)[1].lower() in SKIP_EXT:
                        continue
                    full = os.path.join(dp, name)
                    arc = os.path.join(PORTABLE_DIR, os.path.relpath(full, src_dir))
                    z.write(full, arc)
                    n += 1
        print("已就绪: %-34s %s (%d 个文件)" % (ZIP_NAME, human(os.path.getsize(dst_zip)), n))
    else:
        print("(跳过便携版: 找不到 %s)" % src_dir)

    print()
    print("Release %s 资产在: %s" % (ver, OUT))
    print("说明: 单文件版一个 exe 就能跑; 便携版是多文件, 所以按规则压成 zip。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
