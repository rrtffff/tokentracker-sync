# 发布到 GitHub（一步步来）

前提：项目已经在本地提交好了（`git log` 能看到 `feat: tt-sync v1.0.0`）。

---

## 第 1 步：先在网页上建仓库

1. 打开 https://github.com/new
2. **Repository name** 填仓库名（命名取舍见 `docs/LEGAL-AND-RISKS.md` §2）
3. **不要**勾选 "Add a README file"、".gitignore"、"license" ——
   本地已经有这些文件了，勾了会产生冲突
4. 点 **Create repository**

建好后 GitHub 会显示一串命令，我们只用其中两行。

---

## 第 2 步：把本地代码推上去

在项目目录里执行（把 `你的用户名` 和 `仓库名` 换成实际的）：

```bash
cd "C:/Users/me/Desktop/OpenSource/tt-sync"
git remote add origin https://github.com/你的用户名/仓库名.git
git push -u origin master
```

> 如果提示分支叫 `main`，把最后一句的 `master` 换成 `main`。
> 第一次推送会弹窗要求登录 GitHub（浏览器授权或填 Personal Access Token）。

推送完成后刷新仓库页面，应该能看到 README 和代码。

---

## 第 3 步：准备 Release 附件

先在本地打包（如果还没打包，或改过代码之后）：

```bash
cd "C:/Users/me/Desktop/OpenSource/tt-sync"
python packaging/build_windows.bat
```

> 需要 Python 3.8+（带 tkinter）和 `pip install pyinstaller tkinterdnd2`。
> 如果用的是我帮你配好的环境，直接跑：
> `"C:/Users/me/.workbuddy/binaries/python/envs/ttsync-312/Scripts/python.exe" packaging/prepare_release.py`

然后收集发布文件并生成校验值：

```bash
python packaging/prepare_release.py v1.0.0
```

这会在 `release/` 目录里生成 5 个文件：

```
release/
├── TTSync.exe              图形界面版（约 11.8 MB）
├── ttsync-cli.exe          命令行版（约 11.8 MB）
├── SHA256SUMS.txt          校验值
├── LICENSE                 MIT 许可证
└── THIRD_PARTY_NOTICES.txt 第三方组件声明
```

---

## 第 4 步：在 Release 页面发布

1. 打开仓库页面，右侧栏点 **Releases**（或直接访问
   `https://github.com/你的用户名/仓库名/releases`）
2. 点 **Draft a new release**（如果是第一次，按钮可能显示 **Create a new release**）
3. **Choose a tag** → 输入 `v1.0.0` → 点 **Create new tag: v1.0.0 on publish**
4. **Release title** 填 `v1.0.0`
5. **Describe this release** 里写更新说明，可以直接用下面的模板：

```markdown
首个版本。

## 它解决什么问题
TokenTracker 官方没有提供本地的用量导出 / 导入，跨设备合并只能用它的付费云同步。
本工具用纯本地文件 + U 盘的方式，把多台电脑的用量合并到一个视图。

## 下载哪个
| 文件 | 说明 |
|---|---|
| `TTSync.exe` | 图形界面版，双击即用，**大多数人下这个** |
| `ttsync-cli.exe` | 命令行版，适合写脚本 |

两个都是单文件，**不需要安装 Python 或其他任何东西**。

## 怎么用
1. 两台电脑都先在 TokenTracker 里关闭云同步
2. A 机：运行 `TTSync.exe` → 导出数据 → 存到 U 盘
3. B 机：运行 `TTSync.exe` → 导入并合并（也可以把文件直接拖进窗口）
4. 打开 TokenTracker，面板已经显示两台合计，不用重启

## 安全说明
- 全程本地读写，**不联网、不上传**（可自行查看源码确认）
- 每次导入前自动备份，操作可回滚
- 校验值见 `SHA256SUMS.txt`：`certutil -hashfile TTSync.exe SHA256`

## 已知限制
最近 1 小时的数据在来源机器重算该小时桶后可能短暂偏小，重新导入一次即可修正（幂等，不会重复计算）。

> 第三方工具，与 TokenTracker 项目无关联、未获其背书。
```

6. **Attach binaries** → 把 `release/` 里那 5 个文件全部拖进虚线框
   - 一定要包含 `SHA256SUMS.txt`，这是下载者验证文件完整性的唯一依据
7. 如果这是正式版，勾选 **Set as the latest release**
8. 点 **Publish release**

---

## 第 5 步：检查

发布后打开
`https://github.com/你的用户名/仓库名/releases/latest`，
确认：

- [ ] 5 个附件都在，能正常下载
- [ ] `TTSync.exe` 下载后双击能打开界面
- [ ] README 页面顶部的截图能正常显示（说明 `docs/screenshot.png` 推上去了）
- [ ] 仓库根目录能看到 `LICENSE` 和 `THIRD_PARTY_NOTICES.txt`

---

## 以后发新版本

```bash
# 1. 改代码、更新 CHANGELOG.md 和 ttsync/__init__.py 里的 __version__
# 2. 提交
git add -A && git commit -m "feat: v1.1.0 ..."
git push

# 3. 重新打包 + 收集附件
python packaging/prepare_release.py v1.1.0

# 4. 到 GitHub 建新 Release，tag 填 v1.1.0，重新拖入 release/ 里的文件
```

---

## 常见问题

**Q：exe 超过 GitHub 的 100MB 限制了怎么办？**
本项目的 exe 约 11.8 MB，远低于限制，直接传即可。
（如果将来体积变大：Release 附件单个上限是 2 GB，但用 Git LFS 提交到仓库才有 100MB 限制——
我们只在 Release 附件里放，不受影响。）

**Q：用户可以不下 exe，自己从源码跑吗？**
可以，而且建议在 README 里保留这个说法：
```bash
git clone ...
cd tt-sync
python tt-sync.py        # 需要 Python 3.8+ 且带 tkinter，不需要 pip 装任何东西
```
这能显著降低用户对"来路不明的 exe"的顾虑。

**Q：要不要发到 PyPI？**
可以，但没必要——这是给终端用户用的桌面工具，不是库。
真要发的话，改一下 `pyproject.toml` 就行，需要时告诉我。
