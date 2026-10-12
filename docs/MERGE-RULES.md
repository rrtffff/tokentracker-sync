# 合并规则（精确说明）

这份文档描述 `tt-sync` 的合并算法，以及为什么必须这么做。
如果你要自己实现，或者想验证本工具的正确性，请看这里。

---

## 1. TokenTracker 的读取语义

面板读取的是 `~/.tokentracker/tracker/queue.jsonl`，每行一个 JSON 对象，代表
**一个 30 分钟桶**的用量：

```json
{"source":"claude","model":"claude-sonnet-4.5","hour_start":"2026-10-01T10:00:00.000Z",
 "input_tokens":1200,"output_tokens":340,"cached_input_tokens":0,
 "cache_creation_input_tokens":0,"reasoning_output_tokens":0,
 "total_tokens":1540,"billable_total_tokens":1540,"conversation_count":1}
```

读取时（对应上游 `LocalAPI.readQueueData`）：

```js
const key = `${row.source}|${row.model}|${row.hour_start}`;
seen.set(key, normalizeQueueRow(row));   // 同键【覆盖】，即保留最后一条
const rows = Array.from(seen.values());  // 之后再按天汇总求和
```

两点结论：

1. **去重键**是 `source | model | hour_start`（项目队列是 `project_key | source | hour_start`）。
2. **同键保留最后一条，不做相加。** 面板的"求和"发生在去重**之后**，按天汇总那一层。

---

## 2. 由此产生的两个相反语义

### 2.1 同一台机器内：取最后一条（last-wins）

同一台机器上，同一个桶会被多次写入——用量在那一小时内增长，每次都写一条
**累计值**（不是增量）。所以文件里会有：

```
{"source":"claude","model":"m","hour_start":"10:00",...,"total_tokens":100}
{"source":"claude","model":"m","hour_start":"10:00",...,"total_tokens":120}   ← 更晚、更完整
```

正确结果是 **120**，不是 220。这正是 TokenTracker 自己的做法。

### 2.2 两台机器之间：必须相加（sum）

笔记本和台式机在同一个 `10:00` 都用了 `claude/m`，各自写了一条。这两条代表
**两台机器各自的用量**，正确的合并结果是 `A + B`。

但如果只是把两个文件拼起来，TokenTracker 的 last-wins 会让后一条**覆盖**前一条——
一台机器的数据就没了。

**所以：绝对不能直接拼接。**

---

## 3. 算法

对每个队列（`main` 与 `projects` 各做一遍）：

```
输入：
  own_rows     本机 queue.jsonl 中【不带 _ttsync 标记】的行
  snapshots    已导入的每台来源机器的快照行（存在 tt-sync-state.json 里）

1. own    = dedupe_last_wins(own_rows)          # 机器内 last-wins
2. S_i    = dedupe_last_wins(snapshot_i)        # 每台来源机器内部也先 last-wins
3. keys   = keys(own) ∪ ⋃keys(S_i)              # 所有出现过的桶
4. 对每个 key：
     value = own[key]
     对每台来源机器 i：value 的数值字段 += S_i[key] 的数值字段（缺失视作 0）
     非数值字段优先取 own 的，own 没有才取来源的
5. 输出顺序：控制行 + 缺 hour_start 的行  →  own 原始行  →  合成行（带 _ttsync 标记）
```

### 为什么合成行要放最后

因为读取是 last-wins。合成行是 own 与各来源相加后的结果，必须排在 own 原始行
**之后**才能被读到。放前面就会被 own 的原始行覆盖。

### 为什么合成行要带标记

`_ttsync` 标记让下一次运行时可以把它识别出来并剔除，从而**从"本机原始行 + 各来源
快照的绝对总量"重新计算**，而不是在已有结果上再加一次。这就是幂等的来源。

---

## 4. 幂等性

每次运行都：

```
删除全部 _ttsync 行  →  重新从 own + 各来源快照 计算  →  写出
```

因为来源快照的**绝对总量**存在 `tt-sync-state.json` 里（而不是增量的差值），
所以：

- 同一份数据包导入 1 次、3 次、10 次，结果完全一样；
- 来源机器更新了快照（同桶数值变大、新增桶），导入后自动反映为正确的新总量。

---

## 5. 导出为什么必须剔除合成行

假设 A、B 两台机器互相导入过，A 的队列里既有自己的原始行，也有从 B 导入的合成行。

如果 A 导出时把合成行也带上，那么这份导出包拿到 B 上导入时，就会把 **B 自己的
数据再次加回 B**——重复计数。

所以导出时**只取不带 `_ttsync` 标记的行**。这样：

- A 导出的包只含 A 自己的用量；
- 无论 A↔B 来回搬运多少次，都不会循环累加。

---

## 6. 边界情况

| 情况 | 处理 |
|---|---|
| `kind` 字段的控制记录（`account_session_state` 等） | 原样透传，不参与合并；本机自己的记录逐字节保留，不搬运对方的 |
| 缺 `hour_start` 的行 | 原样透传，不参与合并 |
| 行 JSON 损坏（写入中被截断） | 跳过并计数，不影响其他行 |
| 导出包与本机 `machineId` 相同 | **拒绝导入**（是自己导出的，导回去会重复计数） |
| 云同步开启 | **拒绝导入**（重写文件会让云上传的字节偏移失效），需 `--force` |
| 写入过程 | 先备份原文件，再写临时文件 + 原子 rename |

---

## 7. 与 JS 原型的一致性

本项目的逻辑最初以 JavaScript 实现（`tt-sync.js`）。Python 版本发布前做了一次
**对拍测试**（`tests/diff_test.py`）：同一份起始数据、同一份数据包，分别跑两个实现，
比较结果：

- 控制记录逐字节一致
- 合成行数量一致
- 去重后键集合一致
- **每个桶的数值完全一致**
- 面板口径合计完全一致
- 两边各自重复导入都幂等
- 状态文件记录的来源机器与行数一致

11/11 通过，合计精确到个位相同。
