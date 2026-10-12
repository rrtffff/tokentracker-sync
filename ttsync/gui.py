"""TokenTracker Sync 图形界面（tkinter）。

背景使用 assets/bg.png（柔和渐变 + 右侧角色），界面为左侧白卡片布局。
运行期只依赖标准库：PNG 由 Tk 8.6 原生读取，不需要 Pillow。

拖放支持：装了 tkinterdnd2 可在窗口内拖放；否则仍可把数据文件直接拖到
exe 图标上（走命令行参数）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import core

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_DND = True
except Exception:  # 未安装则优雅降级
    DND_FILES = None
    TkinterDnD = None
    HAS_DND = False

# 窗口标题带「非官方」，避免被误认为 TokenTracker 官方组件
APP_TITLE = "TokenTracker Sync · 用量数据合并（非官方）"
WIN_W, WIN_H = 980, 780
LEFT_W = 596

# 配色
C_WASH = "#E7EFFA"
C_CARD = "#FFFFFF"
C_BORDER = "#C9D9EC"
C_TITLE = "#2B5C8F"
C_TEXT = "#33475B"
C_MUTED = "#7A8CA3"
C_PRIMARY = "#4A80C4"
C_PRIMARY_D = "#3B6DAE"
C_GHOST = "#DDE9F8"
C_GHOST_D = "#C7DBF3"
C_LOG_BG = "#F8FBFE"
C_OK = "#2E7D53"
C_WARN = "#B4691A"


def _asset(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "assets", name)
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", name)


def pick_font(root) -> str:
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    for f in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
              "Noto Sans CJK SC", "Source Han Sans SC", "SimHei"):
        if f in fams:
            return f
    return "TkDefaultFont"


class App:
    def __init__(self, root, initial_file=None):
        self.root = root
        self.initial_file = initial_file
        self.F = pick_font(root)
        self.FM = "Consolas" if sys.platform == "win32" else "monospace"

        root.title(APP_TITLE)
        root.geometry(f"{WIN_W}x{WIN_H}")
        root.resizable(False, False)
        root.configure(bg=C_WASH)
        try:
            root.iconphoto(True, tk.PhotoImage(file=_asset("icon.png")))
        except Exception:
            pass

        self._setup_style()
        self._build_background()
        self._build_left()

        self.refresh_status()
        self.refresh_backups()

        self.log("欢迎使用 TokenTracker Sync —— TokenTracker 跨机器用量合并 / 备份 / 还原。")
        self.log("非官方小工具，全程本地操作，不联网、不上传。")
        self.log("")
        if core.cloud_sync_enabled():
            self.log("⚠ 检测到云同步处于开启状态。导入会重写 queue.jsonl，")
            self.log("  云上传的字节偏移会失效，建议先去设置里关闭云同步。")
            self.log("")

        if initial_file:
            self.root.after(400, lambda: self.do_import(initial_file))

    # ---------------------------------------------------------------- 样式

    def _setup_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(
            "TT.Treeview",
            background="white",
            fieldbackground="white",
            foreground=C_TEXT,
            rowheight=25,
            borderwidth=0,
            font=(self.F, 9),
        )
        st.configure("TT.Treeview.Heading", background="#EAF2FC", foreground=C_TITLE,
                     relief="flat", font=(self.F, 9))
        st.map("TT.Treeview",
               background=[("selected", "#CFE1F7")],
               foreground=[("selected", "#12395F")])

    # ------------------------------------------------------------ 背景与骨架

    def _build_background(self):
        try:
            self.bgimg = tk.PhotoImage(file=_asset("bg.png"))
            lbl = tk.Label(self.root, image=self.bgimg, bd=0, bg=C_WASH)
            lbl.place(x=0, y=0, relwidth=1, relheight=1)
        except Exception as e:
            lbl = tk.Label(self.root, text=f"（背景图加载失败：{e}）", bg=C_WASH, fg=C_MUTED)
            lbl.place(x=0, y=0, relwidth=1, relheight=1)

        # 右侧角色区域的小注脚
        foot = tk.Frame(self.root, bg=C_WASH)
        foot.place(x=LEFT_W, y=WIN_H - 54, width=WIN_W - LEFT_W, height=44)
        tk.Label(foot, text="TokenTracker Sync", bg=C_WASH, fg=C_TITLE,
                 font=(self.F, 11, "bold")).pack()
        tk.Label(foot, text="非官方 · 本地合并 · 不联网 · 免费", bg=C_WASH, fg=C_MUTED,
                 font=(self.F, 8)).pack()

    def _card(self, parent, title, **pk):
        outer = tk.Frame(parent, bg=C_CARD, highlightbackground=C_BORDER,
                         highlightthickness=1, bd=0)
        titlebar = tk.Frame(outer, bg=C_CARD)
        titlebar.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(titlebar, text=title, bg=C_CARD, fg=C_TITLE,
                 font=(self.F, 10, "bold")).pack(side="left")
        body = tk.Frame(outer, bg=C_CARD)
        body.pack(fill="both", expand=True, padx=14, pady=(6, 12))
        outer.pack(**pk)
        return body

    def _btn(self, parent, text, cmd, kind="ghost", width=None):
        primary = kind == "primary"
        b = tk.Button(
            parent,
            text=text,
            command=cmd,
            bg=C_PRIMARY if primary else C_GHOST,
            fg="white" if primary else C_TITLE,
            activebackground=C_PRIMARY_D if primary else C_GHOST_D,
            activeforeground="white" if primary else C_TITLE,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=14,
            pady=6,
            cursor="hand2",
            font=(self.F, 9),
        )
        if width:
            b.configure(width=width)
        return b

    def _build_left(self):
        col = tk.Frame(self.root, bg=C_WASH, width=LEFT_W, height=WIN_H)
        col.place(x=0, y=0)
        col.pack_propagate(False)

        # ---- 状态 ----
        body = self._card(col, "状态", fill="x", padx=14, pady=(14, 0))
        self.lbl_machine = tk.Label(body, text="本机：—", bg=C_CARD, fg=C_TEXT,
                                    font=(self.F, 9, "bold"), anchor="w")
        self.lbl_machine.pack(fill="x")
        self.lbl_own = tk.Label(body, text="本机自身：—", bg=C_CARD, fg=C_TEXT,
                                font=(self.F, 9), anchor="w")
        self.lbl_own.pack(fill="x")
        self.lbl_merged = tk.Label(body, text="合并后合计：—", bg=C_CARD, fg=C_TEXT,
                                   font=(self.F, 9), anchor="w")
        self.lbl_merged.pack(fill="x")
        self.lbl_cloud = tk.Label(body, text="云同步：—", bg=C_CARD, fg=C_TEXT,
                                  font=(self.F, 9), anchor="w")
        self.lbl_cloud.pack(fill="x")
        self.lbl_imports = tk.Label(body, text="已导入来源：—", bg=C_CARD, fg=C_MUTED,
                                    font=(self.F, 8), anchor="w", justify="left",
                                    wraplength=540)
        self.lbl_imports.pack(fill="x")

        # ---- 按钮行 ----
        bar = tk.Frame(col, bg=C_WASH)
        bar.pack(fill="x", padx=14, pady=(10, 0))
        self._btn(bar, "导出数据", self.do_export, "primary").pack(side="left")
        self._btn(bar, "导入并合并", lambda: self.do_import(None), "primary").pack(side="left", padx=6)
        self._btn(bar, "创建备份", self.do_backup).pack(side="left")
        self._btn(bar, "刷新", self.refresh_all).pack(side="left", padx=6)
        self._btn(bar, "使用说明", self.do_help).pack(side="left")

        # ---- 拖放区 ----
        dz_outer = tk.Frame(col, bg=C_CARD, highlightbackground=C_BORDER,
                            highlightthickness=1, bd=0)
        dz_outer.pack(fill="x", padx=14, pady=(10, 0))
        inner = tk.Frame(dz_outer, bg=C_CARD)
        inner.pack(fill="x", padx=14, pady=10)

        try:
            self.mascot_img = tk.PhotoImage(file=_asset("mascot.png"))
            ml = tk.Label(inner, image=self.mascot_img, bg=C_CARD, bd=0)
            ml.pack(side="left", padx=(0, 14))
        except Exception:
            ml = None

        txt = tk.Frame(inner, bg=C_CARD)
        txt.pack(side="left", fill="both", expand=True)
        tk.Label(txt, text="把数据文件拖到这里即可导入" if HAS_DND
                 else "把数据文件拖到本程序图标上导入",
                 bg=C_CARD, fg=C_TITLE, font=(self.F, 10, "bold"), anchor="w").pack(fill="x")
        tk.Label(txt, text="支持另一台电脑导出的 .json 数据包\n也可以点上面「导入并合并」用文件选择框",
                 bg=C_CARD, fg=C_MUTED, font=(self.F, 8), justify="left", anchor="w").pack(fill="x")
        self.drop = dz_outer
        if HAS_DND:
            targets = [dz_outer, inner, txt]
            if ml is not None:
                targets.append(ml)
            for w in targets:
                try:
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:
                    pass

        # ---- 备份 ----
        body = self._card(col, "备份", fill="x", padx=14, pady=(10, 0))
        cols = ("time", "scope", "note", "size")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=4,
                                 style="TT.Treeview")
        for c, t, w in (("time", "时间", 140), ("scope", "范围", 60),
                        ("note", "备注", 230), ("size", "大小", 80)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x")
        bb = tk.Frame(body, bg=C_CARD)
        bb.pack(fill="x", pady=(8, 0))
        self._btn(bb, "还原选中", self.do_restore_backup).pack(side="left")
        self._btn(bb, "回滚导入", self.do_rollback_import).pack(side="left", padx=6)
        self._btn(bb, "数据目录", lambda: self.open_dir(core.TRACKER_DIR)).pack(side="left")
        self._btn(bb, "备份目录", lambda: self.open_dir(core.BACKUP_DIR)).pack(side="left", padx=6)

        # ---- 日志 ----
        body = self._card(col, "日志", fill="both", expand=True, padx=14, pady=(10, 14))
        wrap = tk.Frame(body, bg=C_LOG_BG, highlightbackground=C_BORDER, highlightthickness=1)
        wrap.pack(fill="both", expand=True)
        sb = tk.Scrollbar(wrap, bd=0, highlightthickness=0)
        sb.pack(side="right", fill="y")
        self.logbox = tk.Text(wrap, height=6, wrap="word", bd=0, relief="flat",
                              bg=C_LOG_BG, fg=C_TEXT, font=(self.F, 9),
                              yscrollcommand=sb.set, padx=8, pady=6)
        self.logbox.pack(side="left", fill="both", expand=True)
        sb.config(command=self.logbox.yview)
        self.logbox.configure(state="disabled")

    # -------------------------------------------------------------- 工具

    def log(self, msg=""):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", str(msg) + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        self.root.update_idletasks()

    def open_dir(self, path):
        try:
            os.makedirs(path, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"无法打开目录：\n{e}")

    def _busy(self, on):
        self.root.config(cursor="watch" if on else "")
        self.root.update_idletasks()

    def _dialog(self, title, w=460, h=190):
        d = tk.Toplevel(self.root)
        d.title(title)
        d.configure(bg=C_CARD)
        d.transient(self.root)
        d.grab_set()
        d.resizable(False, False)
        d.geometry(f"{w}x{h}+{self.root.winfo_rootx() + 180}+{self.root.winfo_rooty() + 180}")
        return d

    # ---------------------------------------------------------- 状态刷新

    def refresh_all(self):
        self.refresh_status()
        self.refresh_backups()

    def refresh_status(self):
        try:
            s = core.status()
        except Exception as e:
            self.lbl_machine.configure(text=f"读取状态失败：{e}", fg=C_WARN)
            return
        m = s["machine"]
        self.lbl_machine.configure(text=f"本机：{m['hostname']}    ID {m['machineId'][:12]}…")
        main = s["queues"]["main"]
        self.lbl_own.configure(
            text=f"本机自身：{core.fmt_num(main['own']['keys'])} 个时间桶 / "
                 f"{core.fmt_num(main['own']['tokens'])} tokens"
        )
        if main["synth_rows"]:
            self.lbl_merged.configure(
                text=f"合并后合计：{core.fmt_num(main['merged']['keys'])} 个时间桶 / "
                     f"{core.fmt_num(main['merged']['tokens'])} tokens"
                     f"   （含 {main['synth_rows']} 行导入数据）",
                fg=C_OK,
            )
        else:
            self.lbl_merged.configure(text="合并后合计：与本机自身相同（尚未导入其他电脑的数据）",
                                      fg=C_TEXT)
        if s["cloud_sync"]:
            self.lbl_cloud.configure(text="云同步：开启（建议关闭后再导入）", fg=C_WARN)
        else:
            self.lbl_cloud.configure(text="云同步：已关闭 ✓", fg=C_OK)
        if s["imports"]:
            parts = [f"{i['hostname']}（{core.fmt_num(i['tokens'])} tokens）" for i in s["imports"]]
            self.lbl_imports.configure(text="已导入来源：" + "、".join(parts))
        else:
            self.lbl_imports.configure(text="已导入来源：无")

    def refresh_backups(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for b in core.list_backup_bundles():
            self.tree.insert(
                "", "end", iid=b["path"],
                values=(
                    (b["createdAt"] or "")[:19].replace("T", " "),
                    b["scope"],
                    (b["note"] or "")[:36],
                    f"{b['size'] / 1024:.0f} KB",
                ),
            )

    # ------------------------------------------------------------ 拖放

    def _on_drop(self, event):
        for p in self.root.tk.splitlist(event.data or ""):
            if str(p).lower().endswith(".json"):
                self.do_import(str(p))
                return
        messagebox.showwarning(APP_TITLE, "请拖入 .json 数据包文件。")

    # ------------------------------------------------------------ 导出

    def do_export(self):
        d = filedialog.askdirectory(title="选择导出到哪个文件夹（可指向 U 盘）")
        if not d:
            return
        self._busy(True)
        self.log("")
        self.log(f"== 导出到 {d} ==")
        try:
            r = core.export_data(d, "json", log=self.log)
        except core.SyncError as e:
            self._busy(False)
            messagebox.showerror(APP_TITLE, str(e))
            return
        except Exception as e:
            self._busy(False)
            messagebox.showerror(APP_TITLE, f"导出失败：\n{e}")
            return
        self._busy(False)
        self.log(f"合计：{core.fmt_num(r['tokens'])} tokens")
        for f in r["files"]:
            self.log(f"已写出：{f}")
        self.log("把这个文件带到另一台电脑，选「导入并合并」即可。")
        messagebox.showinfo(APP_TITLE,
                            f"导出完成。\n\n合计 {core.fmt_num(r['tokens'])} tokens\n\n文件：\n"
                            + "\n".join(r["files"]))

    # ------------------------------------------------------------ 导入

    def do_import(self, path=None):
        if not path:
            path = filedialog.askopenfilename(
                title="选择另一台电脑导出的数据包",
                filetypes=[("TokenTracker Sync 数据包", "*.json"), ("所有文件", "*.*")],
            )
        if not path:
            return
        path = core.normalize_path_input(path)
        if not os.path.exists(path):
            messagebox.showerror(APP_TITLE, f"找不到文件：\n{path}")
            return

        self._busy(True)
        try:
            snap = core.read_snapshot(path)
            planned = core.plan_import(snap)
        except core.SyncError as e:
            self._busy(False)
            self.log("")
            self.log(f"[拒绝导入] {e}")
            messagebox.showerror(APP_TITLE, str(e))
            return
        except Exception as e:
            self._busy(False)
            messagebox.showerror(APP_TITLE, f"无法读取数据包：\n{e}")
            return
        self._busy(False)

        host = planned["info"]["hostname"]
        lines = [f"数据来自：{host}", ""]
        for p in planned["plan"]:
            if p["skip"]:
                lines.append(f"{p['target']['label']}：（数据包中没有这部分）")
                continue
            lines.append(p["target"]["label"])
            lines.append(f"    合并前 {core.fmt_num(p['before'])}  →  合并后 {core.fmt_num(p['after'])}"
                         f"   （+{core.fmt_num(p['after'] - p['before'])}）")

        dropped = snap.get("_dropped_fields") or {}
        if dropped:
            lines.append("")
            lines.append("已忽略数据包里本工具不认识的字段：")
            for qn, cnt in dropped.items():
                lines.append(f"    {qn}：{'、'.join(sorted(cnt))}")

        bad = sum(p.get("malformed", 0) for p in planned["plan"])
        if bad:
            lines.append("")
            lines.append(f"注意：本机队列里有 {bad} 行无法解析，会原样保留、不计入统计。")

        lines += ["", "确认后会先自动备份本机数据，然后写入。", "（同一份数据重复导入不会重复计数）"]

        if not messagebox.askyesno(APP_TITLE, "\n".join(lines)):
            self.log("已取消导入。")
            return

        self._busy(True)
        try:
            applied = core.apply_import(planned, path)
        except Exception as e:
            self._busy(False)
            messagebox.showerror(APP_TITLE, f"导入失败：\n{e}")
            return
        self._busy(False)

        self.log("")
        self.log(f"== 导入 {os.path.basename(path)}（来自 {host}）==")
        for w in applied["written"]:
            self.log("  · " + w)
        self.log("完成。打开 TokenTracker 面板即可看到合并后的数据（无需重启）。")
        self.refresh_status()
        messagebox.showinfo(APP_TITLE, f"导入完成，已合并 {host} 的数据。\n详见日志。")

    # ------------------------------------------------------------ 备份

    def do_backup(self):
        d = self._dialog("创建备份", 470, 200)
        tk.Label(d, text="备注（可留空）：", bg=C_CARD, fg=C_TEXT,
                 font=(self.F, 9)).grid(row=0, column=0, padx=(16, 6), pady=(18, 8), sticky="w")
        note_var = tk.StringVar()
        e = tk.Entry(d, textvariable=note_var, width=34, font=(self.F, 9), relief="flat",
                     highlightbackground=C_BORDER, highlightthickness=1)
        e.grid(row=0, column=1, padx=(0, 16), pady=(18, 8))
        e.focus_set()
        full_var = tk.BooleanVar(value=False)
        tk.Checkbutton(d, text="包含会话数据（体积更大）", variable=full_var, bg=C_CARD,
                       fg=C_TEXT, font=(self.F, 9), activebackground=C_CARD,
                       selectcolor=C_CARD, bd=0).grid(row=1, column=0, columnspan=2,
                                                      padx=16, sticky="w")

        def go():
            note, full = note_var.get(), full_var.get()
            d.destroy()
            self._busy(True)
            try:
                r = core.backup(note, full)
            except core.SyncError as ex:
                self._busy(False)
                messagebox.showerror(APP_TITLE, str(ex))
                return
            except Exception as ex:
                self._busy(False)
                messagebox.showerror(APP_TITLE, f"备份失败：\n{ex}")
                return
            self._busy(False)
            self.log("")
            self.log(f"== 备份（{r['scope']}）==")
            for name, size in r["files"]:
                self.log(f"  · {name}  ({size / 1024:.0f} KB)")
            self.log(f"已写出：{r['file']}")
            self.refresh_backups()
            messagebox.showinfo(APP_TITLE, f"备份完成。\n\n{r['file']}")

        bf = tk.Frame(d, bg=C_CARD)
        bf.grid(row=2, column=0, columnspan=2, pady=(12, 14))
        self._btn(bf, "开始备份", go, "primary").pack(side="left", padx=6)
        self._btn(bf, "取消", d.destroy).pack(side="left", padx=6)
        d.bind("<Return>", lambda _e: go())

    def do_restore_backup(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "请先在「备份」列表里选中一个备份。")
            return
        if not messagebox.askyesno(
            APP_TITLE,
            "这将用该备份【整体覆盖】本机现在的数据。\n\n"
            "备份里包含哪些文件就还原哪些（通常含用量队列与导入账目）。\n"
            "还原前会自动把当前数据另存一份，所以是可逆的。\n\n确认还原？",
        ):
            return
        self._busy(True)
        try:
            r = core.restore_backup(sel[0], log=self.log)
        except Exception as e:
            self._busy(False)
            messagebox.showerror(APP_TITLE, f"还原失败：\n{e}")
            return
        self._busy(False)
        self.log("")
        self.log(f"== 整体还原 {os.path.basename(r['path'])} ==")
        self.log(f"  备份时间：{r['createdAt']}    范围：{r['scope']}")
        self.log(f"  当前数据已另存：{r['safety_dir']}（{len(r['saved'])} 个文件）")
        self.log(f"  已还原 {r['restored']} 个文件。")
        self.refresh_all()
        messagebox.showinfo(APP_TITLE, f"还原完成，共 {r['restored']} 个文件。\n详见日志。")

    def do_rollback_import(self):
        lst = core.list_import_backups()
        if not lst:
            messagebox.showinfo(APP_TITLE, "没有找到导入前的自动备份。")
            return
        d = self._dialog("回滚某次导入", 560, 300)
        tk.Label(d, text="选择要回滚到哪一个导入前备份：", bg=C_CARD, fg=C_TEXT,
                 font=(self.F, 9)).pack(padx=16, pady=(14, 6), anchor="w")
        lb = tk.Listbox(d, width=84, height=min(10, len(lst)), font=(self.FM, 8),
                        relief="flat", highlightbackground=C_BORDER, highlightthickness=1,
                        selectbackground="#CFE1F7", selectforeground="#12395F", bd=0)
        for b in lst:
            lb.insert("end", "  " + b["name"])
        lb.pack(padx=16, pady=6)
        lb.selection_set(0)

        def go():
            s = lb.curselection()
            if not s:
                return
            idx = s[0]
            d.destroy()
            self._busy(True)
            try:
                r = core.restore_import_backup(idx, log=self.log)
            except Exception as e:
                self._busy(False)
                messagebox.showerror(APP_TITLE, f"回滚失败：\n{e}")
                return
            self._busy(False)
            self.log("")
            self.log(f"== 回滚 {r['restored']}  ←  {r['from']} ==")
            self.refresh_status()
            messagebox.showinfo(APP_TITLE, f"已回滚 {r['restored']}。")

        bf = tk.Frame(d, bg=C_CARD)
        bf.pack(pady=(4, 12))
        self._btn(bf, "回滚", go, "primary").pack(side="left", padx=6)
        self._btn(bf, "取消", d.destroy).pack(side="left", padx=6)

    # ------------------------------------------------------------ 说明

    def do_help(self):
        d = self._dialog("使用说明", 660, 520)
        t = tk.Text(d, wrap="word", bd=0, relief="flat", bg=C_LOG_BG, fg=C_TEXT,
                    font=(self.F, 9), padx=14, pady=12)
        t.pack(fill="both", expand=True, padx=12, pady=12)
        t.insert("end", HELP_TEXT)
        t.configure(state="disabled")
        self._btn(d, "知道了", d.destroy, "primary").pack(pady=(0, 12))


HELP_TEXT = """【它解决什么问题】
TokenTracker 官方没有提供本地的用量导出 / 导入，跨设备合并只能用它的付费云同步
（$4.99/月）。本工具用纯本地的方式把多台电脑的数据合并起来。

