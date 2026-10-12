# 同类项目调研（Prior art）

调研时间：2026-10-12。目的：确认这个项目是**填补空缺**还是**重复造轮子**。

---

## 结论速览

| 问题 | 答案 |
|---|---|
| 有没有别人做"合并 TokenTracker 的数据"？ | ❌ **没有**。`tokentracker queue.jsonl`、`tokentracker export import` 等检索全部 0 命中 |
| TokenTracker 的 fork 里有人加过导出导入吗？ | ❌ 没有。前 10 个 fork 都是 ★1~2、多数已停更 |
| "多台电脑合并 AI 用量"这件事本身有人做吗？ | ✅ **有，而且不止一个**——说明需求真实存在 |
| 他们的做法和我们的区别？ | 他们要么**自己就是统计工具**（替换掉 TokenTracker），要么**依赖网络**（WebDAV / 自建服务器 / GitHub Gist）；没有一个是"给 TokenTracker 补一个纯离线文件合并" |

**一句话定位**：我们不是在做"又一个用量统计工具"，而是在做**"已经用 TokenTracker 的人，不改用别的工具、不联网、就能看到多机合计"**。

---

## 一、最接近的项目

### 1. ⚠️ `HUIdada1/Dosage_sync`（★2，JavaScript，无许可证）—— 最接近的竞品

> Windows 托盘常驻的开源 AI 用量统计工具：自动采集 ZCode、Codex、CodeBuddy、Qoder、
> DeepSeek Harness、WorkBuddy、Reasonix 等工具的 token 用量，**按设备增量同步到自建
> WebDAV 或本机 zip 备份**，**多台电脑汇总展示**。

| 维度 | Dosage_sync | 本项目 |
|---|---|---|
| 定位 | **自己是一个完整的统计工具** | **TokenTracker 的补充工具** |
| 数据来源 | 直接读各 AI 工具原始日志（17 个源） | 只读 TokenTracker 已聚合好的 `queue.jsonl` |
| 多机方式 | 自建 WebDAV（网络）或 zip 备份 | **U 盘文件，零网络** |
| 依赖 | 需要用户有 WebDAV（坚果云/NAS 等） | 什么都不需要 |
| 界面 | 托盘 + 趋势图 + 热力图 + 计费 | 只做合并/备份，看数据仍回 TokenTracker |

**关键差异**：用 Dosage_sync 意味着**换掉 TokenTracker**；用本项目意味着**继续用 TokenTracker**。
这是两类用户，不是同一批。但它和 TokenTracker 本身是竞争关系（都在采集原始日志）。

### 2. `Ilosyi/TokenStats`（★0，Swift，MIT）—— macOS

- macOS 原生，支持 Claude Code / Codex / OpenCode / ZCode / WorkBuddy
- **有"统计库导出 / 合并导入（幂等）"** ← 和我们的核心功能几乎一样
- 同步方式：WebDAV 云同步（坚果云 / Nextcloud / 群晖）
- 限制：**仅 macOS**；且它自己也是统计工具

### 3. `wangtae/claude-code-usage-analytics`（★6，Python，MIT）

- 用 **GitHub Gist** 做多机同步（JSON 增量）
- 只针对 **Claude Code 的 `~/.claude` 原始数据**，不是 TokenTracker 的数据
- 明确强调"never modifies original `~/.claude/` files"

### 4. `heffter/tokemetry`（★0，Python，MIT）

- 自述：**"No existing tool aggregates usage across multiple machines; tokemetry is..."**
- 架构：每台机器一个 collector → **自建服务器**（VPN 内）→ Web 面板，Docker
- 部署成本明显高于本项目

### 5. `Yaxuan42/ccmerge`（★0，TypeScript，MIT）

- 用**一个私有 GitHub 仓库当同步后端**，每次变更一个 commit（可追溯、可回滚）
- 同步的是 `~/.claude/projects/` 和 `~/.claude/skills/`——**整个会话数据**，不只是用量
- 需要用户有 GitHub 私有仓库 + 配好 git 凭证

### 6. `V5U2/ai-usage-tracker`、`ruansheng8/token-insight`、`heroims/agents-report`

都是"本地采集 + 可选聚合服务器 + 面板"的路子，`agents-report` 面向团队周报。

---

## 二、现有方案的共同套路

