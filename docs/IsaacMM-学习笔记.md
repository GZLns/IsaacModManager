# IsaacMM 学习笔记（同类项目对比）

> 对象：<https://github.com/PetricaT/IsaacMM>
> 调研时间：2026-09-20 ｜ 方式：`git clone` 源码 + 逐文件核对（不是只看 README）

## 一、它是什么

| 项 | 值 |
|---|---|
| 定位 | 《以撒的结合：重生》的 mod **排序**工具（Qt 桌面程序） |
| 语言/依赖 | Python + PySide6，`requires-python = ">=3.14"` |
| 平台 | Windows / macOS / Linux 都发（exe / dmg / AppImage / Flatpak） |
| 许可 | MIT |
| 活跃度 | 2 star、最后推送 2026-07-26、Release 到 **v0.5.6** |
| 工程化 | 有 readthedocs 文档、pytest 测试、4 套 CI 构建、自动更新器、主题仓库 |

## 二、核心思路：**只重命名，不搬文件**

它**唯一**会改的东西是每个 mod 的 `metadata.xml` 里的 `name` 字段（README 原话：
*"This tool ONLY modifies the metadata.xml file, and only 1 field inside, that being name"*）。

排序靠给文件夹名加 3 位数字前缀：

```
001 MOD_NAME_1
002 MOD_NAME_2
003 MOD_NAME_3
```

**为什么是 3 位数**（README 里的重要说明）：游戏按字符比较数字，
`100` 会被当成 `1`（跟 `2` 比它更小），所以必须零填充成 `001/002/003`。
游戏的加载顺序规则是：`符号 > 数字 > 大写字母 > 小写字母`。

## 三、它最值得学的三个功能

### 1. 自动排序（masterlist + 拓扑排序）

- `masterlist.yaml`：**社区维护**的加载顺序规则表，程序从 GitHub 远程拉取，
  24 小时 TTL 缓存，拉不到就用打包内置的那份兜底（`source/mods/sorter.py` 里的 `RemoteCache`）
- `user_rules.yaml`：用户自己按**工坊 ID** 写 `before/after` 约束
- 两者合并后做**拓扑排序**（`collections.deque` 的 Kahn 算法），产出完整加载顺序

> 这解决的是"一个大 mod 必须排在另一个前面"的痛点 —— 目前我们只能靠手动拖分组。

### 2. 冲突检测（`source/mods/conflict_index.py`）

扫描每个 mod 里会互相覆盖的文件类型 `{.png, .anm2, .wav, .lua}`，
**只看子目录里的文件**（根目录的 `main.lua` / `metadata.xml` 不算冲突），
找出"多个 mod 修改同一个相对路径"的情况，在界面上标出来。

性能上做了两级缓存：
- `_quick_token()`：目录 mtime + 顶层条目名与 mtime → 变了才重新扫
- `_fingerprint_folder()`：blake2b 指纹 + 文件集合存 SQLite

### 3. 工坊信息缓存 + 更新提醒

- SQLite 存工坊元数据（title / preview_url / description / created_at / updated_at）
- 配置里有 `workshop.timestamps` —— 用 `time_updated` 对比，**提示哪些 mod 有更新**
- 速率限制：滑动窗口 **180 次 / 300 秒**；`429` 时把配额直接吃满并进入冷却
- 永久失败标记：API 返回 `result=9`（文件不存在）→ 记入 `dead_workshop_ids`，
  以后不再重试（存进数据库）
- `tenacity` 做指数退避重试（3 次，1→10 秒）

## 四、最关键的一条：它**不下载** mod 本体

`source/mods/workshop.py` 的模块注释是 *"download icons, rate limiting, queue, details"* ——
它从 Steam 拿的只有 `GetPublishedFileDetails` 返回的**元数据和 `preview_url`（预览图）**，
mod 本体仍然要靠 Steam 客户端订阅下载。

设置项里对应的是 **`download_icons`（下载图标）**，跟"下载 mod"完全是两件事。

