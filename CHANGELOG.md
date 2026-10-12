# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [1.0.0] — 2026-10-12

首个版本。

### 新增

- **导出 / 导入** —— 把多台电脑的 TokenTracker 用量合并到一个视图。
  算法为「按机器各自 last-wins 去重 + 跨机器相加 + 合成行带 `_ttsync` 幂等标记」。
- **完整备份 / 还原** —— 关键数据文件打包成单个 JSON，可字节级还原；
  还原前会把当前数据另存一份，所以操作可逆。
- **图形界面**（`TokenTrackerSync.exe`）—— 导出（可选目标文件夹）、导入（可选文件 / 拖放 / 拖到 exe 图标）、
  创建备份、备份列表与还原、回滚某次导入、打开数据与备份目录。
  界面带角色插画背景（`assets/bg.png`，由 `art/mascot.jpg` 生成）。
- **命令行**（`TokenTrackerSync-cli.exe` 或 `python tokentracker-sync.py`）——
  `export` / `import` / `status` / `doctor` / `backup` / `backups` / `restore-backup` / `restore`。
- **环境自检 `doctor`** —— 一键输出数据目录、文件大小、资源路径、拖放支持、
  导入账目等，方便贴到 issue 里定位问题。
- **程序图标** —— 从插画头部裁切生成（`assets/icon.ico`，多尺寸）。
- **自描述导出包** —— 每个导出文件的 `_spec` 字段写明键字段、合并规则、执行步骤与禁止事项，
  另一台电脑上的 AI 或人无需本 README 即可正确处理。
- **安全护栏**：
  - 每次导入前自动备份原文件
  - 拒绝导入本机自己导出的数据包（`machineId` 相同）
  - 云同步开启时拒绝导入（重写文件会使云上传字节偏移失效），可用 `--force` 覆盖
  - 云会话控制记录原样透传，不搬运其他机器的
  - 原子写入（临时文件 + rename）
- **对拍测试** —— `tests/diff_test.py` 与最初的 JavaScript 原型逐桶比对数值与合计。
  测试**不依赖任何本机真实数据**，全部样本在脚本内合成，克隆仓库即可运行；
  JS 对拍部分通过环境变量 `TT_SYNC_JS_TOOL` 可选启用。
- **界面素材生成脚本** —— `packaging/make_assets.py`，从 `art/mascot.jpg` 生成
  背景图、窗口内角色图与图标（构建时才需要 Pillow，运行期不需要）。
- **`core.use_home()`** —— 把所有路径重定位到指定目录，便于在临时目录里跑完整流程
  （测试用），也支持需要多份独立数据的高级用法。

### 安全性（本版做了一次针对性审计，详见 `docs/SECURITY.md`）

威胁模型只有一条：**「别人给你的那份数据包文件」**，所以导入路径一律按不可信输入处理。

- **修复**：数值不再可能产出 `NaN` / `Infinity`。Python 的 `json.dumps` 默认会写出这两个
  字面量，但它们是**非法 JSON**，上游 JS 的 `JSON.parse` 会直接抛错 —— 等于本工具把用户
  数据写坏。现在写入端强制 `allow_nan=False`，读取端把非有限数当损坏数据，导入前逐行校验。
- **修复**：导入前按**数值字段白名单**清洗每一行。此前一份构造出来的数据包可以把任意
  字段（含 `kind`）塞进用户的队列，改变 TokenTracker 对该行的解释。
- **修复**：合并时不再「从拼接出来的键字符串反推字段」。字段值里含 `|` 会导致
  字段错位、数据损坏；现在扫描时顺带保留原值。
- **新增护栏**：数据包体积上限 256 MB、单队列行数上限 30 万、来源机器数上限 64 台
  （每台的全部行都会永久保存在状态文件里，此前无上限增长）。
- **加固**：还原备份时改为校验解析后的绝对路径确实落在数据目录内，并显式拒绝
  含驱动器号与 NUL 的文件名。
- **修正**：已有的无法解析的行不再被静默丢弃，而是作为原始文本保留在文件末尾并报告数量。
- **新增** `tests/security_test.py`：15 条断言，每条对应一个具体发现，含
  「正常合并仍然正确」的反向验证。



- 项目名从 `tt-sync` 改为 **TokenTracker Sync**（仓库名 `tokentracker-sync`）。
  原因：GitHub 上 `Darkatse/TT-Sync`（一个无关的 Rust 项目）重名，且其 Windows
  二进制同样叫 `tt-sync.exe`，容易混淆。详见 `docs/LEGAL-AND-RISKS.md` §2。
- 二进制：`TokenTrackerSync.exe` / `TokenTrackerSync-cli.exe`；
  入口脚本：`tokentracker-sync.py`。
- 以下兼容性标识**有意保留**：`tt-sync-state.json`、`~/.tokentracker/tt-sync/`、
  数据包格式 `tokentracker-tt-sync`。

- **界面素材生成脚本** —— `packaging/make_assets.py`，从 `art/mascot.jpg` 生成
  背景图、窗口内角色图与图标（构建时才需要 Pillow，运行期不需要）。

### 已知限制

- 最近 1 小时的数据在来源机器重算该小时桶后可能短暂偏小，重新导入即可修正（幂等）。
- 网页版跨设备视图仍由官方付费云提供，本工具只影响本地桌面版面板。
