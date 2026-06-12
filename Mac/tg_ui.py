import tkinter as tk
from tkinter import ttk

BG="#0f1923";BG2="#1a2635";BG3="#1f2d3d";ACCENT="#4fc3f7";SUCCESS="#69f0ae"
WARNING="#ffd740";DANGER="#ff5252";TEXT="#e8eaf0";MUTED="#546e7a"
FONT=("Segoe UI",10,"bold");FONT_SM=("Segoe UI",9,"bold");FONT_B=("Segoe UI",10,"bold")
FONT_H=("Segoe UI",11,"bold");FONT_TREE=("Segoe UI",10,"bold");MONO=("Consolas",9)
APP_NAME = "Telegram Group Backup & Download"
APP_VERSION = "v1.1"
APP_TITLE = f"{APP_NAME} {APP_VERSION}"
def make_scrollable(parent):
    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0, bd=0)
    sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sb.set)
    sb.pack(side=tk.RIGHT, fill=tk.Y); canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    inner = tk.Frame(canvas, bg=BG)
    wid = canvas.create_window((0,0), window=inner, anchor="nw")
    def _resize(e): canvas.itemconfig(wid, width=e.width)
    def _change(e): canvas.configure(scrollregion=canvas.bbox("all"))
    def _wheel(e): canvas.yview_scroll(int(-1*(e.delta/120)),"units")
    def _bind_all(w):
        if isinstance(w, ttk.Treeview):
            return
        w.bind("<MouseWheel>", _wheel, add="+")
        for c in w.winfo_children(): _bind_all(c)
    canvas.bind("<Configure>", _resize); inner.bind("<Configure>", _change)
    canvas.bind("<MouseWheel>", _wheel); inner.bind("<MouseWheel>", _wheel)
    inner.bind("<Map>", lambda e: _bind_all(inner), add="+")
    return canvas, inner
def card(parent, title=None, pady=16):
    f = tk.Frame(parent, bg=BG2, padx=20, pady=pady)
    if title:
        tk.Label(f, text=title, bg=BG2, fg=ACCENT, font=FONT_H).pack(anchor="w")
        tk.Frame(f, bg=ACCENT, height=1).pack(fill=tk.X, pady=(4,10))
    return f
def field_row(parent, label, var, width=24, hint=None):
    row = tk.Frame(parent, bg=BG2); row.pack(fill=tk.X, pady=3)
    tk.Label(row, text=label, bg=BG2, fg=MUTED, font=FONT_SM, width=14, anchor="w").pack(side=tk.LEFT)
    ttk.Entry(row, textvariable=var, width=width).pack(side=tk.LEFT, padx=(0,8))
    if hint: tk.Label(row, text=hint, bg=BG2, fg=MUTED, font=("Segoe UI",8)).pack(side=tk.LEFT)
def combo_row(parent, label, var, width=42):
    row = tk.Frame(parent, bg=BG2); row.pack(fill=tk.X, pady=3)
    tk.Label(row, text=label, bg=BG2, fg=MUTED, font=FONT_SM, width=14, anchor="w").pack(side=tk.LEFT)
    cb = ttk.Combobox(row, textvariable=var, width=width, state="readonly"); cb.pack(side=tk.LEFT)
    return cb
def check_row(parent, label, var):
    row = tk.Frame(parent, bg=BG2); row.pack(anchor="w", pady=2)
    tk.Checkbutton(row, text=label, variable=var, bg=BG2, fg=TEXT, activebackground=BG2,
                   activeforeground=TEXT, selectcolor=BG3, font=FONT).pack(side=tk.LEFT)