> ✅ **这反过来印证了我们的结论**：以撒的工坊内容是多文件 UGC，Steam 不提供直链，
> 任何第三方工具都无法"直接下载"。我们的做法（唤起 Steam + 守候 + 自动导入）方向是对的。

## 五、两种流派对比

| | **IsaacMM** | **本项目** |
|---|---|---|
| 管 mod 的方式 | 改名排序（不搬文件） | 收进仓库 + 目录链接（junction） |
| 能启用/停用吗 | 不能（只在游戏内 Mods 菜单开关） | **能**（建/删链接，游戏目录永远干净） |
| 跨平台 | ✅ Win/mac/Linux | ❌ 仅 Windows（junction 限制） |
| 工坊搜索 | ❌（只拿已装 mod 的图标和信息） | ✅ 应用内搜索（热门/最新/评分最高） |
| 一键下载 | ❌ | ✅ 唤起 Steam + 守候 + 自动导入启用 |
| 自动排序 | ✅ masterlist + 拓扑排序 | ❌（靠手动拖分组） |
| 冲突检测 | ✅ | ❌ |
| mod 更新提醒 | ✅ | ❌ |
| 中文界面 / 新手说明 | ❌ | ✅（版本区别、语言设置、中文注入方案） |
| 数据目录 | XDG 规范（`%LOCALAPPDATA%/IsaacMM`） | `%LOCALAPPDATA%\IsaacModManager` |

**结论：两边不是重复造轮子，定位不同。** 它是"排序器"，我们是"库管理 + 工坊客户端"。
它甚至因为"不能启用/停用"而在 TODO 里还留着 `Add launch game button`（我们早就有了）。

## 六、一个必须说清的"疑似 bug"（其实不是）

女仆一开始用本机 Python 3.11 编译它的源码，报了两处语法错误：

```
source/mods/workshop.py:234    except httpx.RequestError, OSError:
source/core/database.py:240    except json.JSONDecodeError, TypeError:
```

**但这不是 bug** —— 这是 **PEP 758**（Python 3.14 起允许 `except A, B:` 省略括号），
它的 `pyproject.toml` 里明确写了 `requires-python = ">=3.14"`，CI 也用 3.14 构建。

⚠️ **对我们的启示**：想跑它的源码必须 Python 3.14+；用旧版本解释器会误判成"代码有问题"。
（普通用户下 exe 不受影响。）

## 七、可以借鉴过来的（按性价比排序）

| 优先级 | 功能 | 说明 |
|---|---|---|
| ★★★ | **冲突检测** | 玩家刚需：多个 mod 改同一 `resources/` 文件会导致贴图/音效错乱，现在主人只能进游戏才发现 |
| ★★★ | **工坊元数据缓存 + 限流** | 我们现在每次搜索都实时打 Steam；加 SQLite 缓存 + 滑动窗口限流更稳、更快 |
| ★★ | **mod 更新提醒** | 缓存工坊 `time_updated`，提示"你有 N 个 mod 有新版" |
| ★★ | **自动排序规则** | 可以做一个简化版：按工坊 ID 记 before/after，不用社区 masterlist |
| ★ | 检查更新（自身） | 我们已发 v1.0.0，可加"发现新版本"提示 |
| ★ | 备份开关 | 批量改名前自动备份（我们现在是"仓库"模式，风险本来就低） |

## 八、工程化上值得学的小细节

- `RemoteCache` 模式：**远程文件 + 本地缓存 + 打包内置兜底 + TTL**，网络失败不影响启动
- `.github/AGENTS.md` / `CODEMAP.md` / `PROGRESS.md`：给 AI 协作用的项目文档（我们现在靠 memory + skill）
- 多平台 CI 一次产出 4 种安装包（workflow_call 复用）
- 分隔符（separator）：在列表里插"分组标题"帮助视觉分组 —— 跟我们的"分组"类似但更轻
- `ignored_items` 配置：扫描时忽略 `.git` / `__pycache__` / `System Volume Information` 等
