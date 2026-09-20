# -*- coding: utf-8 -*-
"""数据目录改造的实测: 搬运 / 探测 / 迁移 / 空路径 / 登录态"""
import importlib.util, io, json, os, shutil, tempfile, time

ROOT = os.getcwd()
spec = importlib.util.spec_from_file_location("imm", os.path.join(ROOT, "isaac_mod_manager.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ok = bad = 0
def check(c, t, extra=""):
    global ok, bad
    if c: ok += 1
    else: bad += 1
    print("  %s %s%s" % ("✓" if c else "✗", t, ("  → " + str(extra)) if extra else ""))

LA = os.environ["LOCALAPPDATA"]

print("[1] 数据目录")
check(os.path.basename(m.USER_DIR) == "IsaacModManager", "数据目录 = %%LOCALAPPDATA%%\\IsaacModManager", m.USER_DIR)
check(os.path.dirname(m.USER_DIR) == LA, "在 LOCALAPPDATA 下")
check(os.path.basename(m.CONFIG_PATH) == "config.json", "配置文件名 config.json", m.CONFIG_PATH)
check(os.path.normcase(os.path.basename(m.LOG_PATH)).startswith("isaac_mod_manager.log"), "日志也在数据目录", m.LOG_PATH)
check(os.path.normcase(os.path.dirname(m.CONFIG_PATH)) != os.path.normcase(m.APP_DIR),
      "配置不再写在程序旁边(这是丢分组的根因)")

print()
print("[2] 老数据目录搬运(凭据+缩略图)")
old_dir = os.path.join(LA, "isaac_mod_manager")
old_sess = os.path.join(old_dir, "steam_session.json")
new_sess = os.path.join(m.USER_DIR, "steam_session.json")
check(isinstance(m.MIGRATED_USER_FILES, list), "搬运清单:", m.MIGRATED_USER_FILES)
if os.path.isfile(old_sess):
    check(os.path.isfile(new_sess), "steam_session.json 已搬到新目录(不用重新登录)")
    if os.path.isfile(new_sess):
        a = io.open(old_sess, "rb").read(); b = io.open(new_sess, "rb").read()
        check(a == b, "内容一致")
n_old = len(os.listdir(os.path.join(old_dir, "thumbs"))) if os.path.isdir(os.path.join(old_dir, "thumbs")) else 0
n_new = len(os.listdir(os.path.join(m.USER_DIR, "thumbs"))) if os.path.isdir(os.path.join(m.USER_DIR, "thumbs")) else 0
check(n_new >= n_old and n_new > 0, "缩略图缓存已搬", "%d -> %d 张" % (n_old, n_new))
check(os.path.isdir(old_dir), "老目录保留不动(可回退)")

print()
print("[3] 游戏路径自动探测(首次运行免配置)")
g = m.guess_mods_path()
check(bool(g) and os.path.isdir(g), "探测到 mods 目录", g)
check("The Binding of Isaac Rebirth" in g, "路径正确")

print()
print("[4] 迁移逻辑(显式 target, 不碰真实数据)")
tmpd = tempfile.mkdtemp(prefix="imm_mig_")
tgt = os.path.join(tmpd, "sub", "config.json")
cands = m._legacy_config_candidates()
check(isinstance(cands, list), "候选来源数量", len(cands))
if len(cands) >= 2:
    mt = [os.path.getmtime(p) for p in cands]
    check(mt == sorted(mt, reverse=True), "候选按修改时间倒序(取最新, 不覆盖新的)")
src = m.migrate_legacy_config(tgt)
check(src is not None and os.path.isfile(tgt), "老配置已迁到目标位置", os.path.basename(src or ""))
if os.path.isfile(tgt):
    d = json.load(io.open(tgt, encoding="utf-8"))
    check(isinstance(d.get("groups"), list), "迁移后分组还在", [(x["name"], len(x["mods"])) for x in (d.get("groups") or [])])
    if src:
        check(json.load(io.open(src, encoding="utf-8")) == d, "内容与源一致")
check(m.migrate_legacy_config(tgt) is None, "目标已存在时不再重复迁移(不会覆盖用户改动)")

print()
print("[5] 便携模式 vs 用户目录")
check(m.resolve_config_path("D:/x/y.json", False) == "D:/x/y.json", "--config 显式指定优先")
check(m.resolve_config_path(None, True) == os.path.join(m.APP_DIR, "config.json"),
      "--portable 时用程序旁边的 config.json")

print()
print("[6] mods_path 为空时不崩(新默认值)")
cfg_p = os.path.join(tmpd, "empty.json")
io.open(cfg_p, "w", encoding="utf-8").write(json.dumps({"mods_path": "", "library_path": "", "groups": []}, ensure_ascii=False))
saved_guess = m.guess_mods_path
m.guess_mods_path = lambda: ""            # 模拟探测失败
try:
    L = m.ModLibrary(config_path=cfg_p)
    L.scan()
    st = L.state() if hasattr(L, "state") else {}
    check(True, "空 mods_path 能正常构造与扫描", "mods=%d" % len(L.mods))
    check(isinstance(st.get("cfg", {}).get("data_dir"), str), "state 里带上了 data_dir",
          st.get("cfg", {}).get("data_dir"))
finally:
    m.guess_mods_path = saved_guess
check(m.DEFAULT_CONFIG.get("mods_path") == "", "默认配置不再写死作者本机路径(可放公开仓库)")
check("E:\\STEAM" not in io.open(os.path.join(ROOT, "isaac_mod_manager.py"), encoding="utf-8").read(),
      "源码里已无本机绝对路径")

print()
print("[7] Steam 登录态仍可读(凭据路径统一后)")
L2 = m.ModLibrary(config_path=os.path.join(ROOT, "isaac_mod_manager_config.json"))
who = L2.steam_logged_in()
check(who is not None, "登录态可读", who)
sf = L2.steam_session_file()
check(os.path.normcase(os.path.dirname(sf)) == os.path.normcase(m.USER_DIR),
      "凭据文件在数据目录内", sf.replace(LA, "%LOCALAPPDATA%"))

print()
print("=" * 56)
print("数据目录改造实测: 通过 %d, 失败 %d" % (ok, bad))
print("=" * 56)
shutil.rmtree(tmpd, ignore_errors=True)
