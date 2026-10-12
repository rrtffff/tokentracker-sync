# tt-sync

**把 TokenTracker 的用量数据在多台电脑之间合并起来 —— 纯本地、免费。**

[English](README.md) · [合并规则](docs/MERGE-RULES.md) · [更新日志](CHANGELOG.md)

![tt-sync 界面](docs/screenshot.png)

---

## 为什么会有这个工具

[TokenTracker](https://github.com/xiufengsun/TokenTracker) 是一个很好用的本地 AI 编码用量统计工具。但它有个空缺：**没有本地的用量导出 / 导入功能**。如果你有两台电脑想看到合并后的总量，官方唯一的答案是它的付费云同步（`$4.99/月`）。

这一点我逐条核对过官方仓库：

| 核查项 | 结果 |
|---|---|
| README 是否提到导出本地用量数据 | ❌ 只有 `status --json`（给 AI 读的摘要），不是数据导出 |
| README 是否提到导入本地数据 | ❌ 这个词从未出现 |
| 多设备合并 | 🔒 仅云端：*"Optional cloud sync merges usage across your machines"* |
| 官方有没有规划本地导出导入 | ❌ 没有。`docs/cloud-guide.md` 明确把**同步和备份**都列为付费云服务 |
| 官方 issue 里有没有同类需求 | ❌ `multiple computers` = 0 条，标题含 `export` = 0 条，标题含 `backup` = 0 条 |

于是 `tt-sync` 用**本地文件 + U 盘**把这个空缺补上：不需要账号、不联网、不订阅。

> 这是第三方社区工具，与 TokenTracker 官方无关，也未获其背书。
> 它**从不修改 TokenTracker 本身**，只动数据文件，而且每次动之前都先备份。

---

## 这件事为什么不是"拷个文件"那么简单

你不能直接复制或拼接数据文件。TokenTracker 读队列时按 **`source | model | hour_start`** 三个字段去重，**同键只保留最后一条**，绝不自动相加。这产生了两个方向相反的情况：

| 情况 | 正确处理 | 直接拼接的后果 |
|---|---|---|
| **同一台机器内**同键重复（该小时的累计更新） | 取**最后一条** | ✅ 恰好正确 |
| **两台机器**同键（同小时、同工具、同模型，各自独立的用量） | 必须**相加** | ❌ 一台的数据被静默丢掉 |

弄反了就是丢数据或重复计数。`tt-sync` 的做法是：**先按机器各自 last-wins 去重，再跨机器相加**，写回时给合成行打上幂等标记，所以重复导入永远不会累加。

完整规则见 [docs/MERGE-RULES.md](docs/MERGE-RULES.md)。

---

## 功能

- 🔀 **导出 / 导入** —— 把两台电脑的用量合并成一个视图
- 💾 **完整备份 / 还原** —— 关键数据打成单个文件，可字节级还原
- 🧾 **幂等** —— 同一份文件重复导入，结果完全不变
- 🛡️ **安全** —— 每次导入前自动备份；拒绝导入本机自己的导出包；云同步开启时拒绝执行（可用 `--force` 强制）
- 🔍 **自带说明书** —— 导出的文件里带 `_spec` 字段写明合并规则，另一台电脑上的 AI 或人不用看这份 README 也能正确处理
- 🖱️ **图形界面** —— 一个 `.exe`，导出 / 导入 / 备份 / 还原；支持把文件拖进窗口，也支持拖到 exe 图标上
- ⌨️ **命令行** —— 同一套逻辑，可脚本化
- 🧩 **核心零第三方依赖**（只用标准库）

---

## 快速开始

### 方案 A —— 用 exe（Windows，最省事）

1. 从 [Releases](../../releases) 下载 `TTSync.exe`，放哪儿都行（比如就放在 TokenTracker 安装目录旁边）
2. **两台电脑都先在 TokenTracker 里关闭云同步**
3. A 机：运行 `TTSync.exe` → **导出数据** → 选 U 盘 → 得到 `tt-usage-<机器名>-<时间>.json`
4. B 机：运行 `TTSync.exe` → **导入并合并** → 选中那个文件（也可以直接拖进窗口，或拖到 exe 图标上）
5. 打开 TokenTracker —— 面板已经显示 A + B 的合计，**不用重启**

想让 A 也看到合计：把 B 导出的文件带回 A 再导入一次。这个方向是**双向安全**的——导出时绝不会带上你导入进来的数据，所以 A↔B 来回搬运不会重复计数。

### 方案 B —— 跑源码

```bash
git clone https://github.com/<you>/tt-sync.git
cd tt-sync
python tt-sync.py            # 图形界面
python tt-sync.py status     # 命令行
```

需要 Python 3.8+ 且带 tkinter（官方 Windows/macOS 安装包自带）。**不需要 pip 安装任何东西。**

### 自己打包 exe

```bash
pip install pyinstaller tkinterdnd2
pyinstaller --noconfirm --onefile --noconsole --name TTSync --collect-all tkinterdnd2 tt-sync.py
# 产物：dist/TTSync.exe
```

或直接运行 `packaging/build_windows.bat`。

---

## 命令行用法

```bash
python tt-sync.py                            # 图形界面
python tt-sync.py <数据包.json>               # 图形界面 + 直接导入该文件

python tt-sync.py export --to "E:\"          # 导出（--format json|csv，默认 json）
python tt-sync.py import "E:\tt-usage-*.json" --dry-run   # 只预览
python tt-sync.py import "E:\tt-usage-*.json"             # 正式合并
python tt-sync.py status                     # 本机合计 + 已导入来源

python tt-sync.py backup --note "月度备份"    # 完整备份（单个文件）
python tt-sync.py backups                    # 列出备份
python tt-sync.py restore-backup 0           # 整体还原某个备份（覆盖）
python tt-sync.py restore 0                  # 回滚某一次导入（导入前自动备份）
```

### 合并 vs 备份 —— 千万别搞混

| | 用途 | 数据怎么变 | 文件 |
|---|---|---|---|
| `export` / `import` | **两台电脑合并** | 相加 | `tt-usage-<机器名>-<时间>.json` |
| `backup` / `restore-backup` | **本机整体退回** | **覆盖**现有数据 | `tt-backup-<机器名>-<时间>.json` |

**绝对不要**把 A 机的完整备份还原到 B 机——那会把 B 自己的数据整个毁掉。

---

## 已知限制

1. **先关云同步。** 导入会重写 `queue.jsonl`，云上传用的字节偏移会失效。工具默认会拦住你，确认要做才加 `--force`。
2. **最近 1 小时的数据可能短暂偏小。** TokenTracker 偶尔会重算当前小时的桶并追加新行，那条新行会盖掉合成行。**重新导入一次就修正**（幂等，不会重复计数）。隔天以上的数据不受影响。
3. **两台机器同小时、同模型会相加。** 这是对的，是两台机器各自的真实用量。
4. **云会话状态控制记录不会搬运。** 那是每台机器各自的云端账目；你自己的记录会被逐字节保留。
5. **网页版的跨设备视图仍然只走官方云。** 本工具只影响本机桌面版面板。

---

## 怎么验证它是安全的

```bash
python tests/diff_test.py
```

在临时目录里用合成的双机数据跑一遍合并，断言：跨机器相加、机内 last-wins、幂等性、控制记录保留，并且——因为最初的原型是 JavaScript 写的——做一次**与 JS 实现的对拍测试**，断言每个桶的数值和合计完全一致。

## 法律声明

- **非官方工具。** 本项目与 TokenTracker 项目无关联、未获其背书。
  它只与 TokenTracker 的磁盘文件格式互操作，**没有复制上游任何源码**。
- **无联网。** 程序里没有任何联网代码，可以自行阅读源码确认。
- 发布版二进制中打包的第三方组件许可（CPython、Tcl/Tk、tkinterdnd2、PyInstaller
  bootloader）见 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)。
