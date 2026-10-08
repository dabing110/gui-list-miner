---
name: gui-list-miner
description: 从「没有 API、不能导出」的 Windows 桌面应用里批量挖出列表数据。当用户要整理/导出/盘点微信（点赞、最近阅读、收藏、视频号赞）、或任何 PC 客户端里的历史列表（聊天记录、订单、播放历史、收藏夹）时使用。方案是 Win32 滚动截屏 + Windows 内置 OCR + 几何布局解析 + 多级去重，零安装依赖。关键词：微信点赞整理、导出收藏、PC 客户端抓数据、没有 API 怎么导出、录屏太慢、GUI 列表挖掘、截图 OCR 批量。
version: 1.0.0
author: dabing110
license: MIT
metadata:
  hermes:
    tags: [windows, gui-automation, ocr, wechat, data-mining, screenshot, win32]
    related_skills: [windows-sandbox-install, github-push-via-api]
---

# GUI List Miner · 无 API 桌面应用的列表挖掘机

## 何时用

用户在 Windows 上想导出某个桌面客户端里的历史列表，但该应用：
- 没有开放 API / 没有导出按钮 / 不能复制全部
- 内容是虚拟滚动的（不滚动就只渲染可见区）
- UIA/无障碍树拿不到东西（很多 Qt / 自绘应用 UIA 树只有 2 个控件）

典型：微信点赞、微信最近阅读、视频号赞、收藏夹、某客户端的订单/播放/浏览历史。

**不适用**：有官方 API 的（直接调 API）、网页（用浏览器自动化）。

## 核心思路

```
定位窗口 → 滚动截屏（到底自动停） → 裁剪到列表区 → WinRT OCR
→ 按几何规律还原条目 → 跨帧去重 → 交叉去重 → 输出 CSV
```

关键认知：**不要试图一次截对，要靠「多帧冗余 + 去重」换准确率**。
单帧 OCR 有漏识（一字之差、整行丢失），但同一条目会在 3~10 帧里重复出现，
滚动步长小于半行高就能保证冗余覆盖。

## 五步流程

### 第 0 步：环境（必读，否则坐标全错）

```bash
export PATH="/usr/bin:/bin:/c/Windows/System32:$PATH"
PY="C:/Users/admin/.workbuddy/binaries/python/envs/default/Scripts/python.exe"   # 有 Pillow
```

- **必须用带 Pillow 的 Python**（`PIL`）。系统 `python` 通常没装。
- **Bash 里不能直接调 PowerShell**（会被安全策略拦截，报
  `Command blocked for security`）→ 跑 OCR 要用 PowerShell 工具，不是 Bash。

### 第 1 步：找到窗口

```bash
$PY -c "
import sys,ctypes,time
ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 关键：不设会坐标错位
sys.path.insert(0,'scripts')
import win32util as wu
for w in wu.list_windows(min_width=500): print(w.hwnd, w.title, w.rect)
"
```

**踩坑**：窗口被最小化时 `GetWindowRect` 返回 `(-25600,-25600)`，且不出现在
`list_windows` 里（IsWindowVisible=0）。此时按标题找不到，要先用已知 hwnd 还原：

```python
wu.user32.ShowWindow(h, 9)   # SW_RESTORE
time.sleep(1.0)
wu.user32.ShowWindow(h, 3)   # SW_MAXIMIZE
wu.user32.SetForegroundWindow(h)
```

还原后矩形与上次一致（如 `(-9,-9,1929,1039)`），坐标参数不用改。

### 第 2 步：滚动截屏

```bash
$PY scripts/capture.py --hwnd <H> --rel-x 0.93 --rel-y 0.5 --scroll -2 \
                       --max 800 --delay 0.7 --out data/raw/list
```

- `--rel-x/--rel-y`：鼠标悬停位置相对窗口的比例。**要落在列表区中部**，
  不要压在侧边栏/滚动条外侧。
- `--scroll -2`：每次滚 2 格（240px）。**经验值：步长 < 半行高的一半**，
  保证每条至少出现在 3 帧里。列表行高 100+ 用 -2，网格卡片用 -1。
- 到底自动停：dhash 汉明距离 ≤2 连续 4 次判为「没动」，再 nudge 重试 3 次确认。
- 后台跑（`run_in_background=True`），694 帧约 8 分钟。

**先试跑 5 帧**验证布局再全量：`--max 5`。

### 第 3 步：裁剪 + OCR

裁剪掉侧边栏/导航/标题栏，只留列表列，减少噪声：

```bash
$PY scripts/crop.py --src data/raw/list --dst data/cropped --box 1060,140,1938,1048
```