| 传输方式 | 代表项目 | 代价 |
|---|---|---|
| 自建服务器 / Docker | tokemetry、ai-usage-tracker、token-insight | 要维护机器、要内网穿透或 VPN |
| WebDAV 云盘 | Dosage_sync、TokenStats | 要注册/自建网盘，数据出本机 |
| GitHub Gist / 私有仓库 | claude-code-usage-analytics、ccmerge | 要 GitHub 账号与凭证，数据上云 |
| 局域网 | （TokenTracker 官方也有 LAN Sync） | 两台机器要在同一网络 |

**没有一个是"插个 U 盘，拷一个文件"**。这是本项目最干净的差异点：

- 不需要任何账号
- 不需要任何服务器、网盘、Docker
- 数据不出本机（对"无联网"有硬性要求的人——公司内网、涉密环境、纯粹洁癖——这是唯一解）

---

## 三、我们的差异化（写进 README 的建议）

### 三句话版本

> TokenTracker 官方没有本地导出/导入，跨设备合并只能用它自己的付费云同步。
> 本工具用**一个文件 + U 盘**做到同样的事：**不联网、不要账号、不要服务器**。
> 而且它懂 TokenTracker 的去重语义——**同机取最后一条、跨机相加**，这是直接拼接文件
> 一定会做错的地方。

### 对比表（可直接放进 README）

| | 官方 Cloud | Dosage_sync 等独立工具 | **本项目** |
|---|:---:|:---:|:---:|
| 需要联网 | ✅ 必须 | ✅ WebDAV/服务器 | ❌ **完全不需要** |
| 需要账号 | ✅ | 视方案 | ❌ **不需要** |
| 需要自建服务 | ❌ | ✅ 多数需要 | ❌ **不需要** |
| 继续用 TokenTracker | ✅ | ❌ 要换工具 | ✅ **不用换** |
| 价格 | $4.99/月 | 免费 | 免费 |
| 适用 | 所有平台 | 视项目 | Windows / macOS / Linux（Python） |

### 诚实地说清楚自己的边界

- 这是**小工具**，不是统计工具。看趋势、看热力图、看费用，请用 TokenTracker 面板
- 只支持 TokenTracker 的数据格式（有意为之，不做通用方案）
- 需要用户手动搬运文件（换来的是零依赖、零网络）

---

## 四、其他有价值的发现

### 4.1 命名：`tt-sync` 确实撞名，改名是对的

| 包名 | npm | 说明 |
|---|---|---|
| `tokentracker-sync` | **未占用** ✅ | 我们打算用的名字 |
| `tokentracker-merge` | 未占用 ✅ | 备选 |
| `ttsync` | **已被占用** ❌ | 印证了改名的必要性 |
| `tokentracker-cli` | 已被占用 | 官方包 |
| `ccmerge` | 未占用 | 但已被 `Yaxuan42/ccmerge` 用作项目名 |

### 4.2 需求被反复验证，但**没人做成功**

同类项目星数：★0、★0、★0、★1、★1、★2、★6。**最高的才 6 颗星。**

这说明两件事：
- ✅ **需求真实存在**——这么多人在不同方向尝试同一个问题
- ❌ **没有赢家**——所以也没有"已经被做完了"的问题，切入点仍然开放

为什么都没做起来？推测：它们**要求用户付出额外的运维成本**（搭服务器、配 WebDAV、
建 git 仓库），而"多台电脑看合计"这件事本身的价值不足以支撑这点成本。
我们选的"零成本零依赖"路线，恰好绕开了这个坑。

### 4.3 这条赛道有被上游吞掉的风险

TokenTracker 官方把跨设备合并放在付费 Cloud 里，一旦它开放免费本地导出导入，
本项目的核心价值就会消失。**因此 `docs/UPSTREAM-NOTICE.md` 里主动提出"把实现交给上游"
不只是礼貌，也是一种理性的退出路径。**

---

## 五、给项目的三条建议

1. **README 第一屏就讲清"不联网、不用换工具"** —— 这是唯一别人没占的位置
2. **别把范围扩大到"通用 AI 用量统计"** —— 那条路上已经有 Dosage_sync 等一堆人在挤
3. **保持"能随时被上游吸收"的姿态** —— 代码写清楚、语义文档化，等于给对方一个现成的 PR
