# GUI List Miner

从**没有 API、不能导出、UIA 树是空的** Windows 桌面应用里，批量挖出列表数据。

滚屏截图 → 内置 OCR → 几何布局解析 → 多级去重 → CSV。
纯 Python + Pillow + Windows 自带 OCR 引擎，**零第三方依赖、不联网、不装任何东西**。

> 起因：想整理自己近一年微信点赞过的公众号文章。
> 微信没有开放 API、没有导出入口、UIA 树里只有 2 个控件。
> 于是有了这套东西——最终从三个视图里挖出 **2,435 条原始记录**，去重后策展出 430 条 AI 实践内容。

---

## 能干什么

- 微信：公众号点赞、最近阅读、视频号赞、收藏
- 任何 PC 客户端的历史列表：播放记录、订单、浏览历史、聊天列表
- 前提是它是**虚拟滚动列表**（不滚动就只渲染可见区）

不适合：有官方 API 的（直接调）、网页（用浏览器自动化）。

## 快速开始

```bash
# 0. 环境
export PATH="/usr/bin:/bin:/c/Windows/System32:$PATH"
PY="C:/path/to/python.exe"        # 需要 Pillow

# 1. 找窗口 hwnd（窗口若被最小化，先还原再取）
"$PY" -c "
import sys; sys.path.insert(0,'scripts')
import win32util as wu
for w in wu.list_windows(min_width=500): print(w.hwnd, w.title, w.rect)
"

# 2. 滚动截屏（自动滚到底停止）—— 建议后台跑
"$PY" scripts/capture.py --hwnd 1051982 --rel-x 0.93 --rel-y 0.5 \
                         --scroll -2 --max 800 --delay 0.7 --out data/raw/list

# 3. 裁到列表区 + OCR
"$PY" scripts/crop.py --src data/raw/list --dst data/cropped --box 1060,140,1938,1048
# PowerShell 里跑（Bash 调 PowerShell 会被安全策略拦）
powershell -ExecutionPolicy Bypass -File scripts\ocr_winrt.ps1 -Dir data\cropped -Lang zh-CN

# 4. 解析成条目
"$PY" scripts/extract.py --mode linear --src data/cropped --out out/items.csv --x-min 10
"$PY" scripts/extract.py --mode grid   --src data/cropped --out out/items.csv \
                         --cols 150,405,405,620,620,890 --col-x0 187,410,632 --period 352

# 5. 去重
"$PY" scripts/dedup.py --src out/items.csv --out out/clean.csv --preset linear
```

作为 [WorkBuddy](https://www.workbuddy.cn) skill 使用时，Agent 会自动按 `SKILL.md` 里的流程跑。
安装：把本仓库克隆到 `~/.workbuddy/skills/gui-list-miner/` 即可。

## 两个解析模式

| 模式 | 适用 | 原理 |
|---|---|---|
| `linear` | 单列列表（点赞、阅读历史） | 按**字高**分类（标题 ≥15px，来源 <15px）+ **垂直间隙**切条目 |
| `grid` | 多列卡片网格（视频号、相册） | **网格相位过滤**：真标题的 `y mod 行周期` 恒等于同一相位，而封面字幕、时长角标、点赞数的相位是随机的 → 直接滤掉 |

`grid` 的相位过滤是本项目的杀手锏。微信视频号卡片封面上有大字标题、时长「00:48」、点赞「1.6万」，
常规 OCR 后处理很难区分，但它们不落在网格相位上。

## 去重：为什么需要 5 级

OCR 噪声决定了同一条记录会带着不同错字出现十几次：

| 级 | 方法 | 解决 |
|---|---|---|
| 1 | 指纹前缀归并 | 同一条被截断成不同长度 |
| 2 | 片段丢弃 | 残行混入 |
| 3 | 字符集 Jaccard ≥0.9 | 单字误识 |
| 4 | SequenceMatcher（标题 ≥0.78 且 作者 ≥0.6） | 双字误识，如「究竟」→「宄見」 |
| 5 | 跨表交叉去重 | 多来源合并防重复收录 |

**没有一组阈值通吃**，所以提供了三档预设（用真实数据标定）：

| preset | 适用 | 微信实测效果 |
|---|---|---|
| `grid` | OCR 噪声重的网格卡片 | 629 → 628 |
| `linear` | 文本较干净但同号系列标题多 | 620 → 601 |
| `off` | 只跑前两级，作调参基线 | 620 → 622 |

调参验证法：先跑 `off` 拿基线，再切 preset，比对两份结果的指纹集合，
看 `extra`（合并不足）与 `missing`（过度合并）分别多少。

## 微信实测参数

见 [`references/wechat-layout.md`](references/wechat-layout.md)，含三个视图的入口路径、
裁剪框、布局参数，以及已确认的数据边界（例如「最近阅读只保留最近 1 个月」）。

## 项目结构

```
SKILL.md                 Agent 用的完整工作流（含所有坑位）
README.md                本文件
scripts/
  win32util.py           窗口枚举/还原/滚轮/截屏/dhash  (纯 ctypes + Pillow)
  capture.py             滚动截屏，dhash 到底自动停
  crop.py                批量裁剪
  ocr_winrt.ps1          Windows 内置 WinRT OCR，输出词级坐标
  extract.py             linear / grid 双模式解析
  dedup.py               5 级去重 + 3 档预设 + 跨表交叉去重
  gh_check.py            GitHub 仓库校验（走 api.github.com）
references/
  wechat-layout.md       微信三视图实测参数
```

## 常见问题

**坐标全错？** 没设 DPI 感知。系统缩放非 100% 时必须 `SetProcessDpiAwareness(2)`（`win32util.set_dpi_aware()`）。

**找不到窗口？** 最小化窗口的 `GetWindowRect` 返回 `(-25600,-25600)` 且不出现在 `list_windows()`，要先 `restore(hwnd)`。

**标题缺开头几个字？** 多半是 `--x-min` 阈值太大误杀了行首，不是裁剪切边。打印原始行确认。

**同一条重复很多？** 换 `--preset grid`；反之若不同文章被并成一条，换 `linear` 或 `off`。

**解析出的行是碎的？** 行分组必须按 OCR 行号 `L=`，别用 `y // 8` 整除分桶——跨过 8 的倍数就会劈开一行。

**GitHub 链接校验失败？** `github.com` 网页通道可能被封，`api.github.com` 通常放行。用 `gh_check.py`。

## 依赖

- Windows 10/11
- Python 3.10+ 与 Pillow
- Windows OCR 语言包（设置 → 语言 → 添加语言即可带上）

## License

MIT © dabing110
