# 给上游作者的说明（打招呼文案）

**用途**：在 TokenTracker 仓库开一个 issue / Discussion，告知你写了这个第三方工具。
**为什么值得做**：成本几分钟，但如果对方认可，可能在 README 里链接你（曝光量很可观）；
如果对方反对，你能在事情变大之前决定是否改名或下线。

**重要**：不要写成广告。语气客气、事实为主、给出对方"反对也没关系"的台阶。
**最关键的一句是最后那个提议** —— 主动提出"如果官方愿意支持这个功能，我可以把逻辑整理成
PR 交给你"，这会把关系从「绕过你付费墙的人」变成「想给你贡献代码的人」。

---

## 建议发在

- 仓库 https://github.com/xiufengsun/TokenTracker 的 **Discussions**（更合适，不会刷 issue 列表）
- 或者开一个 issue，标签选 **Feature request**

---

## English version（推荐用这个，公开可见）

**Title:** Third-party local export/import tool for multi-machine usage — feedback welcome

```markdown
Hi, thanks for building TokenTracker — I've been using it daily and it's the only
thing that gives me an honest picture of my AI coding costs.

**Background.** I work across two machines and wanted the combined total.
I read the README and `docs/cloud-guide.md`, and my understanding is that
cross-device merging is deliberately a Cloud feature, while the free/local tier is
single-machine. That's a reasonable product decision, so instead of asking for a
change I wrote a small companion tool for my own use — and then realised other
people probably have the same two-machine problem, so I made it public.

**What it is:** a small MIT-licensed utility that merges the local usage data of
two machines via a plain file and a USB stick. No account, no network, no subscription.

**Repo:** <REPO_URL>

**What it deliberately does *not* do:**
- It does not access, call, scrape or authenticate against any TokenTracker server or API.
- It does not bypass, patch or modify TokenTracker in any way.
- It only reads and writes the local files under `~/.tokentracker/tracker/`.
- It refuses to run while Cloud sync is enabled (rewriting `queue.jsonl` would
  invalidate the upload byte offset), unless the user explicitly forces it.

**One thing I want to be upfront about:** this does provide, for free, something your
Cloud tier sells. I understand if that's not something you want in the ecosystem.
If so, just say the word and I'll rename the project, or take it down — no hard feelings,
and I'll keep using and recommending TokenTracker either way.

**And an offer:** if you'd ever like this capability in the official app, I'm happy to
hand the implementation over. The non-obvious part is the merge semantics, which I
documented in `docs/MERGE-RULES.md`: TokenTracker deduplicates its queue by
`source | model | hour_start` keeping the **last** row per key, so within one machine
repeated rows are cumulative updates (last-wins), but across two machines the same key
is two independent contributions and must be **summed**. Getting that backwards either
loses data or double-counts it. I'd be glad to write it up as a spec or a PR in
whatever shape fits your architecture.

Either way, thanks for the tool.
```

---

## 中文版（如果想用中文说）

**标题**：关于多机用量合并的第三方本地工具 —— 欢迎指正

```markdown
你好，先感谢你做了 TokenTracker，我每天都在用，是目前唯一能让我看清 AI 编码开销的工具。

**背景**：我在两台电脑上工作，想要合并后的总量。我读了 README 和 docs/cloud-guide.md，
理解跨设备合并是有意放在 Cloud 里的，本地/免费部分只覆盖单机。这是个合理的产品决定，
所以我没有要求你改，而是自己写了个小工具自用；后来想到可能别人也有同样的两台电脑问题，
就把它公开了。

**它是什么**：一个 MIT 许可的小工具，用普通文件 + U 盘把两台电脑的本地用量数据合并起来。
不需要账号、不联网、不订阅。

**仓库**：<REPO_URL>

**它刻意不做的事：**
- 不访问、不调用、不抓取、不鉴权你的任何服务器或 API
- 不以任何方式绕过、补丁或修改 TokenTracker 本体
- 只读写 ~/.tokentracker/tracker/ 下的本地文件
- 检测到云同步开启时拒绝执行（重写 queue.jsonl 会让上传的字节偏移失效），除非用户强制

**有一点我想先说清楚**：这个工具确实免费提供了你 Cloud 里卖的功能。如果这不在你希望看到的
生态里，你说一声就行，我会改名字或者直接下掉——不会有任何不快，我也会继续用和推荐
TokenTracker。

**另外一个提议**：如果你希望官方支持这个能力，我很愿意把实现交给你。这个功能不直观的地方
在于合并语义，我写在 docs/MERGE-RULES.md 里了：TokenTracker 的队列按
`source | model | hour_start` 去重、同键【保留最后一条】，所以单机内重复行是该小时的累计更新
（取最后一条），而两台机器的同一个键是两份独立用量、必须【相加】。弄反了会丢数据或重复计数。
我可以按你的架构写成规格说明或者直接提 PR。

不管怎样，谢谢你的工具。
```

---

## 发出前检查

- [ ] 把 `<REPO_URL>` 换成真实仓库地址
- [ ] 确认仓库已经公开，README 顶部有非官方声明
- [ ] 确认 `docs/MERGE-RULES.md` 已推送（文案里引用了它）
- [ ] 语气再读一遍：**别像推销，别像挑衅**
- [ ] 发出去之后：不管对方回什么，都保持礼貌。对方有负面反应时，优先考虑改名/下线