- 完整的风险分析、商标讨论、素材版权与分发注意事项：
  [docs/LEGAL-AND-RISKS.md](docs/LEGAL-AND-RISKS.md)
- 维护者请看 [docs/RELEASING.md](docs/RELEASING.md)（发布流程）。

## 目录结构

```
tt-sync/
├── tt-sync.py            入口（默认开图形界面）
├── ttsync/
│   ├── core.py           合并 / 导出 / 备份 / 还原（只用标准库）
│   ├── cli.py            命令行
│   └── gui.py            tkinter 图形界面
├── assets/               生成出来的界面图片（背景 / 角色 / 图标）
├── art/mascot.jpg        原始角色素材
├── tests/diff_test.py    对拍 + 回归测试
├── packaging/            资源生成脚本 + PyInstaller 打包脚本
└── docs/MERGE-RULES.md   精确的合并语义
```

## 关于角色素材

`art/mascot.jpg` 是界面背景、窗口内角色形象和程序图标所用的原始插画；
`assets/` 里的图片由 `python packaging/make_assets.py` 从它生成。

**如果你要把这个项目发布到 GitHub，请把 `art/mascot.jpg` 换成你自己拥有版权、
或明确允许再分发的素材**；或者直接删掉 `art/` 和 `assets/` 里的图片，
界面会自动降级为无背景图（功能不受影响）。

## 许可证

MIT，见 [LICENSE](LICENSE)。