【关键规则】（这是最容易做错的地方）
TokenTracker 按 数据源 | 模型 | 小时 三个字段去重，同键【只保留最后一条】。
  · 同一台电脑内同键重复 = 该小时的累计更新 → 取最后一条
  · 两台电脑同键         = 各自独立的用量     → 必须相加
这两件事方向相反，搞混就会「丢数据」或「重复计数」。本工具已按规则处理。

【推荐流程】
  1. 两台都先在 TokenTracker 里关闭云同步
  2. A 机：导出数据 → 存到 U 盘
  3. B 机：导入并合并
  4. 想让 A 也看到合计：把 B 导出的文件带回 A 再导入一次
     （来回搬运不会重复计数，导出时不会带上对方的数据）

【备份 vs 合并，千万别搞混】
  · 导出 / 导入 = 两台电脑合并（相加）
  · 备份 / 还原 = 本机整体退回（覆盖）
不要把 A 的完整备份还原到 B，那会把 B 自己的数据整个覆盖掉。

【数据安全】
全程本地读写，不联网、不上传。每次导入前都会自动备份原文件。
出问题时：用「回滚导入」退回某次导入前，或用「备份 / 还原选中」整体退回。

【已知限制】
· 最近 1 小时的数据在来源机器重算该小时桶后可能短暂偏小，重新导入一次即可修正。
· 网页版的跨设备视图仍由官方云提供，本工具只影响本机桌面版面板。
"""


def run(initial_file=None):
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    App(root, initial_file)
    root.mainloop()


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
