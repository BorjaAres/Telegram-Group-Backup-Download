import os
import re
import threading
import queue
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog, filedialog
import webbrowser

from tg_shared import (
    fmt_bytes as _fmt_bytes,
    is_generic_topic_title as _is_generic_topic_title,
    load_config,
    save_config,
)
from tg_telegram import (
    create_group,
    create_topic_in_group,
    do_send_code,
    do_signin,
    list_groups,
    list_topics,
    resolve_route_topic_names,
)
from tg_workers import (
    calculate_download_topics,
    delete_clean_duplicates,
    run_backup_auto,
    run_backup_manual,
    run_backup_routes,
    run_download_topics,
    scan_clean_duplicates,
)
from tg_ui import (
    ACCENT,
    BG,
    BG2,
    BG3,
    DANGER,
    FONT,
    FONT_B,
    FONT_H,
    FONT_SM,
    FONT_TREE,
    MONO,
    MUTED,
    SUCCESS,
    TEXT,
    WARNING,
    APP_NAME,
    APP_TITLE,
    APP_VERSION,
    card,
    check_row,
    combo_row,
    field_row,
    make_scrollable,
)
# -- App ------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE); self.geometry("980x760")
        self.minsize(820,640); self.configure(bg=BG)
        self.config_data = load_config()
        self.groups = []; self.log_queue = queue.Queue(); self.result_queue = queue.Queue()
        self.stop_event = threading.Event(); self.backup_running = False
        self.active_task = None
        self.current_project_name = None
        self.activity_queue = list(self.config_data.get("activity_queue", []))
        self.queue_running = False; self.skip_current_for_queue = False
        self._m_src_topics = []; self._m_dst_topics = []
        self._manual_mappings = []
        self._b_src_topics = []; self._b_dst_topics = []; self._b_routes = []
        self._b_src_is_forum = False; self._b_dest_is_forum = False
        self._d_topics = []; self._d_topic_view = []
        self._clean_topics = []; self._clean_dupes = []
        self._b_loaded_project_index = None
        self._styles(); self._build()
        if self.config_data.get("_migration_notice"):
            self.after(800, lambda: messagebox.showinfo(
                "Projects and login found",
                self.config_data.get("_migration_notice", "")
            ))
        self.after(300, self._poll)
    def _styles(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=BG2, foreground=MUTED, padding=[18,10], font=FONT, borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected",BG3)], foreground=[("selected",ACCENT)])
        s.configure("TFrame", background=BG); s.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
        s.configure("TEntry", fieldbackground=BG3, foreground=TEXT, insertcolor=ACCENT, font=FONT, padding=6)
        s.configure("TCombobox", fieldbackground=BG3, foreground=TEXT, selectbackground=BG3, selectforeground=TEXT, font=FONT, padding=5)
        s.map("TCombobox", fieldbackground=[("readonly",BG3)], foreground=[("readonly",TEXT)])
        s.configure("Treeview", background=BG3, fieldbackground=BG3, foreground=TEXT,
                    font=FONT_TREE, rowheight=26, borderwidth=0)
        s.configure("Treeview.Heading", background=BG2, foreground=TEXT,
                    font=FONT_B, relief="flat")
        s.map("Treeview", background=[("selected",ACCENT)], foreground=[("selected",BG)])
        s.configure("P.TButton", background=ACCENT, foreground=BG, font=FONT_B, padding=[14,8], borderwidth=0)
        s.configure("TButton", background=BG3, foreground=TEXT, font=FONT, padding=[12,7], borderwidth=0)
        s.configure("D.TButton", background=DANGER, foreground="white", font=FONT_B, padding=[12,7], borderwidth=0)
        s.configure("G.TButton", background=BG2, foreground=MUTED, font=FONT_SM, padding=[10,5], borderwidth=0)
        s.map("P.TButton", background=[("active","#29b6f6"),("disabled",BG3)])
        s.map("TButton",   background=[("active",BG2),("disabled",BG2)], foreground=[("disabled",MUTED)])
        s.map("D.TButton", background=[("active","#ff1744"),("disabled",BG3)])
        s.map("G.TButton", background=[("active",BG3),("disabled",BG2)], foreground=[("disabled",MUTED)])
    def _build(self):
        hdr = tk.Frame(self, bg=BG2); hdr.pack(fill=tk.X)
        ih = tk.Frame(hdr, bg=BG2, padx=22, pady=14); ih.pack(fill=tk.X)
        tk.Label(ih, text=APP_TITLE, bg=BG2, fg=TEXT,
                 font=("Segoe UI",16,"bold")).pack(side=tk.LEFT)
        tk.Label(ih, text="By Poleroso · Projects, downloads, and queued runs", bg=BG2, fg=MUTED,
                 font=FONT_SM).pack(side=tk.LEFT, padx=(16,0))
        tk.Frame(self, bg=ACCENT, height=2).pack(fill=tk.X)
        self.nb = ttk.Notebook(self); self.nb.pack(fill=tk.BOTH, expand=True)
        t1=ttk.Frame(self.nb); t2=ttk.Frame(self.nb); t3=ttk.Frame(self.nb); t4=ttk.Frame(self.nb); t5=ttk.Frame(self.nb)
        self.nb.add(t1, text="  1  Setup  ")
        self.nb.add(t2, text="  2  Copy Projects  ")
        self.nb.add(t3, text="  3  Download Projects  ")
        self.nb.add(t4, text="  4  Clean  ")
        self.nb.add(t5, text="  5  Run  ")
        self._setup(t1); self._project_builder(t2); self._download_tab(t3); self._clean_tab(t4); self._run_tab(t5)
    # -- SETUP ------------------------------------------------------------------
    def _setup(self, parent):
        self.setup_canvas, inner = make_scrollable(parent)
        c1 = card(inner, "How to get your API credentials"); c1.pack(fill=tk.X, padx=20, pady=(20,8))
        for n,t in [("1","Go to  https://my.telegram.org"),("2","Log in with your phone number"),
                    ("3","Click 'API development tools'"),("4","Create an app — any name"),
                    ("5","Copy your api_id and api_hash below")]:
            r=tk.Frame(c1,bg=BG2); r.pack(fill=tk.X,pady=2)
            tk.Label(r,text=f" {n} ",bg=ACCENT,fg=BG,font=("Segoe UI",8,"bold"),width=2).pack(side=tk.LEFT,padx=(0,8))
            tk.Label(r,text=t,bg=BG2,fg=TEXT,font=FONT).pack(side=tk.LEFT)
        lnk=tk.Label(c1,text="Open my.telegram.org",bg=BG2,fg="#29b6f6",font=("Segoe UI",10,"underline"),cursor="hand2")
        lnk.pack(anchor="w",pady=(10,0)); lnk.bind("<Button-1>",lambda e:webbrowser.open("https://my.telegram.org"))
        c2=card(inner,"Your credentials"); c2.pack(fill=tk.X,padx=20,pady=8)
        self.api_id_var=tk.StringVar(value=self.config_data.get("api_id",""))
        self.api_hash_var=tk.StringVar(value=self.config_data.get("api_hash",""))
        self.phone_var=tk.StringVar(value=self.config_data.get("phone",""))
        field_row(c2,"API ID",self.api_id_var,18); field_row(c2,"API Hash",self.api_hash_var,36)
        field_row(c2,"Phone",self.phone_var,22,hint="e.g. +34612345678")
        br=tk.Frame(c2,bg=BG2); br.pack(fill=tk.X,pady=(12,0))
        ttk.Button(br,text="Save & Connect",style="P.TButton",command=self._connect).pack(side=tk.LEFT)
        self.conn_lbl=tk.Label(br,text="",bg=BG2,fg=SUCCESS,font=FONT_B); self.conn_lbl.pack(side=tk.LEFT,padx=14)
        self.code_card=card(inner,"Enter your Telegram code")
        tk.Label(self.code_card,text="Enter the code Telegram sent to your app:",bg=BG2,fg=TEXT,font=FONT).pack(anchor="w",pady=(0,8))
        cr=tk.Frame(self.code_card,bg=BG2); cr.pack(anchor="w")
        self.code_var=tk.StringVar()
        self.code_entry=ttk.Entry(cr,textvariable=self.code_var,width=12,font=("Segoe UI",16))
        self.code_entry.pack(side=tk.LEFT,padx=(0,12))
        ttk.Button(cr,text="Confirm",style="P.TButton",command=self._confirm_code).pack(side=tk.LEFT)
        self.setup_log_card=card(inner,"Activity log"); self.setup_log_card.pack(fill=tk.X,padx=20,pady=(8,20))
        self.setup_log=scrolledtext.ScrolledText(self.setup_log_card,height=7,bg=BG,fg=TEXT,font=MONO,insertbackground=ACCENT,bd=0,relief="flat",padx=8,pady=8)
        self.setup_log.pack(fill=tk.X)
        self.setup_log.tag_config("ok",foreground=SUCCESS); self.setup_log.tag_config("err",foreground=DANGER)
    def _connect(self):
        if self._telegram_busy("connect or log in"):
            return
        ai=self.api_id_var.get().strip(); ah=self.api_hash_var.get().strip(); ph=self.phone_var.get().strip()
        if not ai or not ah or not ph: messagebox.showerror("Missing","Fill in API ID, Hash and Phone."); return
        self.code_var.set("")
        self.config_data.update({"api_id":ai,"api_hash":ah,"phone":ph}); save_config(self.config_data)
        self.conn_lbl.config(text="Connecting...",fg=WARNING); self._log_s("Connecting...")
        threading.Thread(target=lambda:do_send_code(int(ai),ah,ph,self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_login)
    def _chk_login(self):
        try:
            r=self.result_queue.get_nowait()
            if r is True: self.conn_lbl.config(text="Connected",fg=SUCCESS); self._load_groups()
            elif r=="need_code": self.conn_lbl.config(text="Check Telegram for code",fg=WARNING); self._show_code()
            else: self.conn_lbl.config(text="Failed",fg=DANGER)
        except queue.Empty: self.after(500,self._chk_login)
    def _show_code(self):
        if not self.code_card.winfo_manager():
            self.code_card.pack(fill=tk.X, padx=20, pady=8, before=self.setup_log_card)
        self.conn_lbl.config(text="Code sent - enter it below", fg=WARNING)
        self._log_s("Code sent. Enter it in the code box above the Activity log.")
        self.code_entry.focus()
        self.code_entry.selection_range(0, tk.END)
        try:
            self.setup_canvas.yview_moveto(0.35)
        except Exception:
            pass
    def _confirm_code(self):
        if self._telegram_busy("confirm the login code"):
            return
        threading.Thread(target=lambda:do_signin(int(self.api_id_var.get()),self.api_hash_var.get(),
            self.phone_var.get(),self.code_var.get(),self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_signin)
    def _chk_signin(self):
        try:
            r=self.result_queue.get_nowait()
            if r is True:
                self.conn_lbl.config(text="Logged in",fg=SUCCESS); self.code_card.pack_forget(); self._load_groups()
            else:
                self.conn_lbl.config(text="Wrong code",fg=DANGER)
                self._log_s("Sign in failed. Check the code and try Confirm again.", "err")
        except queue.Empty: self.after(500,self._chk_signin)
    def _telegram_busy(self, action="use Telegram"):
        if not self.backup_running:
            return False
        msg = f"A task is running. Wait until it finishes before you {action}."
        messagebox.showinfo("Task running", msg)
        self._log_s(msg)
        if hasattr(self, "run_log"):
            self._log_r(msg, "y")
        return True
    def _load_groups(self):
        if self._telegram_busy("refresh groups"):
            return
        self._log_s("Loading groups...")
        threading.Thread(target=lambda:list_groups(int(self.api_id_var.get()),self.api_hash_var.get(),
            self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_groups)
    def _chk_groups(self):
        try:
            r=self.result_queue.get_nowait()
            if isinstance(r,list) and r:
                self.groups=r; self._refresh_combos()
            elif r is None: self._log_s("Not logged in yet.")
            else: self.after(500,self._chk_groups)
        except queue.Empty: self.after(500,self._chk_groups)
    def _log_s(self,msg,tag=None):
        self.setup_log.insert(tk.END,msg+"\n",tag or ""); self.setup_log.see(tk.END)
    # -- AUTO BACKUP ------------------------------------------------------------
    def _group_from_selection(self, text):
        return next((g for g in self.groups if f"(id: {g['id']})" in text), None) or next((g for g in self.groups if g["name"] in text), None)
    # -- PROJECT BUILDER -----------------------------------------------------
    # -- PROJECT BUILDER --------------------------------------------------------
    def _project_builder(self, parent):
        """Destination-first project builder with a simple grouped route list."""
        self._b_routes = []
        self._b_dest_topics = []
        self._b_src_topics = []
        self._b_dest_group_id = None
        self._b_dest_group_name = ""
        self._b_editing_project_name = ""
        # Main scrollable area
        _, inner = make_scrollable(parent)
        # -- STEP 1: Project name + open existing --------------------------
        intro = card(inner, "Project Builder"); intro.pack(fill=tk.X, padx=20, pady=(16,6))
        tk.Label(intro, text="Project -> Destination group -> Add sources -> Check routes -> Save.",
                 bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w")
        s1 = card(inner, "Project"); s1.pack(fill=tk.X, padx=20, pady=6)
        r0 = tk.Frame(s1, bg=BG2); r0.pack(fill=tk.X, pady=3)
        tk.Label(r0, text="Project name:", bg=BG2, fg=TEXT, font=FONT, width=16, anchor="w").pack(side=tk.LEFT)
        self.b_name_var = tk.StringVar()
        ttk.Entry(r0, textvariable=self.b_name_var, width=30).pack(side=tk.LEFT, padx=(0,16))
        r0b = tk.Frame(s1, bg=BG2); r0b.pack(fill=tk.X, pady=3)
        tk.Label(r0b, text="Edit existing:", bg=BG2, fg=MUTED, font=FONT_SM, width=16, anchor="w").pack(side=tk.LEFT)
        self.b_existing_var = tk.StringVar()
        self.b_existing_combo = ttk.Combobox(r0b, textvariable=self.b_existing_var, width=28, state="readonly")
        self.b_existing_combo.pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r0b, text="Load Project", command=self._builder_load_project).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r0b, text="Delete Project", style="G.TButton",
                   command=self._builder_delete_project).pack(side=tk.LEFT)
        self._builder_refresh_project_combo()
        # -- STEP 2: Destination group --------------------------------------
        s2 = card(inner, "Destination group"); s2.pack(fill=tk.X, padx=20, pady=6)
        tk.Label(s2, text="Choose where messages will be copied to.",
                 bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(0,8))
        dest_choices = tk.Frame(s2, bg=BG2); dest_choices.pack(fill=tk.X, pady=3)
        tk.Label(dest_choices, text="Use existing group:", bg=BG2, fg=TEXT, font=FONT_B, anchor="w").pack(side=tk.LEFT, padx=(0,8))
        self.b_dest_var = tk.StringVar()
        self.b_dest_combo = ttk.Combobox(dest_choices, textvariable=self.b_dest_var, width=26)
        self.b_dest_combo.pack(side=tk.LEFT, padx=(0,8))
        self.b_dest_combo.bind("<<ComboboxSelected>>", lambda e: self._builder_load_dest_topics())
        ttk.Button(dest_choices, text="Use This Group", style="P.TButton",
                   command=self._builder_load_dest_topics).pack(side=tk.LEFT, padx=(0,14))
        tk.Label(dest_choices, text="or create new:", bg=BG2, fg=TEXT, font=FONT_B, anchor="w").pack(side=tk.LEFT, padx=(0,8))
        self.new_group_name = tk.StringVar()
        ttk.Entry(dest_choices, textvariable=self.new_group_name, width=22).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(dest_choices, text="Create This Group",
                   command=self._create_group).pack(side=tk.LEFT)
        self.b_dest_status = tk.Label(s2, text="", bg=BG2, fg=MUTED, font=FONT_SM)
        self.b_dest_status.pack(anchor="w", pady=(4,0))
        # Optional: create a new topic in destination
        r2b = tk.Frame(s2, bg=BG2); r2b.pack(fill=tk.X, pady=(14,0))
        tk.Label(r2b, text="Create topic in destination group:", bg=BG2, fg=MUTED, font=FONT_SM, width=30, anchor="w").pack(side=tk.LEFT)
        self.b_new_topic_var = tk.StringVar()
        ttk.Entry(r2b, textvariable=self.b_new_topic_var, width=24).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r2b, text="Create Topic", command=self._builder_add_bucket).pack(side=tk.LEFT)
        # -- STEP 3: Source topics ------------------------------------------
        s3 = card(inner, "Source topics to copy"); s3.pack(fill=tk.X, padx=20, pady=6)
        tk.Label(s3, text="Choose a source group, load its topics, then add one topic or all topics to the route list.",
                 bg=BG2, fg=MUTED, font=FONT_SM, justify="left").pack(anchor="w", pady=(0,8))
        r3 = tk.Frame(s3, bg=BG2); r3.pack(fill=tk.X, pady=3)
        tk.Label(r3, text="Source group:", bg=BG2, fg=TEXT, font=FONT, width=18, anchor="w").pack(side=tk.LEFT)
        self.b_src_var = tk.StringVar()
        self.b_src_combo = ttk.Combobox(r3, textvariable=self.b_src_var, width=34)
        self.b_src_combo.pack(side=tk.LEFT, padx=(0,10))
        self.b_src_combo.bind("<<ComboboxSelected>>", lambda e: self._builder_load_source_topics())
        ttk.Button(r3, text="Reload Source Topics", command=self._builder_load_source_topics).pack(side=tk.LEFT)
        self.b_src_status = tk.Label(s3, text="", bg=BG2, fg=MUTED, font=FONT_SM)
        self.b_src_status.pack(anchor="w", pady=(2,4))
        r4 = tk.Frame(s3, bg=BG2); r4.pack(fill=tk.X, pady=3)
        tk.Label(r4, text="Source topic:", bg=BG2, fg=TEXT, font=FONT, width=18, anchor="w").pack(side=tk.LEFT)
        self.b_src_topic_var = tk.StringVar()
        self.b_src_topic_combo = ttk.Combobox(r4, textvariable=self.b_src_topic_var, width=34)
        self.b_src_topic_combo.pack(side=tk.LEFT)
        r4b = tk.Frame(s3, bg=BG2); r4b.pack(fill=tk.X, pady=4)
        tk.Label(r4b, text="Group copy mode:", bg=BG2, fg=TEXT, font=FONT_B).pack(side=tk.LEFT, padx=(0,8))
        self.b_whole_group_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r4b,
                       text="Create/reuse topics when running",
                       variable=self.b_whole_group_var,
                       command=self._builder_whole_mode_changed,
                       bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT,
                       selectcolor=BG3, font=FONT_SM).pack(side=tk.LEFT, padx=(0,18))
        self.b_flatten_group_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r4b,
                       text="Put whole source group into one destination topic",
                       variable=self.b_flatten_group_var,
                       command=self._builder_flatten_mode_changed,
                       bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT,
                       selectcolor=BG3, font=FONT_SM).pack(side=tk.LEFT)
        tk.Label(s3,
                 text="  First option keeps topics separate. Second option asks you to choose one destination topic for the whole source group.",
                 bg=BG2, fg=MUTED, font=("Segoe UI",8)).pack(anchor="w", padx=(0,0), pady=(0,4))
        btn_row = tk.Frame(s3, bg=BG2); btn_row.pack(fill=tk.X, pady=(8,0))
        self.b_add_selected_btn = ttk.Button(btn_row, text="Add Selected Topic to Routes", command=lambda: self._builder_add_routes(False))
        self.b_add_selected_btn.pack(side=tk.LEFT, padx=(0,8))
        self.b_add_all_btn = ttk.Button(btn_row, text="Add All Topics to Routes", style="P.TButton",
                   command=lambda: self._builder_add_routes(True))
        self.b_add_all_btn.pack(side=tk.LEFT)
        self._builder_update_add_buttons()
        # -- STEP 3b: Copy filters -----------------------------------------
        s3b = card(inner, "What to copy"); s3b.pack(fill=tk.X, padx=20, pady=6)
        tk.Label(s3b, text="These filters apply to ALL routes in this project.",
                 bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(0,8))
        frow = tk.Frame(s3b, bg=BG2); frow.pack(fill=tk.X, pady=2)
        tk.Label(frow, text="Copy:", bg=BG2, fg=TEXT, font=FONT_SM).pack(side=tk.LEFT, padx=(0,10))
        self.b_f_msg = tk.BooleanVar(value=True)
        self.b_f_img = tk.BooleanVar(value=True)
        self.b_f_fil = tk.BooleanVar(value=True)
        for var, lbl in [(self.b_f_msg, "Text messages"), (self.b_f_img, "Images"), (self.b_f_fil, "Files")]:
            tk.Checkbutton(frow, text=lbl, variable=var,
                           bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT,
                           selectcolor=BG3, font=FONT_SM).pack(side=tk.LEFT, padx=(0,14))
        frow2 = tk.Frame(s3b, bg=BG2); frow2.pack(fill=tk.X, pady=2)
        self.b_f_skip_dup = tk.BooleanVar(value=True)
        tk.Checkbutton(frow2, text="Skip duplicate files (same name + size)",
                       variable=self.b_f_skip_dup,
                       bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT,
                       selectcolor=BG3, font=FONT_SM).pack(anchor="w")
        # -- STEP 4: Review routes ------------------------------------------
        s4 = card(inner, "Routes by destination"); s4.pack(fill=tk.X, padx=20, pady=6)
        tk.Label(s4,
                 text="Each source topic is copied into one destination topic. Suggestions are shown, but you choose what to use.",
                 bg=BG2, fg=MUTED, font=FONT_SM, justify="left").pack(anchor="w", pady=(0,10))
        abar = tk.Frame(s4, bg=BG2); abar.pack(fill=tk.X, pady=(0,8))
        ttk.Button(abar, text="Accept Suggestions", style="P.TButton",
                   command=self._builder_accept_suggestions).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(abar, text="Use Existing",
                   command=self._builder_use_existing_selected).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(abar, text="Create",
                   command=self._builder_create_for_selected).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(abar, text="Clear Destination",
                   command=self._builder_clear_destination_selected).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(abar, text="Delete Selected",
                   style="G.TButton", command=self._builder_remove_selected).pack(side=tk.LEFT)
        self.b_status_lbl = tk.Label(s4, text="No routes yet — add sources above.",
                                     bg=BG2, fg=MUTED, font=FONT_SM)
        self.b_status_lbl.pack(anchor="w", pady=(0,8))
        tree_frame = tk.Frame(s4, bg=BG2); tree_frame.pack(fill=tk.X)
        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        self.b_routes_tree = ttk.Treeview(tree_frame, columns=("source", "dest", "status"),
                                          show="tree headings", height=14, selectmode="extended",
                                          yscrollcommand=tree_scroll.set)
        tree_scroll.config(command=self.b_routes_tree.yview)
        self.b_routes_tree.heading("#0", text="Destination")
        self.b_routes_tree.heading("source", text="Source")
        self.b_routes_tree.heading("dest", text="Topic")
        self.b_routes_tree.heading("status", text="Status")
        self.b_routes_tree.column("#0", width=210, anchor="w")
        self.b_routes_tree.column("source", width=250, anchor="w")
        self.b_routes_tree.column("dest", width=180, anchor="w")
        self.b_routes_tree.column("status", width=130, anchor="w")
        self.b_routes_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.b_routes_tree.tag_configure("needs_parent", foreground=DANGER, font=FONT_TREE)
        self.b_routes_tree.tag_configure("suggest_parent", foreground=WARNING, font=FONT_TREE)
        self.b_routes_tree.tag_configure("review_parent", foreground=WARNING, font=FONT_TREE)
        self.b_routes_tree.tag_configure("ready_parent", foreground=TEXT, font=FONT_TREE)
        self.b_routes_tree.tag_configure("needs", foreground=DANGER, font=FONT_TREE)
        self.b_routes_tree.tag_configure("suggestion", foreground=WARNING, font=FONT_TREE)
        self.b_routes_tree.tag_configure("review", foreground=WARNING, font=FONT_TREE)
        self.b_routes_tree.tag_configure("ready", foreground=TEXT, font=FONT_TREE)
        self.b_routes_tree.bind("<Double-1>", lambda e: self._builder_use_existing_selected())
        self.b_routes_tree.bind("<Delete>", lambda e: self._builder_remove_selected())
        self.b_routes_tree.bind("<MouseWheel>", self._builder_tree_mousewheel)
        # -- STEP 5: Save ---------------------------------------------------
        s5 = card(inner); s5.pack(fill=tk.X, padx=20, pady=(6,20))
        foot = tk.Frame(s5, bg=BG2); foot.pack(fill=tk.X)
        ttk.Button(foot, text="Save Project", style="P.TButton",
                   command=self._builder_save_project).pack(side=tk.LEFT, padx=(0,12))
        ttk.Button(foot, text="Clear Routes", style="G.TButton",
                   command=self._builder_clear).pack(side=tk.LEFT)
    # -- Builder helpers --------------------------------------------------------
    def _builder_refresh_project_combo(self):
        projects = list(self.config_data.get("projects", []))
        projects.sort(key=lambda p: ((p.get("dest_name") or "").lower(), p.get("name", "").lower()))
        self._b_project_display_to_name = {}
        names = []
        for p in projects:
            dest = p.get("dest_name") or "Archive"
            mode = "Builder" if p.get("mode") == "routes" else "Download" if p.get("mode") == "download" else "Classic"
            display = f"{dest} / {p['name']}  ({mode})"
            self._b_project_display_to_name[display] = p["name"]
            names.append(display)
        if hasattr(self, 'b_existing_combo'):
            self.b_existing_combo["values"] = names
    def _topic_display(self, topic):
        return f"{topic['title']}  (id:{topic['id']})"
    def _builder_parse_topic(self, text, topics):
        return next((t for t in topics if t["title"] in text), None)
    def _builder_load_dest_topics(self):
        if self._telegram_busy("load topics"):
            return
        sel = self.b_dest_var.get()
        if not sel:
            messagebox.showerror("", "Select a destination group first."); return
        grp = next((g for g in self.groups if g["name"] in sel), None)
        if not grp: return
        self._b_dest_group_id = grp["id"]
        self._b_dest_group_name = grp["name"]
        if hasattr(self, 'b_dest_status'):
            self.b_dest_status.config(text="Loading topics...", fg=WARNING)
        self._log_s(f"Loading destination topics from '{grp['name']}'...")
        threading.Thread(target=lambda: list_topics(
            int(self.api_id_var.get()), self.api_hash_var.get(),
            grp["id"], self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, lambda: self._builder_chk_topics("dest"))
    def _builder_load_source_topics(self):
        if self._telegram_busy("load topics"):
            return
        sel = self.b_src_var.get()
        if not sel:
            messagebox.showerror("", "Select a source group first."); return
        grp = next((g for g in self.groups if g["name"] in sel), None)
        if not grp: return
        if hasattr(self, 'b_src_status'):
            self.b_src_status.config(text="Loading topics...", fg=WARNING)
        self._log_s(f"Loading source topics from '{grp['name']}'...")
        threading.Thread(target=lambda: list_topics(
            int(self.api_id_var.get()), self.api_hash_var.get(),
            grp["id"], self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, lambda: self._builder_chk_topics("src"))
    def _builder_chk_topics(self, which):
        try:
            r = self.result_queue.get_nowait()
            if isinstance(r, list):
                if which == "dest":
                    self._b_dest_topics = r
                    self._b_dest_is_forum = not (len(r) == 1 and int(r[0].get("id", 1)) == 1 and "no topics" in r[0].get("title", "").lower())
                    self._builder_merge_known_dest_topics()
                    self._builder_apply_topic_names("dest", r)
                    if hasattr(self, 'b_dest_status'):
                        self.b_dest_status.config(
                            text=f"{len(r)} destination topics loaded.", fg=SUCCESS)
                    self._builder_redraw()
                else:
                    self._b_src_topics = r
                    self._b_src_is_forum = not (len(r) == 1 and int(r[0].get("id", 1)) == 1 and "no topics" in r[0].get("title", "").lower())
                    self._builder_apply_topic_names("src", r)
                    topic_values = [self._topic_display(t) for t in sorted(r, key=lambda t: t.get("title", "").lower())]
                    self._builder_make_searchable_combo(self.b_src_topic_combo, topic_values)
                    if r[0]["id"] == 1 and len(r) == 1:
                        self.b_whole_group_var.set(False)
                        self.b_flatten_group_var.set(False)
                        if hasattr(self, 'b_src_status'):
                            self.b_src_status.config(text="No topics found. Add General as a single route if needed.", fg=WARNING)
                    else:
                        if hasattr(self, 'b_src_status'):
                            self.b_src_status.config(
                                text=f"{len(r)} topics loaded.", fg=SUCCESS)
        except queue.Empty:
            self.after(500, lambda: self._builder_chk_topics(which))
    def _builder_apply_topic_names(self, which, topics):
        by_id = {int(t["id"]): t["title"] for t in topics}
        changed = 0
        for route in self._b_routes:
            if which == "src" and int(route.get("src_topic_id", 1)) in by_id:
                new_title = by_id[int(route.get("src_topic_id", 1))]
                if route.get("src_topic_title") != new_title:
                    route["src_topic_title"] = new_title; changed += 1
            elif which == "dest" and route.get("dest_topic_id") and int(route.get("dest_topic_id")) in by_id:
                new_title = by_id[int(route.get("dest_topic_id"))]
                if route.get("dest_topic_title") != new_title:
                    route["dest_topic_title"] = new_title; changed += 1
        if changed:
            self._builder_redraw()
    def _builder_merge_known_dest_topics(self):
        if not self._b_dest_group_id:
            return
        by_id = {int(t["id"]): dict(t) for t in self._b_dest_topics if t.get("id")}
        for project in self.config_data.get("projects", []):
            for route in project.get("routes", []):
                if int(route.get("dest_group_id", 0) or 0) != int(self._b_dest_group_id):
                    continue
                tid = route.get("dest_topic_id")
                if not tid:
                    continue
                tid = int(tid)
                title = route.get("dest_topic_title") or f"Topic {tid}"
                if _is_generic_topic_title(title, tid):
                    src_title = route.get("src_topic_title")
                    if src_title and not _is_generic_topic_title(src_title, route.get("src_topic_id", 1)):
                        title = src_title
                current = by_id.get(tid)
                if not current or _is_generic_topic_title(current.get("title"), tid):
                    by_id[tid] = {"id": tid, "title": title}
        for route in self._b_routes:
            tid = route.get("dest_topic_id")
            if not tid:
                continue
            tid = int(tid)
            title = route.get("dest_topic_title") or f"Topic {tid}"
            if _is_generic_topic_title(title, tid):
                src_title = route.get("src_topic_title")
                if src_title and not _is_generic_topic_title(src_title, route.get("src_topic_id", 1)):
                    title = src_title
            current = by_id.get(tid)
            if not current or _is_generic_topic_title(current.get("title"), tid):
                by_id[tid] = {"id": tid, "title": title}
        self._b_dest_topics = sorted(by_id.values(), key=lambda t: (t.get("title", "").lower(), int(t.get("id", 0))))
    def _builder_resolve_generic_names(self):
        if not self._b_routes:
            return
        needs_names = any(
            _is_generic_topic_title(r.get("src_topic_title"), r.get("src_topic_id", 1)) or
            (r.get("dest_topic_id") and _is_generic_topic_title(r.get("dest_topic_title"), r.get("dest_topic_id")))
            for r in self._b_routes
        )
        if not needs_names or self.backup_running:
            return
        self._log_s("Resolving topic names from Telegram...")
        threading.Thread(target=lambda: resolve_route_topic_names(
            int(self.api_id_var.get()), self.api_hash_var.get(), [dict(r) for r in self._b_routes],
            self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, self._builder_chk_resolved_names)
    def _builder_chk_resolved_names(self):
        try:
            r = self.result_queue.get_nowait()
            if not isinstance(r, dict) or r.get("kind") != "route_topic_names":
                self.result_queue.put(r)
                self.after(500, self._builder_chk_resolved_names)
                return
            changed = 0
            for update in r.get("updates", []):
                idx = update.get("idx")
                if idx is None or idx >= len(self._b_routes):
                    continue
                route = self._b_routes[idx]
                for key in ("src_topic_title", "dest_topic_title"):
                    if update.get(key) and route.get(key) != update[key]:
                        route[key] = update[key]; changed += 1
            if changed:
                self._builder_redraw()
                self._log_s(f"Resolved {changed} topic name(s).")
            else:
                self._log_s("No extra topic names resolved.")
        except queue.Empty:
            self.after(500, self._builder_chk_resolved_names)
    def _builder_add_bucket(self):
        if self._telegram_busy("create a topic"):
            return
        title = self.b_new_topic_var.get().strip()
        if not title: messagebox.showerror("", "Enter a topic name."); return
        if not self._b_dest_group_id:
            messagebox.showerror("", "Load destination topics first."); return
        ai = self.api_id_var.get(); ah = self.api_hash_var.get()
        threading.Thread(target=lambda: create_topic_in_group(
            int(ai), ah, self._b_dest_group_id, title,
            self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, self._builder_chk_new_bucket)
    def _builder_chk_new_bucket(self):
        try:
            r = self.result_queue.get_nowait()
            if r:
                self._b_dest_topics.append(r)
                self.b_new_topic_var.set("")
                self._log_s(f"Topic '{r['title']}' created.")
                if hasattr(self, 'b_dest_status'):
                    self.b_dest_status.config(
                        text=f"{len(self._b_dest_topics)} topics  ('{r['title']}' added)", fg=SUCCESS)
                self._builder_redraw()
            else:
                messagebox.showerror("Error", "Could not create topic.")
        except queue.Empty:
            self.after(500, self._builder_chk_new_bucket)
    def _builder_match_dest(self, src_title):
        src_lower = src_title.lower().strip()
        for t in self._b_dest_topics:
            if t["title"].lower().strip() == src_lower:
                return t, "exact"
        for t in self._b_dest_topics:
            d = t["title"].lower().strip()
            if len(d) >= 4 and len(src_lower) >= 4:
                if d in src_lower or src_lower in d:
                    return t, "suggested"
        src_words = set(w for w in src_lower.split() if len(w) >= 4)
        for t in self._b_dest_topics:
            d_words = set(w for w in t["title"].lower().split() if len(w) >= 4)
            if src_words & d_words:
                return t, "suggested"
        return None, None
    def _builder_make_searchable_combo(self, combo, values):
        values = sorted(values, key=lambda v: v.lower())
        combo["values"] = values
        def _filter(_event=None, combo=combo, all_values=values):
            q = combo.get().strip().lower()
            if not q:
                combo["values"] = all_values
                return
            combo["values"] = [v for v in all_values if q in v.lower()]
        combo.bind("<KeyRelease>", _filter)
    def _builder_update_add_buttons(self):
        if not hasattr(self, "b_add_selected_btn"):
            return
        if self.b_whole_group_var.get():
            self.b_add_selected_btn.state(["disabled"])
            self.b_add_all_btn.config(text="Add Source Group")
        elif self.b_flatten_group_var.get():
            self.b_add_selected_btn.state(["disabled"])
            self.b_add_all_btn.config(text="Add Source Group")
        else:
            self.b_add_selected_btn.state(["!disabled"])
            self.b_add_all_btn.config(text="Add All Topics to Routes")
    def _builder_whole_mode_changed(self):
        if self.b_whole_group_var.get():
            self.b_flatten_group_var.set(False)
        self._builder_update_add_buttons()
    def _builder_flatten_mode_changed(self):
        if self.b_flatten_group_var.get():
            self.b_whole_group_var.set(False)
        self._builder_update_add_buttons()
    def _builder_add_routes(self, add_all=False):
        clone_topics = self.b_whole_group_var.get()
        flatten_group = self.b_flatten_group_var.get()
        if not self._b_dest_group_id:
            messagebox.showerror("", "Choose a destination group first."); return
        if not self._b_dest_topics and not (clone_topics and self._b_dest_is_forum):
            messagebox.showerror("", "Load destination topics first."); return
        sel_src = self.b_src_var.get()
        if not sel_src:
            messagebox.showerror("", "Select a source group."); return
        src_grp = next((g for g in self.groups if g["name"] in sel_src), None)
        if not src_grp: return
        added = 0
        new_routes = []
        def make_route(tid, ttitle, clone=False, flatten=False):
            # Skip duplicates
            if any(r["src_group_id"] == src_grp["id"] and r["src_topic_id"] == tid
                   for r in self._b_routes):
                return None
            dest, conf = (None, None) if flatten else self._builder_match_dest(ttitle)
            if clone and conf != "exact":
                dest = None
                conf = "manual"
            return {
                "src_group_id": src_grp["id"], "src_group_name": src_grp["name"],
                "src_topic_id": tid, "src_topic_title": ttitle,
                "source_has_topics": self._b_src_is_forum,
                "dest_group_id": self._b_dest_group_id,
                "dest_topic_id": dest["id"] if dest else None,
                "dest_topic_title": dest["title"] if dest else (ttitle if clone else None),
                "dest_topic_action": "use" if dest else ("create" if clone else "use"),
                "accepted": True if clone else conf == "exact",
                "confidence": conf
            }
        if clone_topics or flatten_group:
            if not self._b_src_topics:
                messagebox.showerror("", "Load source topics first."); return
            if clone_topics and not self._b_src_is_forum:
                messagebox.showerror("", "This source group has no topics to clone. Use the one-topic option instead.")
                return
            for t in self._b_src_topics:
                r = make_route(t["id"], t["title"], clone=clone_topics, flatten=flatten_group)
                if r: self._b_routes.append(r); new_routes.append(r); added += 1
        elif add_all:
            if not self._b_src_topics:
                messagebox.showerror("", "Load source topics first."); return
            for t in self._b_src_topics:
                r = make_route(t["id"], t["title"])
                if r: self._b_routes.append(r); new_routes.append(r); added += 1
        else:
            sel = self.b_src_topic_var.get()
            if not sel:
                messagebox.showerror("", "Select a source topic or use 'Add ALL'."); return
            t = self._builder_parse_topic(sel, self._b_src_topics)
            if not t: messagebox.showerror("", "Could not parse topic."); return
            r = make_route(t["id"], t["title"])
            if r: self._b_routes.append(r); new_routes.append(r); added += 1
        if added == 0:
            messagebox.showinfo("", "No new routes added — already in list."); return
        if clone_topics:
            self._log_s(f"Cloned {added} topic route(s) from {src_grp['name']}. Missing destination topics will be created when the project runs.")
        elif flatten_group:
            self._log_s(f"Added {added} topic route(s) from {src_grp['name']} into one destination topic.")
        else:
            self._log_s(f"Added {added} route(s) from {src_grp['name']}")
        self._builder_redraw()
        if flatten_group and new_routes:
            self._builder_choose_existing(new_routes)
    def _builder_accept_suggestions(self):
        selected = self._builder_selected_routes()
        if selected:
            target_routes = selected
        else:
            target_routes = [
                r for r in self._b_routes
                if r.get("confidence") == "suggested" and r.get("dest_topic_id") and not r.get("accepted")
            ]
            if target_routes and not messagebox.askyesno(
                "Accept all suggestions",
                f"No route is selected.\n\nAccept all {len(target_routes)} suggestion(s) in this project?"
            ):
                return
        count = 0
        for r in target_routes:
            if r.get("confidence") == "suggested" and r.get("dest_topic_id") and not r.get("accepted"):
                r["accepted"] = True; count += 1
        self._builder_redraw()
        if hasattr(self, 'b_status_lbl'):
            self.b_status_lbl.config(text=f"Accepted {count} suggestion(s).", fg=SUCCESS if count else MUTED)
    def _builder_remove_selected(self):
        routes = self._builder_selected_routes()
        if not routes:
            messagebox.showinfo("", "Select one or more source-topic rows first."); return
        if messagebox.askyesno("Delete selected", f"Delete {len(routes)} source topic(s) from this project-"):
            for route in routes:
                if route in self._b_routes:
                    self._b_routes.remove(route)
            self._builder_redraw()
    def _builder_clear(self):
        if self._b_routes and messagebox.askyesno("Clear", "Remove ALL routes from this project-"):
            self._b_routes.clear()
            self._builder_redraw()
    def _builder_selected_route(self):
        routes = self._builder_selected_routes()
        return routes[0] if routes else None
    def _builder_selected_routes(self):
        if not hasattr(self, "b_routes_tree"):
            return []
        sel = self.b_routes_tree.selection()
        if not sel:
            return []
        route_map = getattr(self, "_b_tree_routes", {})
        group_map = getattr(self, "_b_tree_group_routes", {})
        routes = []
        for item in sel:
            route = route_map.get(item)
            if route and route not in routes:
                routes.append(route)
            for child_route in group_map.get(item, []):
                if child_route not in routes:
                    routes.append(child_route)
        return routes
    def _builder_tree_mousewheel(self, event):
        if hasattr(self, "b_routes_tree"):
            self.b_routes_tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"
    def _builder_use_existing_selected(self):
        routes = self._builder_selected_routes()
        if not routes:
            messagebox.showinfo("", "Select one or more source routes first."); return
        self._builder_choose_existing(routes)
    def _builder_create_for_selected(self):
        routes = self._builder_selected_routes()
        if not routes:
            messagebox.showinfo("", "Select one or more source routes first."); return
        if not self._b_dest_group_id:
            messagebox.showerror("", "Load destination topics first."); return
        title = simpledialog.askstring(
            "Create destination topic",
            "New destination topic name:",
            initialvalue=routes[0].get("src_topic_title", ""),
            parent=self
        )
        if not title:
            return
        self._b_pending_create_routes = routes
        ai = self.api_id_var.get(); ah = self.api_hash_var.get()
        threading.Thread(target=lambda: create_topic_in_group(
            int(ai), ah, self._b_dest_group_id, title.strip(),
            self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, self._builder_chk_created_for_route)
    def _builder_chk_created_for_route(self):
        try:
            r = self.result_queue.get_nowait()
            routes = getattr(self, "_b_pending_create_routes", [])
            self._b_pending_create_routes = []
            if r and routes:
                self._b_dest_topics.append(r)
                for route in routes:
                    route["dest_topic_id"] = r["id"]
                    route["dest_topic_title"] = r["title"]
                    route["dest_topic_action"] = "create"
                    route["accepted"] = True
                    route["confidence"] = "manual"
                self._log_s(f"Topic '{r['title']}' created and assigned to {len(routes)} route(s).")
                self._builder_redraw()
            else:
                messagebox.showerror("Error", "Could not create topic.")
        except queue.Empty:
            self.after(500, self._builder_chk_created_for_route)
    def _builder_clear_destination_selected(self):
        routes = self._builder_selected_routes()
        if not routes:
            messagebox.showinfo("", "Select one or more source routes first."); return
        if not messagebox.askyesno("Clear destination", f"Remove destination from {len(routes)} route(s)-"):
            return
        for route in routes:
            route["dest_topic_id"] = None
            route["dest_topic_title"] = None
            route["dest_topic_action"] = "use"
            route["accepted"] = False
            route["confidence"] = None
        self._builder_redraw()
    def _builder_choose_existing(self, routes):
        if isinstance(routes, dict):
            routes = [routes]
        route = routes[0]
        d = tk.Toplevel(self)
        d.title("Use Existing")
        d.configure(bg=BG2)
        d.geometry("620x520")
        d.minsize(520, 420)
        d.grab_set()
        src_txt = f"{route['src_group_name']} / {route['src_topic_title']}"
        label = "Source topic" if len(routes) == 1 else f"{len(routes)} source topics selected"
        tk.Label(d, text=label, bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w", padx=16, pady=(16,0))
        tk.Label(d, text=src_txt if len(routes) == 1 else "The chosen destination will be applied to all selected routes.",
                 bg=BG2, fg=TEXT, font=FONT_B).pack(anchor="w", padx=16, pady=(0,8))
        suggested = route.get("dest_topic_title") if route.get("confidence") == "suggested" else ""
        if suggested:
            tk.Label(d, text=f"Suggested: {suggested}", bg=BG2, fg=WARNING, font=FONT_SM).pack(anchor="w", padx=16, pady=(0,6))
        self._builder_merge_known_dest_topics()
        tk.Label(d, text="Search existing destination topics:", bg=BG2, fg=TEXT, font=FONT).pack(anchor="w", padx=16, pady=(0,4))
        search_var = tk.StringVar(value=route.get("dest_topic_title") or suggested or "")
        search = ttk.Entry(d, textvariable=search_var)
        search.pack(fill=tk.X, padx=16, pady=(0,8))
        topics = sorted(self._b_dest_topics, key=lambda t: (t.get("title", "").lower(), int(t.get("id", 0))))
        current_view = []
        list_frame = tk.Frame(d, bg=BG2); list_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0,10))
        sb = ttk.Scrollbar(list_frame); sb.pack(side=tk.RIGHT, fill=tk.Y)
        lb = tk.Listbox(list_frame, bg=BG3, fg=TEXT, font=FONT, selectbackground=ACCENT,
                        selectforeground=BG, bd=0, activestyle="none", height=14,
                        exportselection=False, yscrollcommand=sb.set)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); sb.config(command=lb.yview)
        status = tk.Label(d, text="", bg=BG2, fg=MUTED, font=FONT_SM)
        status.pack(anchor="w", padx=16, pady=(0,4))
        selected_topic = {"topic": None}
        def _display(topic):
            return f"{topic['title']}  (id:{topic['id']})"
        def _filter(*_):
            nonlocal current_view
            q = search_var.get().strip().lower()
            current_view = [
                t for t in topics
                if not q or q in t.get("title", "").lower() or q in str(t.get("id", ""))
            ]
            lb.delete(0, tk.END)
            for t in current_view:
                lb.insert(tk.END, _display(t))
            if current_view:
                lb.select_set(0)
                lb.activate(0)
                lb.see(0)
                selected_topic["topic"] = current_view[0]
            else:
                selected_topic["topic"] = None
            status.config(text=f"{len(current_view)}/{len(topics)} topic(s)")
        search_var.trace_add("write", _filter)
        def _remember_selection(*_):
            idxs = lb.curselection()
            if idxs and idxs[0] < len(current_view):
                selected_topic["topic"] = current_view[idxs[0]]
        lb.bind("<<ListboxSelect>>", _remember_selection)
        def _use():
            _remember_selection()
            idxs = lb.curselection()
            t = selected_topic.get("topic")
            if idxs and idxs[0] < len(current_view):
                t = current_view[idxs[0]]
            if not t:
                q = search_var.get().strip().lower()
                exact = [x for x in current_view if x.get("title", "").lower() == q or str(x.get("id")) == q]
                t = exact[0] if exact else (current_view[0] if len(current_view) == 1 else None)
            if not t: messagebox.showerror("", "Select a destination topic."); return
            for route in routes:
                old = route.get("dest_topic_title")
                route["dest_topic_id"] = t["id"]
                route["dest_topic_title"] = t["title"]
                route["dest_topic_action"] = "use"
                route["accepted"] = True
                if t["title"] != old or route.get("confidence") != "exact":
                    route["confidence"] = "manual"
            self._builder_redraw(); d.destroy()
        br = tk.Frame(d, bg=BG2); br.pack(pady=4)
        ttk.Button(br, text="Use Existing", style="P.TButton", command=_use).pack(side=tk.LEFT, padx=4)
        ttk.Button(br, text="Cancel", style="G.TButton", command=d.destroy).pack(side=tk.LEFT, padx=4)
        d.bind("<Return>", lambda e: _use())
        lb.bind("<Double-1>", lambda e: _use())
        search.focus()
        search.select_range(0, tk.END)
        _filter()
    def _builder_redraw(self):
        if not hasattr(self, 'b_routes_tree'):
            return
        tree = self.b_routes_tree
        for item in tree.get_children():
            tree.delete(item)
        self._b_tree_routes = {}
        self._b_tree_group_routes = {}
        def route_status(route):
            if route.get("dest_topic_action") == "create" and route.get("dest_topic_title"):
                return "Create", "ready", 3
            if not route.get("dest_topic_id"):
                return "Choose destination", "needs", 0
            if route.get("accepted"):
                return "Ready", "ready", 3
            if route.get("confidence") == "suggested":
                return "Suggestion - approve", "suggestion", 1
            return "Review", "review", 2
        buckets = {}
        for r in self._b_routes:
            status, tag, priority = route_status(r)
            if r.get("dest_topic_action") == "create" and r.get("dest_topic_title"):
                key = (r.get("dest_topic_title", ""), f"create:{r.get('dest_topic_title', '')}")
            elif not r.get("dest_topic_id"):
                key = ("Needs destination", None)
            elif tag == "suggestion":
                key = ("Suggestions to approve", "__suggestions__")
            elif tag == "review":
                key = ("Needs review", "__review__")
            else:
                key = (r.get("dest_topic_title", ""), r.get("dest_topic_id"))
            bucket = buckets.setdefault(key, {"routes": [], "priority": priority, "tag": tag})
            bucket["routes"].append(r)
            bucket["priority"] = min(bucket["priority"], priority)
            if priority < {"needs": 0, "suggestion": 1, "review": 2, "ready": 3}.get(bucket["tag"], 3):
                bucket["tag"] = tag
        if not self._b_routes:
            tree.insert("", "end", text="No routes yet", values=("Add source topics above", "", ""))
        else:
            def bucket_sort(item):
                (title, _), data = item
                return (data["priority"], title.lower())
            for (title, tid), data in sorted(buckets.items(), key=bucket_sort):
                routes = data["routes"]
                parent_tag = {
                    "needs": "needs_parent",
                    "suggestion": "suggest_parent",
                    "review": "review_parent",
                    "ready": "ready_parent",
                }.get(data["tag"], "")
                parent = tree.insert("", "end", text=title, open=True,
                                     values=("", f"{len(routes)} source(s)", ""),
                                     tags=(parent_tag,))
                self._b_tree_group_routes[parent] = list(routes)
                for r in sorted(routes, key=lambda x: (x["src_group_name"].lower(), x["src_topic_title"].lower())):
                    status, row_tag, _priority = route_status(r)
                    if r.get("dest_topic_action") == "create" and r.get("dest_topic_title"):
                        dest = r.get("dest_topic_title", "")
                    elif not r.get("dest_topic_id"):
                        dest = ""
                    else:
                        dest = r.get("dest_topic_title", "")
                    iid = tree.insert(parent, "end",
                                      text="",
                                      values=(f"{r['src_group_name']} / {r['src_topic_title']}", dest, status),
                                      tags=(row_tag,))
                    self._b_tree_routes[iid] = r
        # Update status label
        total = len(self._b_routes)
        accepted = sum(1 for r in self._b_routes if r.get("accepted"))
        n_unassigned = sum(
            1 for r in self._b_routes
            if not r.get("dest_topic_id")
            and not (r.get("dest_topic_action") == "create" and r.get("dest_topic_title"))
        )
        pending = total - accepted - n_unassigned
        if total == 0:
            status = "No routes yet — add sources above."
            color = MUTED
        elif n_unassigned > 0:
            status = f"{n_unassigned} route(s) need a destination."
            color = DANGER
        elif pending > 0:
            status = f"{pending} suggestion(s) need approval."
            color = WARNING
        else:
            status = f"All {total} route(s) ready to save."
            color = SUCCESS
        if hasattr(self, 'b_status_lbl'):
            self.b_status_lbl.config(text=status, fg=color)
    def _builder_load_project(self):
        selected = self.b_existing_var.get()
        name = getattr(self, "_b_project_display_to_name", {}).get(selected, selected)
        if not name: messagebox.showinfo("", "Select a project to load."); return
        p = next((x for x in self.config_data["projects"] if x["name"] == name), None)
        if not p: return
        if p.get("mode") != "routes":
            messagebox.showinfo("Classic project",
                "This project can be run or deleted, but it cannot be edited in Project Builder.")
            return
        self._b_editing_project_name = p["name"]
        self.b_name_var.set(p["name"])
        self._b_routes = [dict(r) for r in p.get("routes", [])]
        if self._b_routes:
            r0 = self._b_routes[0]
            self._b_dest_group_id = r0.get("dest_group_id")
            grp = next((g for g in self.groups if g["id"] == self._b_dest_group_id), None)
            if grp:
                self.b_dest_var.set(f"{grp['name']}  (id: {grp['id']})")
                self._b_dest_group_name = grp["name"]
        # Rebuild dest_topics from routes
        seen = {}
        for r in self._b_routes:
            if r.get("dest_topic_id") and r["dest_topic_id"] not in seen:
                seen[r["dest_topic_id"]] = {"id": r["dest_topic_id"], "title": r["dest_topic_title"]}
        self._b_dest_topics = list(seen.values())
        self._builder_redraw()
        if hasattr(self, 'b_dest_status'):
            self.b_dest_status.config(
                text=f"Loaded from project — {len(self._b_dest_topics)} dest topics known.", fg=ACCENT)
        self._builder_merge_known_dest_topics()
        self._builder_resolve_generic_names()
        # Restore filter checkboxes if saved
        f = p.get("filters", {})
        if hasattr(self, 'b_f_msg'):
            self.b_f_msg.set(f.get("messages", True))
            self.b_f_img.set(f.get("images", True))
            self.b_f_fil.set(f.get("files", True))
            self.b_f_skip_dup.set(f.get("skip_duplicates", True))
        self._log_s(f"Project '{name}' loaded: {len(self._b_routes)} routes")
    def _builder_delete_project(self):
        selected = self.b_existing_var.get()
        name = getattr(self, "_b_project_display_to_name", {}).get(selected, selected)
        if not name:
            messagebox.showinfo("", "Select a project to delete."); return
        idx = next((i for i, p in enumerate(self.config_data.get("projects", [])) if p.get("name") == name), None)
        if idx is None:
            return
        project = self.config_data["projects"][idx]
        if not messagebox.askyesno("Delete project",
            f"Delete '{project.get('name')}'-\n\nProgress/state files will be kept."):
            return
        self.config_data["projects"].pop(idx)
        save_config(self.config_data)
        if self.b_name_var.get().strip() == name:
            self.b_name_var.set("")
            self._b_editing_project_name = ""
            self._b_routes = []
            self._b_dest_topics = []
            self._b_src_topics = []
            self._builder_redraw()
        self.b_existing_var.set("")
        self._builder_refresh_project_combo()
        self._download_refresh_project_combo()
        self._refresh_run_dd()
        self._log_s(f"Project '{name}' deleted. State file kept.")
    def _builder_save_project(self):
        name = self.b_name_var.get().strip() or getattr(self, "_b_editing_project_name", "").strip()
        if name and not self.b_name_var.get().strip():
            self.b_name_var.set(name)
        if not name: messagebox.showerror("", "Enter a project name."); return
        if not self._b_routes:
            messagebox.showerror("", "No routes to save. Add sources first."); return
        unassigned = [
            r for r in self._b_routes
            if not r.get("dest_topic_id")
            and not (r.get("dest_topic_action") == "create" and r.get("dest_topic_title"))
        ]
        if unassigned:
            messagebox.showerror("Unassigned routes",
                f"{len(unassigned)} route(s) have no destination.\n"
                "Use Existing or Create before saving."); return
        unaccepted = [r for r in self._b_routes if not r.get("accepted")]
        if unaccepted:
            messagebox.showerror("Suggestions need approval",
                f"{len(unaccepted)} suggestion(s) still need approval.\n"
                "Choose Accept Suggestions, Use Existing, or Create before saving."); return
        safe = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        dest_name = self._b_dest_group_name or (self._b_routes[0]["dest_group_id"] if self._b_routes else "Unknown")
        src_names = ", ".join(sorted(set(r["src_group_name"] for r in self._b_routes)))
        existing = next((p for p in self.config_data["projects"] if p["name"] == name), None)
        if existing:
            old_map = {f"{r['src_group_id']}_{r['src_topic_id']}": r.get("dest_topic_id")
                       for r in existing.get("routes", [])}
            changed = [r["src_topic_title"] for r in self._b_routes
                       if old_map.get(f"{r['src_group_id']}_{r['src_topic_id']}") not in
                       (None, r.get("dest_topic_id"))]
            if changed:
                if not messagebox.askyesno("Destination changed",
                    f"These routes changed destination:\n{', '.join(changed[:5])}\n\n"
                    "This may cause duplicates. Continue-"): return
            existing["routes"] = self._b_routes
            existing["source_name"] = src_names
            existing["dest_name"] = dest_name
            existing["filters"] = {
                "messages": self.b_f_msg.get(),
                "images":   self.b_f_img.get(),
                "files":    self.b_f_fil.get(),
                "skip_duplicates": self.b_f_skip_dup.get()
            }
        else:
            self.config_data["projects"].append({
                "name": name, "mode": "routes",
                "source_name": src_names, "dest_name": dest_name,
                "routes": self._b_routes,
                "state_file": f"state_{safe}.json",
                "filters": {
                    "messages": self.b_f_msg.get(),
                    "images":   self.b_f_img.get(),
                    "files":    self.b_f_fil.get(),
                    "skip_duplicates": self.b_f_skip_dup.get()
                }
            })
        save_config(self.config_data)
        self._b_editing_project_name = name
        self._refresh_proj_list()
        self._refresh_run_dd()
        self._builder_refresh_project_combo()
        if hasattr(self, 'b_status_lbl'):
            self.b_status_lbl.config(text=f"Project '{name}' saved.", fg=SUCCESS)
        self._log_s(f"Project '{name}' saved — {len(self._b_routes)} routes.")
    def _auto(self, parent):
        _,inner=make_scrollable(parent)
        cg=card(inner,"Create destination group  (optional)"); cg.pack(fill=tk.X,padx=20,pady=(20,8))
        tk.Label(cg,text="Creates a supergroup and enables Topics. Skip if destination already exists.",
                 bg=BG2,fg=MUTED,font=FONT_SM,justify="left").pack(anchor="w",pady=(0,8))
        r=tk.Frame(cg,bg=BG2); r.pack(fill=tk.X)
        tk.Label(r,text="Group name:",bg=BG2,fg=MUTED,font=FONT_SM,width=14,anchor="w").pack(side=tk.LEFT)
        self.new_group_name=tk.StringVar()
        ttk.Entry(r,textvariable=self.new_group_name,width=28).pack(side=tk.LEFT,padx=(0,10))
        ttk.Button(r,text="Create + Enable Topics",style="P.TButton",command=self._create_group).pack(side=tk.LEFT)
        ap=card(inner,"Add auto backup project"); ap.pack(fill=tk.X,padx=20,pady=8)
        self.proj_name_var=tk.StringVar()
        field_row(ap,"Project name",self.proj_name_var,30)
        self.source_var=tk.StringVar()
        self.source_combo=combo_row(ap,"Source group",self.source_var)
        self.dest_var=tk.StringVar()
        self.dest_combo=combo_row(ap,"Destination group",self.dest_var)
        # Filter options
        flt=tk.Frame(ap,bg=BG2); flt.pack(fill=tk.X,pady=(10,0))
        tk.Label(flt,text="Copy:",bg=BG2,fg=MUTED,font=FONT_SM).pack(side=tk.LEFT,padx=(0,12))
        self.f_msg=tk.BooleanVar(value=True); self.f_img=tk.BooleanVar(value=True); self.f_fil=tk.BooleanVar(value=True)
        for var,lbl in [(self.f_msg,"Text messages"),(self.f_img,"Images"),(self.f_fil,"Files")]:
            tk.Checkbutton(flt,text=lbl,variable=var,bg=BG2,fg=TEXT,activebackground=BG2,
                           activeforeground=TEXT,selectcolor=BG3,font=FONT_SM).pack(side=tk.LEFT,padx=(0,12))
        flt2=tk.Frame(ap,bg=BG2); flt2.pack(fill=tk.X,pady=(4,0))
        self.f_skip_dup=tk.BooleanVar(value=True)
        tk.Checkbutton(flt2,text="Skip duplicate files (same name + size)",variable=self.f_skip_dup,
                       bg=BG2,fg=TEXT,activebackground=BG2,activeforeground=TEXT,selectcolor=BG3,font=FONT_SM).pack(anchor="w")
        br=tk.Frame(ap,bg=BG2); br.pack(fill=tk.X,pady=(12,0))
        ttk.Button(br,text="Add Project",style="P.TButton",command=self._add_project).pack(side=tk.LEFT)
        ttk.Button(br,text="Refresh groups",command=self._load_groups).pack(side=tk.LEFT,padx=10)
        sp=card(inner,"Saved auto projects"); sp.pack(fill=tk.X,padx=20,pady=(8,20))
        lf=tk.Frame(sp,bg=BG2); lf.pack(fill=tk.X)
        sb2=ttk.Scrollbar(lf); sb2.pack(side=tk.RIGHT,fill=tk.Y)
        self.proj_list=tk.Listbox(lf,bg=BG3,fg=TEXT,font=FONT,selectbackground=ACCENT,selectforeground=BG,
                                   bd=0,height=5,yscrollcommand=sb2.set)
        self.proj_list.pack(fill=tk.X); sb2.config(command=self.proj_list.yview)
        br2=tk.Frame(sp,bg=BG2); br2.pack(anchor="w",pady=(8,0))
        ttk.Button(br2,text="Rename",style="G.TButton",command=self._rename_project).pack(side=tk.LEFT)
        ttk.Button(br2,text="Delete",style="G.TButton",command=self._del_project).pack(side=tk.LEFT,padx=8)
        self._refresh_proj_list()
    def _refresh_combos(self):
        groups = sorted(self.groups, key=lambda g: g.get("name", "").lower())
        names=[f"{g['name']}  (id: {g['id']})" for g in groups]
        if hasattr(self, 'source_combo'): self.source_combo["values"]=names; self.dest_combo["values"]=names
        if hasattr(self,'m_src_combo'): self.m_src_combo["values"]=names; self.m_dst_combo["values"]=names
        if hasattr(self,'b_src_combo'):
            self._builder_make_searchable_combo(self.b_src_combo, names)
            self._builder_make_searchable_combo(self.b_dest_combo, names)
        if hasattr(self,'d_src_combo'): self.d_src_combo["values"]=names
        if hasattr(self,'c_group_combo'): self.c_group_combo["values"]=names
    def _create_group(self):
        if self._telegram_busy("create a group"):
            return
        title=self.new_group_name.get().strip()
        if not title: messagebox.showerror("Missing","Enter a name."); return
        ai=self.api_id_var.get(); ah=self.api_hash_var.get()
        threading.Thread(target=lambda:create_group(int(ai),ah,title,self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_create_group)
    def _chk_create_group(self):
        try:
            r=self.result_queue.get_nowait()
            if r:
                self.groups.append(r); self._refresh_combos()
                if hasattr(self, 'dest_var'):
                    self.dest_var.set(f"{r['name']}  (id: {r['id']})")
                if hasattr(self, 'b_dest_var'):
                    self.b_dest_var.set(f"{r['name']}  (id: {r['id']})")
                    self._b_dest_group_id = r["id"]
                    self._b_dest_group_name = r["name"]
                    if hasattr(self, 'b_dest_status'):
                        self.b_dest_status.config(text="Group created. Loading its topics...", fg=SUCCESS)
                    self.after(300, self._builder_load_dest_topics)
                messagebox.showinfo("Done",f"'{r['name']}' created with Topics enabled!")
                self.new_group_name.set("")
            else: messagebox.showerror("Error","Failed — check log.")
        except queue.Empty: self.after(500,self._chk_create_group)
    def _add_project(self):
        name=self.proj_name_var.get().strip(); src_s=self.source_var.get(); dst_s=self.dest_var.get()
        if not name or not src_s or not dst_s: messagebox.showerror("Missing","Fill in all fields."); return
        src=next((g for g in self.groups if g["name"] in src_s),None)
        dst=next((g for g in self.groups if g["name"] in dst_s),None)
        if not src or not dst: messagebox.showerror("Error","Can't match groups. Refresh."); return
        safe=re.sub(r'[^a-zA-Z0-9_]','_',name)
        self.config_data["projects"].append({
            "name":name,"mode":"auto",
            "source_id":src["id"],"source_name":src["name"],
            "dest_id":dst["id"],"dest_name":dst["name"],
            "state_file":f"state_{safe}.json",
            "filters":{"messages":self.f_msg.get(),"images":self.f_img.get(),
                       "files":self.f_fil.get(),"skip_duplicates":self.f_skip_dup.get()}
        })
        save_config(self.config_data); self._refresh_proj_list(); self._refresh_run_dd()
        self.proj_name_var.set("")
    def _rename_project(self):
        sel=self.proj_list.curselection()
        if not sel: messagebox.showinfo("","Select a project."); return
        p=self.config_data["projects"][sel[0]]
        d=tk.Toplevel(self); d.title("Rename"); d.configure(bg=BG2)
        d.geometry("360x140"); d.resizable(False,False); d.grab_set()
        tk.Label(d,text="New name:",bg=BG2,fg=TEXT,font=FONT).pack(pady=(20,6))
        nv=tk.StringVar(value=p["name"])
        e=ttk.Entry(d,textvariable=nv,width=32); e.pack(pady=(0,12)); e.select_range(0,tk.END); e.focus()
        def _ok():
            nn=nv.get().strip()
            if not nn: return
            old_sf=p["state_file"]; safe=re.sub(r'[^a-zA-Z0-9_]','_',nn); new_sf=f"state_{safe}.json"
            if old_sf!=new_sf and os.path.exists(old_sf):
                try: os.rename(old_sf,new_sf)
                except Exception as ex: messagebox.showerror("Error",str(ex)); return
            p["name"]=nn; p["state_file"]=new_sf
            save_config(self.config_data); self._refresh_proj_list(); self._refresh_run_dd(); d.destroy()
        ttk.Button(d,text="Rename",style="P.TButton",command=_ok).pack()
        d.bind("<Return>",lambda e:_ok())
    def _del_project(self):
        sel=self.proj_list.curselection()
        if not sel: return
        if messagebox.askyesno("Delete","Delete project- (state file kept)"):
            self.config_data["projects"].pop(sel[0]); save_config(self.config_data)
            self._refresh_proj_list(); self._refresh_run_dd()
    def _refresh_proj_list(self):
        if not hasattr(self, 'proj_list'):
            return
        self.proj_list.delete(0,tk.END)
        for p in self.config_data.get("projects",[]):
            mode=f" [{p.get('mode','auto')}]"
            src=p.get('source_name','Unknown'); dst=p.get('dest_name','Unknown')
            self.proj_list.insert(tk.END,f"  {p['name']}{mode}   -   {src} -> {dst}")
    # -- MANUAL MAP -------------------------------------------------------------
    def _manual(self, parent):
        _,inner=make_scrollable(parent)
        info=card(inner,"Manual topic mapping"); info.pack(fill=tk.X,padx=20,pady=(20,8))
        tk.Label(info,text="Map individual topics from any source group to any destination topic.\n"
                           "Source groups without topics are treated as a single channel (General).\n"
                           "You can mix topics from different source groups into one destination.",
                 bg=BG2,fg=MUTED,font=FONT_SM,justify="left").pack(anchor="w",pady=(0,4))
        sp=card(inner,"Source"); sp.pack(fill=tk.X,padx=20,pady=(0,6))
        r1=tk.Frame(sp,bg=BG2); r1.pack(fill=tk.X,pady=3)
        tk.Label(r1,text="Source group",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_src_var=tk.StringVar()
        self.m_src_combo=ttk.Combobox(r1,textvariable=self.m_src_var,width=36,state="readonly"); self.m_src_combo.pack(side=tk.LEFT,padx=(0,8))
        ttk.Button(r1,text="Load Topics",command=self._load_src_topics).pack(side=tk.LEFT)
        r2=tk.Frame(sp,bg=BG2); r2.pack(fill=tk.X,pady=3)
        tk.Label(r2,text="Source topic",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_src_topic_var=tk.StringVar()
        self.m_src_topic_combo=ttk.Combobox(r2,textvariable=self.m_src_topic_var,width=36,state="readonly"); self.m_src_topic_combo.pack(side=tk.LEFT)
        dp=card(inner,"Destination"); dp.pack(fill=tk.X,padx=20,pady=(0,6))
        r3=tk.Frame(dp,bg=BG2); r3.pack(fill=tk.X,pady=3)
        tk.Label(r3,text="Dest group",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_dst_var=tk.StringVar()
        self.m_dst_combo=ttk.Combobox(r3,textvariable=self.m_dst_var,width=36,state="readonly"); self.m_dst_combo.pack(side=tk.LEFT,padx=(0,8))
        ttk.Button(r3,text="Load Topics",command=self._load_dst_topics).pack(side=tk.LEFT)
        r4=tk.Frame(dp,bg=BG2); r4.pack(fill=tk.X,pady=3)
        tk.Label(r4,text="Dest topic",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_dst_topic_var=tk.StringVar()
        self.m_dst_topic_combo=ttk.Combobox(r4,textvariable=self.m_dst_topic_var,width=36,state="readonly"); self.m_dst_topic_combo.pack(side=tk.LEFT,padx=(0,8))
        r5=tk.Frame(dp,bg=BG2); r5.pack(fill=tk.X,pady=3)
        tk.Label(r5,text="Or create topic",bg=BG2,fg=MUTED,font=FONT_SM,width=16,anchor="w").pack(side=tk.LEFT)
        self.m_new_topic_var=tk.StringVar()
        ttk.Entry(r5,textvariable=self.m_new_topic_var,width=26).pack(side=tk.LEFT,padx=(0,8))
        ttk.Button(r5,text="Create Topic",command=self._create_dest_topic).pack(side=tk.LEFT)
        # Filters for manual mode
        fp=card(inner,"Filters"); fp.pack(fill=tk.X,padx=20,pady=(0,6))
        frow=tk.Frame(fp,bg=BG2); frow.pack(fill=tk.X)
        tk.Label(frow,text="Copy:",bg=BG2,fg=MUTED,font=FONT_SM).pack(side=tk.LEFT,padx=(0,12))
        self.mf_msg=tk.BooleanVar(value=True); self.mf_img=tk.BooleanVar(value=True); self.mf_fil=tk.BooleanVar(value=True)
        for var,lbl in [(self.mf_msg,"Text"),(self.mf_img,"Images"),(self.mf_fil,"Files")]:
            tk.Checkbutton(frow,text=lbl,variable=var,bg=BG2,fg=TEXT,activebackground=BG2,
                           activeforeground=TEXT,selectcolor=BG3,font=FONT_SM).pack(side=tk.LEFT,padx=(0,10))
        self.mf_skip_dup=tk.BooleanVar(value=True)
        tk.Checkbutton(fp,text="Skip duplicate files",variable=self.mf_skip_dup,
                       bg=BG2,fg=TEXT,activebackground=BG2,activeforeground=TEXT,selectcolor=BG3,font=FONT_SM).pack(anchor="w",pady=(4,0))
        ar=tk.Frame(inner,bg=BG); ar.pack(fill=tk.X,padx=20,pady=4)
        ttk.Button(ar,text="+ Add this mapping",style="P.TButton",command=self._add_mapping).pack(side=tk.LEFT)
        ml=card(inner,"Mappings in this project"); ml.pack(fill=tk.X,padx=20,pady=(0,8))
        lf=tk.Frame(ml,bg=BG2); lf.pack(fill=tk.X)
        sb3=ttk.Scrollbar(lf); sb3.pack(side=tk.RIGHT,fill=tk.Y)
        self.m_list=tk.Listbox(lf,bg=BG3,fg=TEXT,font=FONT,selectbackground=ACCENT,selectforeground=BG,
                                bd=0,height=5,yscrollcommand=sb3.set)
        self.m_list.pack(fill=tk.X); sb3.config(command=self.m_list.yview)
        ttk.Button(ml,text="Remove selected",style="G.TButton",command=self._remove_mapping).pack(anchor="w",pady=(8,0))
        sr=card(inner,"Save as project"); sr.pack(fill=tk.X,padx=20,pady=(0,20))
        row=tk.Frame(sr,bg=BG2); row.pack(fill=tk.X)
        tk.Label(row,text="Project name:",bg=BG2,fg=MUTED,font=FONT_SM,width=14,anchor="w").pack(side=tk.LEFT)
        self.m_proj_name=tk.StringVar()
        ttk.Entry(row,textvariable=self.m_proj_name,width=28).pack(side=tk.LEFT,padx=(0,10))
        ttk.Button(row,text="Save Project",style="P.TButton",command=self._save_manual_project).pack(side=tk.LEFT)
    def _load_src_topics(self):
        if self._telegram_busy("load topics"):
            return
        sel=self.m_src_var.get()
        if not sel: messagebox.showerror("","Select a source group."); return
        grp=next((g for g in self.groups if g["name"] in sel),None)
        if not grp: return
        ai=self.api_id_var.get(); ah=self.api_hash_var.get()
        self._log_s(f"Loading topics from '{grp['name']}'...")
        threading.Thread(target=lambda:list_topics(int(ai),ah,grp["id"],self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,lambda:self._chk_topics("src"))
    def _load_dst_topics(self):
        if self._telegram_busy("load topics"):
            return
        sel=self.m_dst_var.get()
        if not sel: messagebox.showerror("","Select a destination group."); return
        grp=next((g for g in self.groups if g["name"] in sel),None)
        if not grp: return
        ai=self.api_id_var.get(); ah=self.api_hash_var.get()
        self._log_s(f"Loading topics from '{grp['name']}'...")
        threading.Thread(target=lambda:list_topics(int(ai),ah,grp["id"],self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,lambda:self._chk_topics("dst"))
    def _chk_topics(self, which):
        try:
            r=self.result_queue.get_nowait()
            if isinstance(r,list):
                if which=="src":
                    self._m_src_topics=r
                    self.m_src_topic_combo["values"]=[f"{t['title']}  (id:{t['id']})" for t in r]
                    self._log_s(f"Loaded {len(r)} source topics")
                else:
                    self._m_dst_topics=r
                    self.m_dst_topic_combo["values"]=[f"{t['title']}  (id:{t['id']})" for t in r]
                    self._log_s(f"Loaded {len(r)} dest topics")
            elif r is None:
                self._log_s("Topic load failed. If a backup is running, wait until it finishes and try again.")
        except queue.Empty: self.after(500,lambda:self._chk_topics(which))
    def _create_dest_topic(self):
        if self._telegram_busy("create a topic"):
            return
        title=self.m_new_topic_var.get().strip()
        if not title: messagebox.showerror("","Enter a topic name."); return
        sel=self.m_dst_var.get()
        if not sel: messagebox.showerror("","Select destination group first."); return
        grp=next((g for g in self.groups if g["name"] in sel),None)
        if not grp: return
        ai=self.api_id_var.get(); ah=self.api_hash_var.get()
        threading.Thread(target=lambda:create_topic_in_group(int(ai),ah,grp["id"],title,self.log_queue,self.result_queue),daemon=True).start()
        self.after(500,self._chk_new_topic)
    def _chk_new_topic(self):
        try:
            r=self.result_queue.get_nowait()
            if r:
                self._m_dst_topics.append(r)
                self.m_dst_topic_combo["values"]=[f"{t['title']}  (id:{t['id']})" for t in self._m_dst_topics]
                self.m_dst_topic_var.set(f"{r['title']}  (id:{r['id']})")
                self.m_new_topic_var.set("")
            else: messagebox.showerror("Error","Could not create topic.")
        except queue.Empty: self.after(500,self._chk_new_topic)
    def _add_mapping(self):
        sg=self.m_src_var.get(); st=self.m_src_topic_var.get()
        dg=self.m_dst_var.get(); dt=self.m_dst_topic_var.get()
        if not all([sg,st,dg,dt]): messagebox.showerror("Incomplete","Select source group, source topic, dest group and dest topic."); return
        src_grp=next((g for g in self.groups if g["name"] in sg),None)
        dst_grp=next((g for g in self.groups if g["name"] in dg),None)
        src_t=next((t for t in self._m_src_topics if t["title"] in st),None)
        dst_t=next((t for t in self._m_dst_topics if t["title"] in dt),None)
        if not all([src_grp,dst_grp,src_t,dst_t]): messagebox.showerror("Error","Reload topics and try again."); return
        self._manual_mappings.append({
            "src_group_id":src_grp["id"],"src_group_name":src_grp["name"],
            "src_topic_id":src_t["id"],"src_topic_title":src_t["title"],
            "dest_group_id":dst_grp["id"],"dest_group_name":dst_grp["name"],
            "dest_topic_id":dst_t["id"],"dest_topic_title":dst_t["title"]
        })
        self.m_list.insert(tk.END,f"  {src_grp['name']} / {src_t['title']} -> {dst_grp['name']} / {dst_t['title']}")
    def _remove_mapping(self):
        sel=self.m_list.curselection()
        if not sel: return
        self._manual_mappings.pop(sel[0]); self.m_list.delete(sel[0])
    def _save_manual_project(self):
        if not self._manual_mappings: messagebox.showerror("","Add at least one mapping."); return
        name=self.m_proj_name.get().strip()
        if not name: messagebox.showerror("","Enter a project name."); return
        safe=re.sub(r'[^a-zA-Z0-9_]','_',name)
        dst_name=self._manual_mappings[0]["dest_group_name"]
        src_names=", ".join(set(m["src_group_name"] for m in self._manual_mappings))
        self.config_data["projects"].append({
            "name":name,"mode":"manual",
            "source_name":src_names,"dest_name":dst_name,
            "mappings":self._manual_mappings.copy(),
            "state_file":f"state_{safe}.json",
            "filters":{"messages":self.mf_msg.get(),"images":self.mf_img.get(),
                       "files":self.mf_fil.get(),"skip_duplicates":self.mf_skip_dup.get()}
        })
        save_config(self.config_data); self._refresh_proj_list(); self._refresh_run_dd()
        messagebox.showinfo("Saved",f"'{name}' saved with {len(self._manual_mappings)} mapping(s).")
        self._manual_mappings.clear(); self.m_list.delete(0,tk.END); self.m_proj_name.set("")
    # -- DOWNLOAD ----------------------------------------------------------------
    def _download_tab(self, parent):
        _, inner = make_scrollable(parent)
        top = card(inner, "Download source group"); top.pack(fill=tk.X, padx=20, pady=(20,8))
        tk.Label(top, text="Choose a Telegram group, then download files into folders by group and topic.",
                 bg=BG2, fg=MUTED, font=FONT_SM).pack(anchor="w", pady=(0,8))
        name_row = tk.Frame(top, bg=BG2); name_row.pack(fill=tk.X, pady=3)
        tk.Label(name_row, text="Project name:", bg=BG2, fg=TEXT, font=FONT, width=14, anchor="w").pack(side=tk.LEFT)
        self.d_name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.d_name_var, width=36).pack(side=tk.LEFT)
        edit_row = tk.Frame(top, bg=BG2); edit_row.pack(fill=tk.X, pady=3)
        tk.Label(edit_row, text="Edit existing:", bg=BG2, fg=MUTED, font=FONT_SM, width=14, anchor="w").pack(side=tk.LEFT)
        self.d_existing_var = tk.StringVar()
        self.d_existing_combo = ttk.Combobox(edit_row, textvariable=self.d_existing_var, width=36, state="readonly")
        self.d_existing_combo.pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(edit_row, text="Load Download Project", command=self._download_load_project).pack(side=tk.LEFT)
        self._download_refresh_project_combo()
        r1 = tk.Frame(top, bg=BG2); r1.pack(fill=tk.X, pady=3)
        tk.Label(r1, text="Source group:", bg=BG2, fg=TEXT, font=FONT, width=14, anchor="w").pack(side=tk.LEFT)
        self.d_src_var = tk.StringVar()
        self.d_src_combo = ttk.Combobox(r1, textvariable=self.d_src_var, width=42, state="readonly")
        self.d_src_combo.pack(side=tk.LEFT, padx=(0,8))
        self.d_src_combo.bind("<<ComboboxSelected>>", lambda e: self._download_load_topics())
        ttk.Button(r1, text="Reload Topics", command=self._download_load_topics).pack(side=tk.LEFT)
        opts = card(inner, "What to download"); opts.pack(fill=tk.X, padx=20, pady=8)
        self.d_files_var = tk.BooleanVar(value=True)
        self.d_photos_var = tk.BooleanVar(value=False)
        self.d_videos_var = tk.BooleanVar(value=False)
        self.d_text_var = tk.BooleanVar(value=False)
        for var, label in [
            (self.d_files_var, "Files"),
            (self.d_photos_var, "Photos"),
            (self.d_videos_var, "Videos"),
            (self.d_text_var, "Text messages to messages.txt"),
        ]:
            tk.Checkbutton(opts, text=label, variable=var, bg=BG2, fg=TEXT,
                           activebackground=BG2, activeforeground=TEXT,
                           selectcolor=BG3, font=FONT_SM).pack(side=tk.LEFT, padx=(0,16))
        tk.Label(opts, text="GIFs are skipped.", bg=BG2, fg=MUTED, font=("Segoe UI",8)).pack(anchor="w", pady=(8,0))
        topics = card(inner, "Topics"); topics.pack(fill=tk.BOTH, expand=True, padx=20, pady=8)
        row = tk.Frame(topics, bg=BG2); row.pack(fill=tk.X, pady=(0,8))
        ttk.Button(row, text="Select Whole Group / All Topics", command=self._download_select_all).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(row, text="Calculate Size", style="P.TButton", command=self._download_calculate).pack(side=tk.LEFT)
        self.d_status_lbl = tk.Label(row, text="", bg=BG2, fg=MUTED, font=FONT_SM)
        self.d_status_lbl.pack(side=tk.LEFT, padx=12)
        search_row = tk.Frame(topics, bg=BG2); search_row.pack(fill=tk.X, pady=(0,8))
        tk.Label(search_row, text="Search topics:", bg=BG2, fg=MUTED, font=FONT_SM).pack(side=tk.LEFT, padx=(0,8))
        self.d_topic_search_var = tk.StringVar()
        self.d_topic_search_var.trace_add("write", lambda *_: self._download_filter_topics())
        ttk.Entry(search_row, textvariable=self.d_topic_search_var, width=36).pack(side=tk.LEFT)
        lf = tk.Frame(topics, bg=BG2); lf.pack(fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lf); sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.d_topic_list = tk.Listbox(lf, bg=BG3, fg=TEXT, font=FONT, selectbackground=ACCENT,
                                       selectforeground=BG, bd=0, height=10, selectmode=tk.EXTENDED,
                                       yscrollcommand=sb.set)
        self.d_topic_list.pack(fill=tk.BOTH, expand=True); sb.config(command=self.d_topic_list.yview)
        dest = card(inner, "Destination folder"); dest.pack(fill=tk.X, padx=20, pady=8)
        r2 = tk.Frame(dest, bg=BG2); r2.pack(fill=tk.X)
        self.d_folder_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.d_folder_var, width=58).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r2, text="Choose Folder", command=self._download_choose_folder).pack(side=tk.LEFT)
        controls = card(inner); controls.pack(fill=tk.X, padx=20, pady=(8,20))
        ttk.Button(controls, text="Save Download Project", style="P.TButton",
                   command=self._download_save_project).pack(side=tk.LEFT, padx=(0,10))
        self.d_start_btn = ttk.Button(controls, text="Save and Run", command=self._download_run_now)
        self.d_start_btn.pack(side=tk.LEFT, padx=(0,10))
        self.d_stop_btn = ttk.Button(controls, text="Stop", style="D.TButton", command=self._stop)
        self.d_stop_btn.pack(side=tk.LEFT)
        self.d_stop_btn.state(["disabled"])
        log_card = card(inner, "Download log", pady=12); log_card.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0,20))
        self.download_log = scrolledtext.ScrolledText(log_card, bg=BG, fg=TEXT, font=MONO,
                                                      insertbackground=ACCENT, bd=0, relief="flat",
                                                      padx=10, pady=8, height=10)
        self.download_log.pack(fill=tk.BOTH, expand=True)
        for tag, color in [("g", SUCCESS), ("r", DANGER), ("y", WARNING), ("d", MUTED),
                           ("a", ACCENT), ("status", "#82b1ff"), ("copied", SUCCESS)]:
            self.download_log.tag_config(tag, foreground=color)
    def _download_filters(self):
        return {
            "files": self.d_files_var.get(),
            "photos": self.d_photos_var.get(),
            "videos": self.d_videos_var.get(),
            "text": self.d_text_var.get(),
        }
    def _download_refresh_project_combo(self):
        projects = [p for p in self.config_data.get("projects", []) if p.get("mode") == "download"]
        projects.sort(key=lambda p: p.get("name", "").lower())
        self._download_display_to_name = {}
        displays = []
        for p in projects:
            display = f"{p.get('source_name','Unknown')} / {p.get('name','Unknown')}"
            self._download_display_to_name[display] = p.get("name")
            displays.append(display)
        if hasattr(self, "d_existing_combo"):
            self.d_existing_combo["values"] = displays
    def _download_load_project(self):
        selected = self.d_existing_var.get()
        name = getattr(self, "_download_display_to_name", {}).get(selected, selected)
        if not name:
            messagebox.showinfo("", "Select a download project to load."); return
        p = next((x for x in self.config_data.get("projects", []) if x.get("name") == name and x.get("mode") == "download"), None)
        if not p:
            return
        self.d_name_var.set(p.get("name", ""))
        src = next((g for g in self.groups if g.get("id") == p.get("source_id")), None)
        self.d_src_var.set(f"{src['name']}  (id: {src['id']})" if src else p.get("source_name", ""))
        self.d_folder_var.set(p.get("output_dir") or p.get("dest_name", ""))
        f = p.get("filters", {})
        self.d_files_var.set(f.get("files", True))
        self.d_photos_var.set(f.get("photos", False))
        self.d_videos_var.set(f.get("videos", False))
        self.d_text_var.set(f.get("text", False))
        self._d_topics = [dict(t) for t in p.get("topics", [])]
        selected_ids = {int(x) for x in p.get("selected_topic_ids", [])}
        self.d_topic_search_var.set("")
        self._download_filter_topics()
        for i, t in enumerate(getattr(self, "_d_topic_view", [])):
            if int(t["id"]) in selected_ids:
                self.d_topic_list.select_set(i)
        self.d_status_lbl.config(text=f"Loaded download project '{p.get('name')}'.", fg=SUCCESS)
    def _download_load_topics(self):
        if self._telegram_busy("load topics"):
            return
        src = self._group_from_selection(self.d_src_var.get())
        if not src: messagebox.showerror("", "Select a source group."); return
        self.d_status_lbl.config(text="Loading topics...", fg=WARNING)
        self._d_topics = []
        self._d_topic_view = []
        self.d_topic_list.delete(0, tk.END)
        threading.Thread(target=lambda: list_topics(
            int(self.api_id_var.get()), self.api_hash_var.get(),
            src["id"], self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, self._download_chk_topics)
    def _download_chk_topics(self):
        try:
            r = self.result_queue.get_nowait()
            if isinstance(r, list):
                self._d_topics = r
                self.d_topic_search_var.set("")
                self._download_filter_topics(select_all=True)
                self._download_select_all()
                self.d_status_lbl.config(text=f"{len(r)} topic(s) loaded.", fg=SUCCESS)
            elif r is None:
                self.d_status_lbl.config(text="Could not load topics.", fg=DANGER)
        except queue.Empty:
            self.after(500, self._download_chk_topics)
    def _download_select_all(self):
        if hasattr(self, "d_topic_list"):
            self.d_topic_list.select_set(0, tk.END)
    def _download_selected_topics(self):
        idxs = self.d_topic_list.curselection()
        view = getattr(self, "_d_topic_view", self._d_topics)
        return [view[i] for i in idxs]
    def _download_filter_topics(self, select_all=False):
        if not hasattr(self, "d_topic_list"):
            return
        query = self.d_topic_search_var.get().strip().lower() if hasattr(self, "d_topic_search_var") else ""
        previous_ids = {int(t["id"]) for t in self._download_selected_topics()} if not select_all else set()
        self._d_topic_view = [
            t for t in self._d_topics
            if not query or query in t.get("title", "").lower() or query in str(t.get("id", ""))
        ]
        self.d_topic_list.delete(0, tk.END)
        for t in self._d_topic_view:
            self.d_topic_list.insert(tk.END, self._download_topic_display(t))
        for i, t in enumerate(self._d_topic_view):
            if select_all or int(t["id"]) in previous_ids:
                self.d_topic_list.select_set(i)
        if query:
            self.d_status_lbl.config(text=f"{len(self._d_topic_view)}/{len(self._d_topics)} topic(s) shown.", fg=ACCENT)
    def _download_topic_display(self, topic):
        suffix = topic.get("_download_summary", "")
        return f"{topic['title']}  (id:{topic['id']}){suffix}"
    def _download_calculate(self):
        if self._telegram_busy("calculate download size"):
            return
        src = self._group_from_selection(self.d_src_var.get())
        selected = self._download_selected_topics()
        if not src or not selected: messagebox.showerror("", "Select a source group and at least one topic."); return
        self.d_status_lbl.config(text="Calculating...", fg=WARNING)
        self.stop_event.clear()
        threading.Thread(target=lambda: calculate_download_topics(
            int(self.api_id_var.get()), self.api_hash_var.get(), src["id"],
            self._d_topics, [t["id"] for t in selected], self._download_filters(),
            self.log_queue, self.result_queue, self.stop_event), daemon=True).start()
        self.after(500, self._download_chk_calculation)
    def _download_chk_calculation(self):
        try:
            rows = self.result_queue.get_nowait()
            if rows is None:
                self.d_status_lbl.config(text="Calculation failed.", fg=DANGER); return
            by_id = {int(r["id"]): r for r in rows}
            for t in self._d_topics:
                row = by_id.get(int(t["id"]))
                if row:
                    t["_download_summary"] = f" — {row['count']} item(s), {_fmt_bytes(row['size'])}"
            self._download_filter_topics()
            for i, t in enumerate(getattr(self, "_d_topic_view", [])):
                if int(t["id"]) in by_id:
                    self.d_topic_list.select_set(i)
            total = sum(r["size"] for r in rows)
            count = sum(r["count"] for r in rows)
            self.d_status_lbl.config(text=f"{count} item(s), {_fmt_bytes(total)} selected.", fg=SUCCESS)
        except queue.Empty:
            self.after(500, self._download_chk_calculation)
    def _download_choose_folder(self):
        folder = filedialog.askdirectory(title="Choose download folder")
        if folder:
            self.d_folder_var.set(folder)
    def _download_project_payload(self):
        src = self._group_from_selection(self.d_src_var.get())
        selected = self._download_selected_topics()
        folder = self.d_folder_var.get().strip()
        if not src or not selected: messagebox.showerror("", "Select a source group and at least one topic."); return
        if not folder: messagebox.showerror("", "Choose a destination folder."); return
        if not any(self._download_filters().values()):
            messagebox.showerror("", "Choose at least one thing to download."); return
        name = self.d_name_var.get().strip() or f"Download {src['name']}"
        return {
            "name": name,
            "mode": "download",
            "source_id": src["id"],
            "source_name": src["name"],
            "dest_name": folder,
            "output_dir": folder,
            "topics": [dict(t) for t in self._d_topics],
            "selected_topic_ids": [t["id"] for t in selected],
            "filters": self._download_filters()
        }
    def _download_save_project(self, silent=False):
        payload = self._download_project_payload()
        if not payload:
            return None
        existing = next((p for p in self.config_data.get("projects", []) if p.get("name") == payload["name"]), None)
        if existing:
            existing.update(payload)
        else:
            self.config_data.setdefault("projects", []).append(payload)
        save_config(self.config_data)
        self._refresh_run_dd()
        self._builder_refresh_project_combo()
        self._download_refresh_project_combo()
        self.d_name_var.set(payload["name"])
        if not silent:
            messagebox.showinfo("Saved", f"Download project '{payload['name']}' saved.")
        self._log_d(f"Saved download project: {payload['name']}", "g")
        return payload
    def _download_run_now(self):
        payload = self._download_save_project(silent=True)
        if not payload:
            return
        if hasattr(self, "run_proj_var"):
            self.run_proj_var.set(self._project_label(payload["name"]))
        self.nb.select(self.nb.tabs()[-1])
        self._start()
    def _start_download_project(self, p, ai, ah, filters):
        selected_topics = p.get("selected_topic_ids") or [t.get("id") for t in p.get("topics", [])]
        self.active_task = "download"
        if hasattr(self, "d_start_btn"): self.d_start_btn.state(["disabled"])
        if hasattr(self, "d_stop_btn"): self.d_stop_btn.state(["!disabled"])
        self._log_d("Starting download...", "a")
        self._log_d(f"Source: {p.get('source_name','Unknown')}", "d")
        self._log_d(f"Folder: {p.get('output_dir') or p.get('dest_name','Unknown')}", "d")
        selected_names = [t.get("title", "General") for t in p.get("topics", []) if t.get("id") in selected_topics]
        self._log_d("Topics: " + ", ".join(selected_names[:8]) + ("..." if len(selected_names) > 8 else ""), "d")
        threading.Thread(target=lambda: run_download_topics(
            int(ai), ah, p["source_id"], p.get("source_name", "Source"),
            p.get("output_dir") or p.get("dest_name"), p.get("topics", []), selected_topics, filters,
            self.log_queue, self.stop_event), daemon=True).start()
    def _download_start(self):
        payload = self._download_project_payload()
        if not payload:
            return
        if self.backup_running:
            messagebox.showwarning("Running", "A task is already running."); return
        self.stop_event.clear(); self.backup_running = True
        self.d_start_btn.state(["disabled"]); self.d_stop_btn.state(["!disabled"])
        if hasattr(self, "start_btn"): self.start_btn.state(["disabled"])
        self._start_download_project(payload, self.api_id_var.get(), self.api_hash_var.get(), payload["filters"])
    # -- CLEAN ------------------------------------------------------------------
    def _clean_tab(self, parent):
        _, inner = make_scrollable(parent)
        top = card(inner, "Clean exact duplicate files", pady=12); top.pack(fill=tk.X, padx=20, pady=(16,8))
        tk.Label(top, text="Scans the real Telegram destination group and finds duplicate files in the same topic with the same name and size. Keeps the first copy and lets you delete the extras.",
                 bg=BG2, fg=MUTED, font=FONT_SM, wraplength=850, justify=tk.LEFT).pack(anchor="w", pady=(0,8))
        r1 = tk.Frame(top, bg=BG2); r1.pack(fill=tk.X, pady=3)
        tk.Label(r1, text="Group:", bg=BG2, fg=MUTED, font=FONT_SM, width=10, anchor="w").pack(side=tk.LEFT)
        self.c_group_var = tk.StringVar()
        self.c_group_combo = ttk.Combobox(r1, textvariable=self.c_group_var, state="readonly", width=55)
        self.c_group_combo.pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r1, text="Load Topics", command=self._clean_load_topics).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(r1, text="Refresh Groups", command=self._load_groups).pack(side=tk.LEFT)
        opts = tk.Frame(top, bg=BG2); opts.pack(fill=tk.X, pady=(8,0))
        self.c_include_previews = tk.BooleanVar(value=False)
        tk.Checkbutton(opts, text="Also delete nearby preview images for duplicate files (slower, optional)", variable=self.c_include_previews,
                       bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT, selectcolor=BG3, font=FONT).pack(side=tk.LEFT)
        mid = card(inner, "Topics to scan", pady=10); mid.pack(fill=tk.X, padx=20, pady=8)
        srow = tk.Frame(mid, bg=BG2); srow.pack(fill=tk.X, pady=(0,5))
        self.c_topic_search_var = tk.StringVar()
        ttk.Entry(srow, textvariable=self.c_topic_search_var, width=38).pack(side=tk.LEFT, padx=(0,8))
        self.c_topic_search_var.trace_add("write", lambda *_: self._clean_filter_topics())
        ttk.Button(srow, text="Select All Topics", command=self._clean_select_all_topics).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(srow, text="Clear", style="G.TButton", command=lambda: self.c_topic_list.selection_clear(0, tk.END)).pack(side=tk.LEFT)
        lrow = tk.Frame(mid, bg=BG2); lrow.pack(fill=tk.X)
        tsb = ttk.Scrollbar(lrow); tsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.c_topic_list = tk.Listbox(lrow, bg=BG3, fg=TEXT, font=FONT, selectbackground=ACCENT,
                                       selectforeground=BG, bd=0, height=8, selectmode=tk.MULTIPLE,
                                       yscrollcommand=tsb.set)
        self.c_topic_list.pack(fill=tk.X); tsb.config(command=self.c_topic_list.yview)
        self.c_status_lbl = tk.Label(mid, text="", bg=BG2, fg=MUTED, font=FONT_SM)
        self.c_status_lbl.pack(anchor="w", pady=(6,0))
        actions = tk.Frame(mid, bg=BG2); actions.pack(fill=tk.X, pady=(8,0))
        ttk.Button(actions, text="Scan Selected Topics", style="P.TButton", command=self._clean_scan).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(actions, text="Delete Selected Duplicates", style="D.TButton", command=self._clean_delete_selected).pack(side=tk.LEFT, padx=(0,8))
        ttk.Button(actions, text="Select All Duplicate Results", command=self._clean_select_all_results).pack(side=tk.LEFT)
        res = card(inner, "Duplicate results", pady=10); res.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8,20))
        cols = ("topic", "file", "size", "keep", "delete", "previews")
        self.c_result_tree = ttk.Treeview(res, columns=cols, show="headings", height=12, selectmode="extended")
        for col, label, width in [
            ("topic", "Topic", 170), ("file", "Duplicate file", 300), ("size", "Size", 90),
            ("keep", "Keeping msg", 90), ("delete", "Deleting msg", 90), ("previews", "Preview images", 100)
        ]:
            self.c_result_tree.heading(col, text=label)
            self.c_result_tree.column(col, width=width, anchor="w")
        rsb = ttk.Scrollbar(res, command=self.c_result_tree.yview)
        self.c_result_tree.configure(yscrollcommand=rsb.set)
        rsb.pack(side=tk.RIGHT, fill=tk.Y); self.c_result_tree.pack(fill=tk.BOTH, expand=True)
    def _clean_group(self):
        return self._group_from_selection(self.c_group_var.get())
    def _clean_load_topics(self):
        if self._telegram_busy("load clean topics"):
            return
        grp = self._clean_group()
        if not grp:
            messagebox.showerror("", "Select a group first."); return
        self.c_status_lbl.config(text="Loading topics...", fg=ACCENT)
        threading.Thread(target=lambda: list_topics(
            int(self.api_id_var.get()), self.api_hash_var.get(), grp["id"],
            self.log_queue, self.result_queue), daemon=True).start()
        self.after(500, self._clean_chk_topics)
    def _clean_chk_topics(self):
        try:
            r = self.result_queue.get_nowait()
            if isinstance(r, dict):
                self._clean_topics = [{"id": int(k), "title": v} for k, v in r.items()]
                self._clean_filter_topics(select_all=True)
                self.c_status_lbl.config(text=f"{len(self._clean_topics)} topic(s) loaded.", fg=SUCCESS)
            else:
                self.c_status_lbl.config(text="Could not load topics.", fg=DANGER)
        except queue.Empty:
            self.after(500, self._clean_chk_topics)
    def _clean_filter_topics(self, select_all=False):
        q = self.c_topic_search_var.get().strip().lower()
        selected_ids = set()
        if not select_all and hasattr(self, "_clean_topic_view"):
            for i in self.c_topic_list.curselection():
                selected_ids.add(int(self._clean_topic_view[i]["id"]))
        self._clean_topic_view = [t for t in self._clean_topics if not q or q in t["title"].lower() or q in str(t["id"])]
        self.c_topic_list.delete(0, tk.END)
        for t in self._clean_topic_view:
            self.c_topic_list.insert(tk.END, f"{t['title']}  (id: {t['id']})")
        if select_all:
            self.c_topic_list.selection_set(0, tk.END)
        else:
            for i, t in enumerate(self._clean_topic_view):
                if int(t["id"]) in selected_ids:
                    self.c_topic_list.selection_set(i)
    def _clean_select_all_topics(self):
        self.c_topic_list.selection_set(0, tk.END)
    def _clean_selected_topic_ids(self):
        return [self._clean_topic_view[i]["id"] for i in self.c_topic_list.curselection()]
    def _clean_scan(self):
        if self._telegram_busy("scan duplicates"):
            return
        grp = self._clean_group()
        topic_ids = self._clean_selected_topic_ids()
        if not grp or not topic_ids:
            messagebox.showerror("", "Select a group and at least one topic."); return
        self._clean_dupes = []
        for item in self.c_result_tree.get_children():
            self.c_result_tree.delete(item)
        self.c_status_lbl.config(text="Scanning duplicate files...", fg=ACCENT)
        self.stop_event.clear()
        self.backup_running = True
        self.active_task = "clean"
        threading.Thread(target=lambda: scan_clean_duplicates(
            int(self.api_id_var.get()), self.api_hash_var.get(), grp["id"],
            self._clean_topics, topic_ids, self.c_include_previews.get(),
            self.log_queue, self.result_queue, self.stop_event), daemon=True).start()
        self.after(700, self._clean_chk_scan)
    def _clean_chk_scan(self):
        try:
            r = self.result_queue.get_nowait()
            self._clean_dupes = r if isinstance(r, list) else []
            for item in self.c_result_tree.get_children():
                self.c_result_tree.delete(item)
            for i, d in enumerate(self._clean_dupes):
                self.c_result_tree.insert("", tk.END, iid=str(i), values=(
                    d.get("topic_title", "Unknown"), d.get("label", "Unknown"), _fmt_bytes(d.get("size")),
                    d.get("keep_msg_id"), d.get("msg_id"), len(d.get("preview_msg_ids", []))
                ))
            self.c_status_lbl.config(text=f"{len(self._clean_dupes)} duplicate file(s) found.", fg=SUCCESS if self._clean_dupes else MUTED)
            self.backup_running = False
            self.active_task = None
        except queue.Empty:
            self.after(700, self._clean_chk_scan)
    def _clean_select_all_results(self):
        self.c_result_tree.selection_set(self.c_result_tree.get_children())
    def _clean_delete_selected(self):
        grp = self._clean_group()
        selected = [int(i) for i in self.c_result_tree.selection()]
        if not grp or not selected:
            messagebox.showerror("", "Select duplicate results to delete."); return
        msg_ids = []
        preview_count = 0
        for i in selected:
            d = self._clean_dupes[i]
            msg_ids.extend(d.get("delete_msg_ids") or [d.get("msg_id")])
            preview_count += len(d.get("preview_msg_ids", []))
        msg_ids = sorted(set(int(x) for x in msg_ids if x))
        if not messagebox.askyesno("Delete duplicates",
            f"Delete {len(selected)} duplicate file message(s) and {preview_count} nearby preview image(s)-\n\nThis cannot be undone."):
            return
        self.c_status_lbl.config(text="Deleting selected duplicates...", fg=WARNING)
        self.stop_event.clear()
        self.backup_running = True
        self.active_task = "clean"
        threading.Thread(target=lambda: delete_clean_duplicates(
            int(self.api_id_var.get()), self.api_hash_var.get(), grp["id"], msg_ids,
            self.log_queue, self.result_queue, self.stop_event), daemon=True).start()
        self.after(700, self._clean_chk_delete)
    def _clean_chk_delete(self):
        try:
            ok = self.result_queue.get_nowait()
            self.c_status_lbl.config(text="Delete complete. Scan again to refresh results." if ok else "Delete failed.", fg=SUCCESS if ok else DANGER)
            self.backup_running = False
            self.active_task = None
        except queue.Empty:
            self.after(700, self._clean_chk_delete)
    # -- RUN --------------------------------------------------------------------
    def _run_tab(self, parent):
        _, inner = make_scrollable(parent)
        top=card(inner,"Run project",pady=10); top.pack(fill=tk.X,padx=20,pady=(12,2))
        r1=tk.Frame(top,bg=BG2); r1.pack(fill=tk.X,pady=1)
        tk.Label(r1,text="Project:",bg=BG2,fg=MUTED,font=FONT_SM,width=10,anchor="w").pack(side=tk.LEFT)
        self.run_proj_var=tk.StringVar()
        self.run_proj_dd=ttk.Combobox(r1,textvariable=self.run_proj_var,width=42,state="readonly"); self.run_proj_dd.pack(side=tk.LEFT,padx=(0,10))
        self._refresh_run_dd()
        r2=tk.Frame(top,bg=BG2); r2.pack(fill=tk.X,pady=(6,0))
        self.start_btn=ttk.Button(r2,text="Start Project",style="P.TButton",command=self._start); self.start_btn.pack(side=tk.LEFT)
        self.run_scan_dest_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="Deep duplicate check", variable=self.run_scan_dest_var,
                       bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT,
                       selectcolor=BG3, font=FONT_SM).pack(side=tk.LEFT, padx=(10,10))
        self.run_convert_images_var = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="Convert image files to photos (slower)", variable=self.run_convert_images_var,
                       bg=BG2, fg=TEXT, activebackground=BG2, activeforeground=TEXT,
                       selectcolor=BG3, font=FONT_SM).pack(side=tk.LEFT, padx=(0,10))
        self.repair_links_btn=ttk.Button(r2,text="Repair Missing Links",command=self._repair_missing_links); self.repair_links_btn.pack(side=tk.LEFT,padx=(0,8))
        self.repair_files_btn=ttk.Button(r2,text="Repair Missing Files",command=self._repair_missing_files); self.repair_files_btn.pack(side=tk.LEFT,padx=(0,10))
        self.stop_btn=ttk.Button(r2,text="Stop",style="D.TButton",command=self._stop); self.stop_btn.pack(side=tk.LEFT,padx=10)
        self.stop_btn.state(["disabled"])
        self.prog_lbl=tk.Label(inner,text="",bg=BG,fg=SUCCESS,font=FONT_B)
        tk.Label(top,text="Deep duplicate check scans destination topics for files added outside the app. First scan builds a shared file index and can be slow; later scans only check new messages.",
                 bg=BG2,fg=MUTED,font=FONT_SM).pack(anchor="w",pady=(6,0))
        tk.Label(top,text="Convert image files to photos downloads and reuploads image-documents, so it is slower but avoids sending them as files.",
                 bg=BG2,fg=MUTED,font=FONT_SM).pack(anchor="w",pady=(2,0))
        qc=card(inner,"Activity queue",pady=8); qc.pack(fill=tk.X,padx=20,pady=(2,0))
        tk.Label(qc,text="The running project stays on top. Add waiting projects below it; they run one after another.",
                 bg=BG2,fg=MUTED,font=FONT_SM).pack(anchor="w",pady=(0,4))
        qrow=tk.Frame(qc,bg=BG2); qrow.pack(fill=tk.X)
        ttk.Button(qrow,text="Add Selected to Queue",command=self._queue_add_selected).pack(side=tk.LEFT,padx=(0,8))
        ttk.Button(qrow,text="Move Up",command=lambda:self._queue_move(-1)).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Move Down",command=lambda:self._queue_move(1)).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Remove",style="G.TButton",command=self._queue_remove_selected).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Skip Current",style="G.TButton",command=self._queue_skip_current).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Requeue Current",style="G.TButton",command=self._queue_requeue_current).pack(side=tk.LEFT,padx=(0,6))
        ttk.Button(qrow,text="Clear Queue",style="G.TButton",command=self._queue_clear).pack(side=tk.LEFT)
        qlf=tk.Frame(qc,bg=BG2); qlf.pack(fill=tk.X,pady=(5,0))
        qsb=ttk.Scrollbar(qlf); qsb.pack(side=tk.RIGHT,fill=tk.Y)
        self.queue_list=tk.Listbox(qlf,bg=BG3,fg=TEXT,font=FONT,selectbackground=ACCENT,selectforeground=BG,
                                   bd=0,height=4,yscrollcommand=qsb.set)
        self.queue_list.pack(fill=tk.X); qsb.config(command=self.queue_list.yview)
        lc=card(inner,"Live log",pady=10); lc.pack(fill=tk.BOTH,expand=True,padx=20,pady=(4,20))
        self.run_log=scrolledtext.ScrolledText(lc,bg=BG,fg=TEXT,font=MONO,insertbackground=ACCENT,bd=0,relief="flat",padx=10,pady=8,height=34)
        self.run_log.pack(fill=tk.BOTH,expand=True)
        self.run_log.tag_config("g", foreground=SUCCESS)
        self.run_log.tag_config("r", foreground=DANGER)
        self.run_log.tag_config("y", foreground=WARNING)
        self.run_log.tag_config("d", foreground=MUTED)
        self.run_log.tag_config("a", foreground=ACCENT)
        self.run_log.tag_config("w", foreground="#ff9800")   # orange for retries
        self.run_log.tag_config("status",foreground="#ffffff",font=("Consolas",9,"bold"))
        self.run_log.tag_config("copied",foreground=SUCCESS)
        self._queue_refresh()
    def _refresh_run_dd(self):
        self._run_display_to_name = {}
        displays = []
        for p in self._visible_projects():
            label = self._project_label(p["name"])
            self._run_display_to_name[label] = p["name"]
            displays.append(label)
        self.run_proj_dd["values"]=displays
        if displays:
            self.run_proj_dd.current(0)
        else:
            self.run_proj_var.set("")
        self._update_run_button_text()
    def _run_selected_project_name(self):
        selected = self.run_proj_var.get()
        return getattr(self, "_run_display_to_name", {}).get(selected, selected)
    def _visible_projects(self):
        return list(self.config_data.get("projects", []))
    def _project_kind(self, project):
        if project.get("mode") == "download":
            return "Download"
        return "Copy"
    def _project_label(self, name):
        p = next((x for x in self.config_data.get("projects", []) if x.get("name") == name), None)
        if not p:
            return name
        return f"{self._project_kind(p)}: {name}"
    def _queue_refresh(self):
        if not hasattr(self, "queue_list"):
            return
        valid = {p.get("name") for p in self._visible_projects()}
        cleaned = [name for name in self.activity_queue if name in valid]
        if cleaned != self.activity_queue:
            self.activity_queue = cleaned
            self._queue_save()
        self.queue_list.delete(0, tk.END)
        if self.current_project_name:
            self.queue_list.insert(tk.END, f"Running: {self._project_label(self.current_project_name)}")
        for i, name in enumerate(self.activity_queue, 1):
            self.queue_list.insert(tk.END, f"Waiting {i}. {self._project_label(name)}")
        self._update_run_button_text()
    def _update_run_button_text(self):
        if not hasattr(self, "start_btn"):
            return
        self.start_btn.config(text="Add to Queue" if self.backup_running else "Start Project")
    def _queue_save(self):
        self.config_data["activity_queue"] = self.activity_queue
        save_config(self.config_data)
    def _queue_add_project(self, name):
        if not name:
            messagebox.showerror("", "Select a project first."); return
        self.activity_queue.append(name)
        self.queue_running = True
        self._queue_save()
        self._queue_refresh()
        self._log_r(f"Queued: {self._project_label(name)}", "a")
    def _queue_add_selected(self):
        name = self._run_selected_project_name()
        self._queue_add_project(name)
    def _queue_waiting_index_from_selection(self):
        if not hasattr(self, "queue_list"):
            return None
        sel = self.queue_list.curselection()
        if not sel:
            return None
        idx = sel[0]
        if self.current_project_name:
            idx -= 1
        if idx < 0 or idx >= len(self.activity_queue):
            return None
        return idx
    def _queue_move(self, direction):
        idx = self._queue_waiting_index_from_selection()
        if idx is None:
            messagebox.showinfo("", "Select a waiting queue item to move."); return
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self.activity_queue):
            return
        self.activity_queue[idx], self.activity_queue[new_idx] = self.activity_queue[new_idx], self.activity_queue[idx]
        self._queue_save()
        self._queue_refresh()
        select_idx = new_idx + (1 if self.current_project_name else 0)
        self.queue_list.select_set(select_idx)
    def _queue_remove_selected(self):
        idx = self._queue_waiting_index_from_selection()
        if idx is None:
            messagebox.showinfo("", "Select a waiting queue item to remove."); return
        removed = self.activity_queue.pop(idx)
        self._queue_save()
        self._queue_refresh()
        self._log_r(f"Removed from queue: {self._project_label(removed)}", "d")
    def _queue_skip_current(self):
        if not self.backup_running or not self.current_project_name:
            messagebox.showinfo("", "No project is currently running."); return
        self.skip_current_for_queue = True
        self.queue_running = bool(self.activity_queue)
        self.stop_event.set()
        self._queue_refresh()
        self._log_r(f"Skipping current: {self._project_label(self.current_project_name)}", "y")
    def _queue_requeue_current(self):
        if not self.backup_running or not self.current_project_name:
            messagebox.showinfo("", "No project is currently running."); return
        self.activity_queue.append(self.current_project_name)
        self._queue_save()
        self.skip_current_for_queue = True
        self.queue_running = True
        self.stop_event.set()
        self._queue_refresh()
        self._log_r(f"Moved current to end: {self._project_label(self.current_project_name)}", "y")
    def _queue_clear(self):
        if self.backup_running and self.queue_running:
            if not messagebox.askyesno("Clear queue", "Clear waiting items- The current task will keep running."):
                return
        self.activity_queue.clear()
        self._queue_save()
        self.queue_running = False
        self._queue_refresh()
        self._log_r("Queue cleared.", "d")
    def _queue_start(self):
        if self.backup_running:
            if self.activity_queue:
                self.queue_running = True
                self._queue_refresh()
                self._log_r("Queue will continue after the current task.", "a")
            else:
                messagebox.showinfo("", "Add waiting projects to the queue first.")
            return
        if not self.activity_queue:
            messagebox.showinfo("", "Add at least one project to the queue."); return
        self.queue_running = True
        self._queue_run_next()
    def _queue_run_next(self):
        if self.backup_running:
            return
        if not self.queue_running or not self.activity_queue:
            self.queue_running = False
            self._log_r("Queue complete.", "g")
            return
        name = self.activity_queue.pop(0)
        self._queue_save()
        self._queue_refresh()
        self._log_r(f"Queue starting: {self._project_label(name)}", "a")
        self.stop_event.clear()
        self._start(project_name=name, from_queue=True)
    def _routes_for_run(self, project):
        dest_name = project.get("dest_name") or "Unknown"
        routes = []
        for route in project.get("routes", []):
            r = dict(route)
            if not r.get("dest_group_name"):
                r["dest_group_name"] = dest_name
            routes.append(r)
        return routes
    def _start(self, project_name=None, from_queue=False):
        sel=project_name or self._run_selected_project_name()
        if not sel: messagebox.showerror("","Select a project."); return
        p=next((x for x in self.config_data["projects"] if x["name"]==sel),None)
        if not p: return
        ai=self.config_data.get("api_id",""); ah=self.config_data.get("api_hash","")
        if not ai or not ah: messagebox.showerror("","Complete Setup first."); return
        filters=dict(p.get("filters",{"messages":True,"images":True,"files":True,"skip_duplicates":True}))
        filters["scan_destination"] = bool(getattr(self, "run_scan_dest_var", tk.BooleanVar(value=False)).get())
        filters["convert_image_files"] = bool(getattr(self, "run_convert_images_var", tk.BooleanVar(value=False)).get())
        if self.backup_running:
            if not from_queue:
                self._queue_add_project(sel)
            return
        self.stop_event.clear(); self.backup_running=True
        self.queue_running = bool(self.activity_queue) or from_queue
        mode=p.get("mode","auto")
        self.active_task = "download" if mode == "download" else "backup"
        self.current_project_name = p.get("name")
        self._queue_refresh()
        self.start_btn.state(["!disabled"]); self.stop_btn.state(["!disabled"])
        if hasattr(self, "repair_links_btn"): self.repair_links_btn.state(["disabled"])
        if hasattr(self, "repair_files_btn"): self.repair_files_btn.state(["disabled"])
        if hasattr(self, "d_start_btn"): self.d_start_btn.state(["disabled"])
        self._log_r(f"Starting {self._project_kind(p).lower()}: {p['name']}","a")
        if mode=="download":
            self._log_r(f"  {p.get('source_name','Unknown')} -> {p.get('output_dir') or p.get('dest_name','Unknown')}\n","d")
            self._start_download_project(p, ai, ah, filters)
        elif mode=="auto":
            self._log_r(f"  {p.get('source_name','Unknown')} -> {p.get('dest_name','Unknown')}\n","d")
            threading.Thread(target=lambda:run_backup_auto(
                int(ai),ah,p["source_id"],p["dest_id"],p["state_file"],
                self.log_queue,self.stop_event,filters),daemon=True).start()
        elif mode=="routes":
            routes=self._routes_for_run(p)
            self._log_r(f"  {len(routes)} route(s) via Project Builder\n","d")
            threading.Thread(target=lambda:run_backup_routes(
                int(ai),ah,routes,p["state_file"],
                self.log_queue,self.stop_event,filters),daemon=True).start()
        else:
            self._log_r(f"  {len(p.get('mappings',[]))} mapping(s)\n","d")
            threading.Thread(target=lambda:run_backup_manual(
                int(ai),ah,p["mappings"],p["state_file"],
                self.log_queue,self.stop_event,filters),daemon=True).start()
    def _repair_missing_links(self):
        sel = self._run_selected_project_name()
        if not sel:
            messagebox.showerror("", "Select a project."); return
        p = next((x for x in self.config_data["projects"] if x["name"] == sel), None)
        if not p:
            return
        if p.get("mode") != "routes":
            messagebox.showinfo("", "Repair Missing Links works with Project Builder copy projects.")
            return
        ai = self.config_data.get("api_id", ""); ah = self.config_data.get("api_hash", "")
        if not ai or not ah:
            messagebox.showerror("", "Complete Setup first."); return
        if self.backup_running:
            messagebox.showwarning("Running", "A task is still running.\nWait for it to stop before starting another.")
            return
        if not messagebox.askyesno(
            "Repair Missing Links",
            "This will look for older copied posts that missed Telegram button links, delete the old copy when it can be matched safely, and upload it again with the link.\n\nContinue-"
        ):
            return
        self.stop_event.clear(); self.backup_running = True
        self.active_task = "backup"
        self.current_project_name = p.get("name")
        self._queue_refresh()
        self.start_btn.state(["disabled"])
        if hasattr(self, "repair_links_btn"): self.repair_links_btn.state(["disabled"])
        if hasattr(self, "repair_files_btn"): self.repair_files_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        filters = p.get("filters", {"messages": True, "images": True, "files": True, "skip_duplicates": True})
        self._log_r(f"Starting repair missing links: {p['name']}", "a")
        routes = self._routes_for_run(p)
        threading.Thread(target=lambda: run_backup_routes(
            int(ai), ah, routes, p["state_file"],
            self.log_queue, self.stop_event, filters, repair_links=True), daemon=True).start()
    def _repair_missing_files(self):
        sel = self._run_selected_project_name()
        if not sel:
            messagebox.showerror("", "Select a project."); return
        p = next((x for x in self.config_data["projects"] if x["name"] == sel), None)
        if not p:
            return
        if p.get("mode") != "routes":
            messagebox.showinfo("", "Repair Missing Files works with Project Builder copy projects.")
            return
        ai = self.config_data.get("api_id", ""); ah = self.config_data.get("api_hash", "")
        if not ai or not ah:
            messagebox.showerror("", "Complete Setup first."); return
        if self.backup_running:
            messagebox.showwarning("Running", "A task is still running.\nWait for it to stop before starting another.")
            return
        if not messagebox.askyesno(
            "Repair Missing Files",
            "This scans old source history and the live destination topics, then copies only files missing from the destination.\n\n"
            "It does not change normal project progress, but it can take a long time.\n\nContinue?"
        ):
            return
        self.stop_event.clear(); self.backup_running = True
        self.active_task = "backup"
        self.current_project_name = p.get("name")
        self._queue_refresh()
        self.start_btn.state(["disabled"])
        if hasattr(self, "repair_links_btn"): self.repair_links_btn.state(["disabled"])
        if hasattr(self, "repair_files_btn"): self.repair_files_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        filters = dict(p.get("filters", {"messages": True, "images": True, "files": True, "skip_duplicates": True}))
        filters["scan_destination"] = False
        filters["convert_image_files"] = bool(getattr(self, "run_convert_images_var", tk.BooleanVar(value=False)).get())
        self._log_r(f"Starting repair missing files: {p['name']}", "a")
        routes = self._routes_for_run(p)
        threading.Thread(target=lambda: run_backup_routes(
            int(ai), ah, routes, p["state_file"],
            self.log_queue, self.stop_event, filters, repair_missing_files=True), daemon=True).start()
    def _stop(self):
        self.queue_running = False
        self.stop_event.set(); self._log_r("Stop requested. Queue paused.","r")
        if self.active_task == "download":
            self._log_d("Stop requested...", "r")
    def _log_should_follow(self, widget):
        try:
            return widget.yview()[1] >= 0.98
        except Exception:
            return True
    def _log_finish(self, widget, should_follow):
        if should_follow:
            widget.see(tk.END)
    def _set_progress(self, text):
        if not hasattr(self, "prog_lbl"):
            return
        self.prog_lbl.config(text=text)
        if text and not self.prog_lbl.winfo_manager():
            self.prog_lbl.pack(anchor="w", padx=22, pady=(2,0))
    def _log_r(self,msg,tag=""):
        follow = self._log_should_follow(self.run_log)
        self.run_log.insert(tk.END,msg+"\n",tag)
        self._log_finish(self.run_log, follow)
    def _log_d(self, msg, tag=""):
        if hasattr(self, "download_log"):
            follow = self._log_should_follow(self.download_log)
            self.download_log.insert(tk.END, msg + "\n", tag)
            self._log_finish(self.download_log, follow)
    def _log_download_live_status(self, msg):
        if not hasattr(self, "download_log"):
            return
        follow = self._log_should_follow(self.download_log)
        if not hasattr(self, "_download_live_mark"):
            self._download_live_mark = "download_live_status"
        mark = self._download_live_mark
        try:
            self.download_log.index(mark)
            line_start = self.download_log.index(f"{mark} linestart")
            self.download_log.delete(line_start, f"{line_start} lineend+1c")
            self.download_log.mark_set(mark, line_start)
            self.download_log.mark_gravity(mark, tk.LEFT)
            self.download_log.insert(mark, "STATUS " + msg + "\n", "status")
        except tk.TclError:
            self.download_log.mark_set(mark, tk.END)
            self.download_log.mark_gravity(mark, tk.LEFT)
            self.download_log.insert(tk.END, "STATUS " + msg + "\n", "status")
        if follow:
            self.download_log.see(mark)
    def _clear_download_live_status(self):
        if hasattr(self, "download_log") and hasattr(self, "_download_live_mark"):
            try:
                self.download_log.delete(self._download_live_mark, f"{self._download_live_mark} lineend+1c")
            except tk.TclError:
                pass
    def _log_download_progress(self, msg):
        if msg.startswith("OK "):
            msg = msg[3:]
        if msg.startswith("ERROR "):
            msg = msg[6:]
        m = re.match(
            r"Download progress: \[(?P<label>.+?)\] (?P<topic>\d+/\d+ this topic \([^)]+\)) \| "
            r"(?P<skipped>\d+ skipped), (?P<errors>\d+ errors) \| "
            r"(?P<run>\d+/\d+ this download \([^)]+\))$",
            msg
        )
        if not m:
            self._log_d("OK " + msg, "g")
            return
        skipped_n = int(m.group("skipped").split()[0])
        errors_n = int(m.group("errors").split()[0])
        parts = [
            ("Download: ", "g"),
            ("[", "d"),
            (m.group("label"), "a"),
            ("] ", "d"),
            (m.group("topic"), "status"),
            (" | ", "d"),
            (m.group("skipped"), "y" if skipped_n else "d"),
            (", ", "d"),
            (m.group("errors"), "r" if errors_n else "d"),
            (" | ", "d"),
            (m.group("run"), "copied"),
            ("\n", ""),
        ]
        if hasattr(self, "download_log"):
            follow = self._log_should_follow(self.download_log)
            for text, tag in parts:
                self.download_log.insert(tk.END, text, tag)
            self._log_finish(self.download_log, follow)
    def _compact_progress_label(self, label):
        compact = label.replace(" / General (no topics)", "")
        compact = compact.replace(" -> ", " -> ")
        compact = re.sub(r"\s+-GROUP COPY-\s*", " copy", compact)
        compact = re.sub(r"\s+", " ", compact).strip()
        if len(compact) <= 76:
            return compact
        parts = compact.split(" -> ", 1)
        if len(parts) == 2:
            left, right = parts
            if " / " in left:
                left_group, left_topic = left.split(" / ", 1)
                left = left_group if left_topic == "General" else f"{left_group}/{left_topic}"
            if " / " in right:
                right_group, right_topic = right.split(" / ", 1)
                right = right_topic if right_group == "Archive STL" else f"{right_group}/{right_topic}"
            compact = f"{left} -> {right}"
        if len(compact) > 76:
            compact = compact[:73].rstrip() + "..."
        return compact
    def _log_progress_r(self, msg):
        if msg.startswith("OK "):
            msg = msg[3:]
        if msg.startswith("ERROR "):
            msg = msg[6:]
        m = re.match(
            r"Progress: \[(?P<label>.+?)\] (?P<topic>\d+/\d+ this topic \([^)]+\)) \| "
            r"(?P<skipped>\d+ skipped), (?P<errors>\d+ errors) \| "
            r"(?P<run>\d+/\d+ this run \([^)]+\))$",
            msg
        )
        if not m:
            self._log_r("OK " + msg, "g")
            return
        skipped_n = int(m.group("skipped").split()[0])
        errors_n = int(m.group("errors").split()[0])
        parts = [
            ("Progress: ", "g"),
            ("[", "d"),
            (self._compact_progress_label(m.group("label")), "a"),
            ("] ", "d"),
            (m.group("topic"), "status"),
            (" | ", "d"),
            (m.group("skipped"), "y" if skipped_n else "d"),
            (", ", "d"),
            (m.group("errors"), "r" if errors_n else "d"),
            (" | ", "d"),
            (m.group("run"), "copied"),
            ("\n", ""),
        ]
        follow = self._log_should_follow(self.run_log)
        for text, tag in parts:
            self.run_log.insert(tk.END, text, tag)
        self._log_finish(self.run_log, follow)
    # -- Poll -------------------------------------------------------------------
    def _poll(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if msg == "__DONE__":
                    was_download = self.active_task == "download"
                    should_continue_queue = self.queue_running and (not self.stop_event.is_set() or self.skip_current_for_queue)
                    self.backup_running = False
                    self.active_task = None
                    self.current_project_name = None
                    self.skip_current_for_queue = False
                    self._queue_refresh()
                    if hasattr(self, "start_btn"): self.start_btn.state(["!disabled"])
                    if hasattr(self, "stop_btn"): self.stop_btn.state(["disabled"])
                    if hasattr(self, "repair_links_btn"): self.repair_links_btn.state(["!disabled"])
                    if hasattr(self, "repair_files_btn"): self.repair_files_btn.state(["!disabled"])
                    if hasattr(self, "d_start_btn"): self.d_start_btn.state(["!disabled"])
                    if hasattr(self, "d_stop_btn"): self.d_stop_btn.state(["disabled"])
                    self._log_r("-" * 50, "d")
                    if was_download:
                        self._clear_download_live_status()
                        self._log_d("-" * 50, "d")
                    if should_continue_queue:
                        self.after(500, self._queue_run_next)
                elif msg.startswith("Download progress:") or msg.startswith("OK Download progress:"):
                    self._log_download_progress(msg)
                    self._set_progress(msg)
                elif msg.startswith("DOWNLOAD_STATUS "):
                    clean = msg[len("DOWNLOAD_STATUS "):]
                    if hasattr(self, "d_status_lbl"):
                        self.d_status_lbl.config(text=clean, fg=ACCENT)
                    self._log_download_live_status(clean)
                    self._set_progress(clean)
                elif any(x in msg for x in ("Download destination:", "Download pending:", "Download items this run:", "Download size:", "Counting download items")):
                    self._log_d(msg, "d")
                    self._log_r(msg, "d")
                elif msg.startswith("Downloading file:"):
                    self._log_d("Downloading " + msg, "status")
                    self._log_r("Downloading " + msg, "status")
                elif msg.startswith("Downloaded file:") or msg.startswith("Saving text:"):
                    if msg.startswith("Downloaded file:"):
                        self._clear_download_live_status()
                    self._log_d("OK " + msg, "g")
                    self._log_r("OK " + msg, "g")
                elif any(x in msg for x in ("Downloading topic:", "Done topic:", "Download complete:")):
                    tag = "g" if "complete" in msg or "Done topic" in msg else "a"
                    self._log_d(("OK " if tag == "g" else "START ") + msg, tag)
                    self._log_r(("OK " if tag == "g" else "START ") + msg, tag)
                elif "Progress:" in msg:
                    self._log_progress_r(msg); self._set_progress(msg)
                elif msg.startswith("SUMMARY "):
                    self._log_r("OK " + msg, "status"); self._set_progress(msg[8:])
                elif msg.startswith(("Repair files complete.", "Repair complete.", "Repair links done:", "Repair files done:")):
                    tag = "y" if re.search(r"[1-9]\d* errors", msg) else "g"
                    self._log_r("OK " + msg, tag); self._set_progress(msg)
                elif msg.startswith(("Repair files: copied", "Repair links: fixed")):
                    self._log_r("OK " + msg, "g")
                elif msg.startswith(("Repair files progress:", "Repair files: destination has")):
                    self._log_r("STATUS " + msg, "status"); self._set_progress(msg)
                elif msg.startswith(("Repair files: rebuilding", "Repair files: scanning", "Repair links:", "Clean scan:")):
                    self._log_r(msg, "a")
                elif msg.startswith(("  scanned", "Clean scan topic:")):
                    self._log_r(msg, "d")
                elif any(x in msg for x in ("ERROR","FATAL","PermissionError","protected")):
                    shown = msg if msg.startswith(("ERROR ", "FATAL ")) else "ERROR " + msg
                    self._log_r(shown, "r"); self._log_s(msg, "err")
                    if self.active_task == "download": self._log_d(shown, "r")
                elif msg.startswith("STATUS "):
                    clean = msg[7:]  # strip "STATUS "
                    self._log_r("STATUS " + clean, "g"); self._set_progress(clean)
                elif "Skipping duplicate" in msg:
                    self._log_r("WARN " + msg, "y")
                elif "WARNING" in msg:
                    self._log_r("WARN " + msg, "y")
                elif any(x in msg for x in ("Done.", "complete", "All mappings")):
                    self._log_r("OK " + msg, "g"); self._set_progress(msg)
                elif any(x in msg for x in ("Flood wait","flood")):
                    self._log_r("WARN " + msg, "y")
                elif any(x in msg for x in ("Logged in","Connected")):
                    self._log_r("OK " + msg, "g"); self._log_s(msg, "ok")
                elif any(x in msg for x in ("Stopped","Stop")):
                    self._log_r("STOP " + msg, "r")
                elif any(x in msg for x in ("Starting ","--","route","Route")):
                    self._log_r(msg, "a")
                elif any(x in msg for x in ("Topics ready","Syncing","Counting","Resuming","fresh","dest id")):
                    self._log_r(msg, "d")
                elif any(x in msg for x in ("Copying topic","Creating topic")):
                    self._log_r("START " + msg, "a")
                elif any(x in msg for x in ("retry","Request error","Connection")):
                    self._log_r("WARN " + msg, "y")
                else:
                    self._log_r(msg); self._log_s(msg)
        except queue.Empty:
            pass
        self.after(300, self._poll)
if __name__=="__main__":
    app=App(); app.mainloop()