OCR 用 Windows 内置 WinRT 引擎（**零 pip 依赖，无需联网**，中英日韩都支持）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ocr_winrt.ps1 -Dir data\cropped -Lang zh-CN
```

输出 `<png>.txt`，词级带坐标：`L=<行号> X=.. Y=.. H=..\t<词>`，另有行级 `L=<i> T\t<整行>`。

**注意**：`OcrLine` 没有 BoundingRect，**只有 `OcrWord` 有** → 必须从词级坐标自己聚行。
**裁剪前 OCR、裁剪后解析会坐标错位** → 一定先裁剪再 OCR。

### 第 4 步：几何解析（本 skill 的技术核心）

OCR 给你一堆带坐标的词，要还原成「一条记录」。两条路子：

#### A. 线性列表（微信点赞 / 最近阅读）

靠**字高**和**间隙**切分：
- 标题行字高中位数 ≥ 15，来源/副标题 < 15
- 相邻条目间隙 > 42px → 新条目
- 日期行用正则排除：`今天|昨天|前天|星期[一二三四五六日天]|\d{1,2}月\d{1,2}日`
- UI 提示文本单独拉黑（如「只保留最近1个月的浏览记录」「清空阅读记录」）

```bash
$PY scripts/extract.py --mode linear --src data/cropped --out out/items.csv
```

#### B. 网格卡片（视频号 / 相册类）

靠**网格相位过滤**——这是排除封面大字干扰的杀手锏：

```
3 列卡片，列起点 x 固定（如 187/410/632），行周期 ≈352px
同帧内所有真标题的 y mod 352 都等于同一个相位 φ
作者行 y = 标题 y + 28
而封面字幕、时长角标「00:48」、点赞数「1.6万」的相位是随机的 → 被滤掉
```

参数：`PERIOD=352`、`PHASE_TOL=9`、`TITLE_H=15.0`、`AUTHOR_H=14.5`、
`X_ALIGN=22`（首字漏识时 x0 会右移 ~17px，容忍要放宽）。

作者行还得多挡几类噪声：时长 `\d{1,2}:\d{2}`、角标开头 `^[〔\[【〖（(]`、
点赞数误识出的「凸」、阿拉伯数字 ≥2 个、纯符号（如「·」）。

```bash
$PY scripts/extract.py --mode grid --src data/cropped --out out/items.csv \
                       --cols 187,410,632 --period 352
```

**调试手法**：解析不对时，把可疑列裁出来放大成 PNG 用 Read 工具直接看，
比对 OCR 输出定位是「裁剪切边」还是「OCR 漏识」还是「规则误杀」。
（曾经误判为切边，实际是 `x<55` 的过滤阈值误杀了 x0=34 的行首字。）

**行分组必须按 OCR 行号 `L=`**，不要用 `y // 8` 之类的整除分桶 ——
同一视觉行的 y 只要跨过 8 的倍数就会被劈成两行，把标题切碎
（实测同一份数据：整除分桶 2449 条 vs 按行号 2103 条，前者全是碎片）。

### 第 5 步：多级去重

OCR 噪声决定必须多层去重：

| 层 | 方法 | 解决的问题 |
|---|---|---|
| 1 | 指纹前缀归并 `fp()[:30]` | 同一条被截断成不同长度 |
| 2 | 子串丢弃（短串是长串子串则弃） | 片段行混入 |
| 3 | 字符集 Jaccard ≥ 0.9 | OCR 一字之差 |
| 4 | 标题 SequenceMatcher ≥ 0.78 **且** 作者 ≥ 0.6 | 双字误识（如「究竟」→「宄見」，ratio 只有 0.82） |
| 5 | 跨表交叉去重 | 多来源采集时防重复收录 |

```bash
$PY scripts/dedup.py --src out/items.csv --out out/clean.csv --preset grid
$PY scripts/dedup.py --src out/reads.csv --against out/likes.csv --out out/reads_clean.csv --preset linear
```

**坑**：相似度比较要用**原文**，别用去符号后的 fp 键（fp 丢信息会让 ratio 虚高/虚低）。

#### 预设怎么选（实测标定过）

| preset | 适用 | 参数 | 微信实测 |
|---|---|---|---|
| `grid` | 网格卡片，OCR 噪声重 | jaccard .90 / sim .78·.60 / min 6 / 双向子串开 | 629 → **628** |
| `linear` | 线性列表，文本较干净但同号系列标题多 | jaccard .98 / sim .90·.80 / min 12 / **双向子串关** | 620 → **601** |
| `off` | 只要前两层，作调参基线 | 跳过模糊层 | 620 → **622** |

**没有一组参数通吃**：grid 数据必须激进（同一条会带着 1~2 个错字反复出现），
linear 数据必须保守（同一公众号的系列标题措辞高度相似，激进会把不同文章并成一条）。
**先跑 `off` 拿基线**，再切 preset 对比差值。

调参验证法（判断是"漏合并"还是"过度合并"）：
```bash
python -c "... 对比新旧两份 title 的 fp 集合，看 extra / missing 各多少"
```
`extra>0`（新的里有旧的没有）→ 合并不足；
`missing>0` 且 `extra=0` → 多合并了，调高阈值。
目标是把 `extra` 压到 0 的同时让总数稳定下来。

### 第 6 步（可选）：补 GitHub 链接

用户特别在意链接真实性时，**每个仓库必须经 API 校验**再写进报告，
千万别凭模型记忆编 URL。

```bash
$PY scripts/gh_check.py repos owner/repo owner/repo
$PY scripts/gh_check.py search "关键词"     # 不确定归属时用，每次自动 sleep 7s
```

**关键**：`github.com` 网页通道会被封（HEAD 返回 000），
但 `api.github.com` 放行 → 一律走 API。取 `stargazers_count` 报真实 star。
search API 限速 10/min，脚本已内置 sleep。

## 微信专项参数

见 `references/wechat-layout.md`，含三个视图（点赞 / 最近阅读 / 视频号赞）的
入口路径、裁剪框、布局参数和已知数据边界。

## 输出约定

- 全量 CSV：每条带 `selected / grade(S-A-B) / theme / value / github` 标签
- 精选 Markdown：按主题聚类，S 级置顶
- 概况表注明「GitHub 链接全部经 API 校验」，让用户敢点

## 交付前自检

- [ ] 精选条目用的是**修正后的标题**，不是 OCR 原文（易漏：只换标签没换标题）
- [ ] 多来源合并时已排除交叉重复项
- [ ] GitHub 链接 100% 经 api.github.com 返回 200
- [ ] 报告里写明数据范围边界（如「最近阅读只保留 1 个月」）
