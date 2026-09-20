# -*- coding: utf-8 -*-
"""v2.0 新功能的专项测试: 缓存 / 冲突检测 / 自动排序 / 更新 / 备份。

全部在沙盒里跑(临时配置 + 临时 mod 目录), 不碰主人的真实数据。
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("imm", os.path.join(ROOT, "isaac_mod_manager.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

OK, BAD = 0, 0
def check(c, t, extra=""):
    global OK, BAD
    if c: OK += 1
    else: BAD += 1
    print("  %s %s%s" % ("✓" if c else "✗", t, ("  → " + str(extra)) if extra else ""))

tmp = tempfile.mkdtemp(prefix="imm_v2_")
game = os.path.join(tmp, "game")
mods_dir = os.path.join(game, "mods")
lib_dir = os.path.join(game, "isaac_mod_library")
os.makedirs(mods_dir); os.makedirs(lib_dir)
cfg_path = os.path.join(tmp, "config.json")
io.open(cfg_path, "w", encoding="utf-8").write(json.dumps(
    {"mods_path": mods_dir, "library_path": lib_dir, "groups": []}, ensure_ascii=False))

META = """<metadata><name>%s</name><directory>%s</directory><id>%s</id><version>%s</version></metadata>"""

def make_mod(directory, name, wid, files):
    """files: {相对路径: 内容}"""
    base = os.path.join(lib_dir, directory)
    os.makedirs(base, exist_ok=True)
    io.open(os.path.join(base, "metadata.xml"), "w", encoding="utf-8").write(
        META % (name, directory, wid, "1.0"))
    for rel, text in files.items():
        p = os.path.join(base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        io.open(p, "w", encoding="utf-8").write(text)
    return base

print("[1] 本地缓存层 (modcache)")
cache = m.cache()
check(cache is not None, "缓存可用", type(cache).__name__)
cache.put_workshop({"999": {"title": "T", "preview": "P", "updated": 111, "size": 5}})
got = cache.get_workshop(["999"], max_age=999)
check(got.get("999", {}).get("title") == "T", "工坊元数据可写可读")
cache.mark_dead("888")
check("888" in cache.dead_ids(), "失效标记")
cache.kv_set("k1", {"a": 1})
check(cache.kv_get("k1") == {"a": 1}, "键值存取")
st = cache.rate_state(limit=5, window=60)
for _ in range(5):
    cache.rate_allow(limit=5, window=60)
check(cache.rate_allow(limit=5, window=60) is False, "滑动窗口限流会拦住超额请求",
      cache.rate_state(limit=5, window=60))

print()
print("[2] ① 冲突检测")
make_mod("Alpha_1000000001", "Alpha", "1000000001",
         {"main.lua": "-- a", "content/gfx/shared.anm2": "<a>",
          "content/sfx/only_a.wav": "a", "readme.txt": "x"})
make_mod("Beta_1000000002", "Beta", "1000000002",
         {"main.lua": "-- b", "content/gfx/shared.anm2": "<b>",
          "content/gfx/only_b.anm2": "b"})
make_mod("Gamma_1000000003", "Gamma", "1000000003",
         {"main.lua": "-- c", "content/gfx/other.anm2": "c"})
L = m.ModLibrary(config_path=cfg_path)
check(len(L.mods) == 3, "沙盒里 3 个 mod", sorted(L.mods))
L.set_dirs(["Alpha_1000000001", "Beta_1000000002", "Gamma_1000000003"], True)
L.scan()
check(sum(1 for x in L.mods.values() if x["state"] == "enabled") == 3, "3 个都已启用")
detail = L.conflict_scan()
paths = [d["path"] for d in detail]
check(len(detail) == 1, "只检出 1 处冲突", paths)
check(any("shared.anm2" in p for p in paths), "冲突文件是 shared.anm2")
check(not any("main.lua" in p for p in paths), "根目录 main.lua 不算冲突(每个 mod 都有)")
check(not any("readme" in p for p in paths), "非资源扩展名不参与")
check(len(L.conflict_map) == 2, "涉及 2 个 mod", sorted(L.conflict_map))
st = L.state()
check(st["conflicts"]["count"] == 1, "state 里带上冲突数")
check(any(x.get("conflicts") for x in st["mods"]), "卡片能拿到冲突数")

# 第二次扫描应走指纹缓存(结果一致)
d2 = L.conflict_scan()
check(len(d2) == 1, "带指纹缓存重扫结果一致")

print()
print("[3] ④ 自动排序(规则 + 拓扑)")
L.set_sort_rules([{"id": "1000000003", "after": ["1000000001"]}])
check(len(L.sort_rules()) == 1, "规则已保存")
res = L.auto_sort()
order = res["order"]
check(len(order) == 3, "顺序覆盖全部已启用 mod", order)
check(order.index("Gamma_1000000003") > order.index("Alpha_1000000001"),
      "★ Gamma 排在 Alpha 之后(规则生效)", order)
check(res["cycles"] == [], "无循环依赖")
# 环检测
L.set_sort_rules([{"id": "1000000001", "after": ["1000000002"]},
                  {"id": "1000000002", "after": ["1000000001"]}])
r2 = L.auto_sort()
check(len(r2["cycles"]) == 2, "互相依赖会被识别成环", r2["cycles"])
L.set_sort_rules([{"id": "1000000003", "after": ["1000000001"]}])

print()
print("[4] ④ 排序落到链接名(真建 junction, 仓库实体不动)")
res = L.apply_load_order()
check(res["changed"] == 3, "3 个链接被重命名", res["changed"])
names = sorted(os.listdir(mods_dir))
check(all(n[:3].isdigit() and n[3] == "_" for n in names), "链接名都带 NNN_ 前缀", names)
check(sorted(os.listdir(lib_dir)) == ["Alpha_1000000001", "Beta_1000000002", "Gamma_1000000003"],
      "★ 仓库里的实体目录名没被动")
L.scan()
check(len(L.mods) == 3, "★ 改名后扫描仍然认识这 3 个 mod(没丢)",
      [x.get("link") for x in L.mods.values()])
check(all(x["state"] == "enabled" for x in L.mods.values()), "状态仍然是启用")
top = sorted(os.listdir(mods_dir))[0]
try:
    check(os.path.isdir(os.path.join(mods_dir, top)), "重命名后的链接仍然可用(能穿透访问)")
except OSError as e:
    check(False, "链接可用", str(e)[:60])
check(m.cache().load_order() and len(m.cache().load_order()) == 3, "顺序记进了缓存")

res = L.clear_load_order()
check(res["changed"] == 3, "清除前缀", res["changed"])
L.scan()
check(sorted(os.listdir(mods_dir)) == ["Alpha_1000000001", "Beta_1000000002", "Gamma_1000000003"],
      "名字恢复原样")

print()
print("[5] ③ mod 更新提醒")
L.check_updates()                      # 第一次: 记录"已知状态", 不该报更新
first = L.check_updates()
check(first["checked"] == 3, "检查了 3 个 mod", first)
check(len(first["updates"]) == 0, "首次检查不误报更新")
# 沙盒里的工坊 ID 是假的(Steam 上查不到), 所以直接往缓存里喂一条"更新过的"元数据,
# 再让它命中缓存 —— 这样验证的是"比较逻辑"本身
m.cache().put_workshop({"1000000001": {"title": "Alpha", "updated": 4102444800, "size": 1}})
m.cache().kv_set("seen_upd:1000000001", 1)
upd = L.check_updates(max_age=99999)
check(any(u["id"] == "1000000001" for u in upd["updates"]),
      "★ 记录的时间变新就会报出来", [u["id"] for u in upd["updates"]])
check(L.update_state()["count"] >= 1, "更新数进了 state")
upd2 = L.check_updates(mark_seen=True, max_age=99999)
check(upd2["updates"] == [] and upd2.get("marked", 0) >= 1,
      "★ 标记为已知后本次就清空", "marked=%s" % upd2.get("marked"))
upd3 = L.check_updates(max_age=99999)
check(all(u["id"] != "1000000001" for u in upd3["updates"]),
      "★ 之后也不再提示")

print()
print("[6] ⑥ 备份")
b1 = L.backup_now("test1")
check(os.path.isfile(b1["path"]), "备份文件生成了", b1["name"])
import zipfile
with zipfile.ZipFile(b1["path"]) as z:
    names = z.namelist()
    check(set(names) == {"config.json", "mods.json", "README.txt"}, "备份内容", names)
    mj = json.loads(z.read("mods.json").decode("utf-8"))
    check(len(mj["mods"]) == 3, "清单里有 3 个 mod")
    check("app_version" in mj, "记录了版本")
L.cfg["backup_keep"] = 2
for i in range(3):
    L.backup_now("r%d" % i)
lst = L.list_backups()
check(len(lst["items"]) <= 2, "只保留最近 N 份", len(lst["items"]))

print()
print("[7] ⑤ 程序自身更新(版本比较)")
check(m._ver_tuple("2.0.0") > m._ver_tuple("1.9.9"), "2.0.0 > 1.9.9")
check(m._ver_tuple("v1.10.0") > m._ver_tuple("1.9.0"), "1.10.0 > 1.9.0 (不是字符串比较)")
check(m.APP_VERSION == "2.0.0", "版本号已更新", m.APP_VERSION)
check("GZLns/IsaacModManager" == m.APP_REPO, "仓库地址")

print()
print("[8] 前端接线")
js = io.open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
html = io.open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
css = io.open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8").read()
for k, t in (("btnConflicts", "冲突按钮"), ("btnSort", "排序按钮"), ("btnUpdates", "更新按钮"),
             ("cfBadge", "冲突角标"), ("upBadge", "更新角标")):
    check(k in html, "顶栏有" + t)
for k, t in (("openConflicts", "冲突面板"), ("openSort", "排序面板"),
             ("checkWsUpdates", "更新检查"), ("checkAppUpdate", "自身更新"), ("renderBadges", "角标渲染")):
    check("function %s" % k in js, "有" + t)
check("sBackup" in js and "sKeep" in js, "设置里有备份开关")
check("#conflicts" in js and "#sort" in js, "有直达入口")
check(".nbadge" in css and ".cf-list" in css and ".sr-add" in css, "样式齐备")

print()
print("[9] 端点在(源码里能看到路由)")
src = io.open(os.path.join(ROOT, "isaac_mod_manager.py"), encoding="utf-8").read()
for ep in ("/api/conflicts", "/api/updates/check", "/api/sort/preview", "/api/sort/apply",
           "/api/sort/clear", "/api/sort/rules", "/api/app/update",
           "/api/backup/create", "/api/backup/list"):
    check(ep in src, "路由 " + ep)

print()
print("=" * 58)
print("v2.0 新功能测试: 通过 %d, 失败 %d" % (OK, BAD))
print("=" * 58)
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if BAD else 0)
