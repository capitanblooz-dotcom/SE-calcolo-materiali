#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Autore:  Capitan Blooz <capitanblooz@gmail.com>
# Copyright (c) 2026 Capitan Blooz
# Licenza: MIT (open source) - vedi LICENSE.txt.
# Il software e' fornito "cosi' com'e'", senza alcuna garanzia; l'autore non e'
# responsabile di eventuali danni derivanti dal suo uso.
# Space Engineers e' un marchio di Keen Software House; progetto non affiliato.
"""
se_gui.py - Interfaccia grafica per se_calc.py (Space Engineers 1).

Permette di scegliere:
  - la sorgente delle definizioni: solo gioco base (vanilla) oppure un mondo salvato
    (con le sue mod e la sua efficienza dell'assemblatore);
  - il file della nave (bp.sbc);
e mostra blocchi, componenti, lingotti e minerali necessari.

Avvio:  python se_gui.py      (oppure doppio clic su se_gui.pyw / se_gui.py)
"""

__author__ = "Capitan Blooz"
__email__ = "capitanblooz@gmail.com"
__license__ = "MIT"
__copyright__ = "Copyright (c) 2026 Capitan Blooz"


import json
import os
import queue
import re
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import se_calc as sc

APP_TITLE = "SE - Calcolo materiali nave"
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "se_gui_settings.json")
VANILLA = "vanilla"
WORLD = "world"
SERVER = "server"

# Parametri del mondo di test: (gruppo, [(chiave, etichetta, tipo)]); tipo = "num", "bool" o lista di valori
TEST_FIELDS = [
    ("Generale", [
        ("GameMode", "Modalità di gioco", ["Creative", "Survival"]),
        ("ExperimentalMode", "Modalità sperimentale", "bool"),
        ("EnableIngameScripts", "Script (blocco programmabile)", "bool"),
        ("EnableCopyPaste", "Copia/incolla blueprint", "bool"),
        ("EnableSpectator", "Telecamera spettatore", "bool"),
        ("Enable3rdPersonView", "Vista in terza persona", "bool"),
        ("WeaponsEnabled", "Armi abilitate", "bool"),
    ]),
    ("Produzione e inventari", [
        ("AssemblerEfficiencyMultiplier", "Efficienza assemblatore ×", "num"),
        ("AssemblerSpeedMultiplier", "Velocità assemblatore ×", "num"),
        ("RefinerySpeedMultiplier", "Velocità raffineria ×", "num"),
        ("InventorySizeMultiplier", "Inventario personaggio ×", "num"),
        ("BlocksInventorySizeMultiplier", "Inventario blocchi ×", "num"),
        ("WelderSpeedMultiplier", "Velocità saldatore ×", "num"),
        ("GrinderSpeedMultiplier", "Velocità smerigliatrice ×", "num"),
    ]),
    ("Limiti", [
        ("TotalPCU", "PCU totali", "num"),
        ("BlockLimitsEnabled", "Limiti blocchi", ["NONE", "GLOBALLY", "PER_FACTION", "PER_PLAYER"]),
        ("MaxGridSize", "Blocchi max per griglia (0 = ∞)", "num"),
        ("MaxBlocksPerPlayer", "Blocchi max per giocatore (0 = ∞)", "num"),
        ("WorldSizeKm", "Dimensione mondo km (0 = ∞)", "num"),
        ("HackSpeedMultiplier", "Velocità hacking ×", "num"),
    ]),
    ("Simulazione", [
        ("EnvironmentHostility", "Meteoriti", ["SAFE", "NORMAL", "CATACLYSM", "CATACLYSM_UNREAL"]),
        ("ThrusterDamage", "Danno dei propulsori", "bool"),
        ("DestructibleBlocks", "Blocchi distruttibili", "bool"),
        ("EnableOxygen", "Ossigeno", "bool"),
        ("EnableOxygenPressurization", "Pressurizzazione", "bool"),
        ("EnableSunRotation", "Rotazione del sole", "bool"),
        ("ViewDistance", "Distanza visiva (m)", "num"),
        ("SyncDistance", "Distanza di sincronizzazione (m)", "num"),
    ]),
]
# valori vanilla per i campi che il modello del gioco non riporta
TEST_FALLBACK = {"BlocksInventorySizeMultiplier": "1", "SyncDistance": "3000", "GameMode": "Creative",
                 "ExperimentalMode": "true", "EnableSpectator": "false"}
DEFAULT_TEMPLATE = "Empty World"

REFINERY_PRESETS = [
    ("Raffineria (x1)", 1.0),
    ("Altoforno / raffineria base (x0,7)", 0.7),
]


def nfmt(x, dec=2):
    """Numero in formato italiano: 1.234,56 (interi senza decimali)."""
    if abs(x - round(x)) < 1e-6:
        return f"{int(round(x)):,}".replace(",", ".")
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pfloat(s, default):
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return default


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x820")
        self.minsize(900, 600)

        self.cfg = self._load_settings()
        self.worlds = []
        self.blueprints = []
        self.world_info = None           # dict letto da read_world
        self.db_cache = {}               # chiave -> (db, info)
        self.last = None                 # ultimo risultato, per CSV
        self.queue = queue.Queue()
        self.busy = False

        self.var_mode = tk.StringVar(value=self.cfg.get("mode", VANILLA))
        self.var_world = tk.StringVar()
        self.var_ship = tk.StringVar(value=self.cfg.get("ship", ""))
        self.var_game = tk.StringVar(value=self.cfg.get("game", sc.find_game() or ""))
        self.var_mods = tk.StringVar(value=self.cfg.get("mods_dir", sc.first_existing(sc.DEFAULT_MODS_DIRS) or ""))
        self.var_assembler = tk.StringVar(value=self.cfg.get("assembler", "1"))
        self.var_refinery = tk.StringVar(value=self.cfg.get("refinery", REFINERY_PRESETS[0][0]))
        self.var_count = tk.StringVar(value=self.cfg.get("count", "1"))
        self.var_auto = tk.BooleanVar(value=self.cfg.get("auto_mods", True))
        self.var_extra = tk.StringVar(value=self.cfg.get("extra_mods", ""))
        self.var_world_info = tk.StringVar(value="")
        self.servers = []                # server trovati nei log del gioco
        self.var_server = tk.StringVar()
        self.templates = []
        self.var_status = tk.StringVar(value=f"Pronto.   ·   di {__author__} ({__email__}) - licenza MIT")

        self._build()
        self._refresh_worlds(select=self.cfg.get("world"))
        self._refresh_servers(select=self.cfg.get("server"))
        self._refresh_blueprints()
        self._on_mode()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

    # ------------------------------------------------------------------ impostazioni
    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        w = self._selected_world()
        data = {
            "mode": self.var_mode.get(), "world": w["path"] if w else self.cfg.get("world"),
            "ship": self.var_ship.get(), "game": self.var_game.get(), "mods_dir": self.var_mods.get(),
            "assembler": self.var_assembler.get(), "refinery": self.var_refinery.get(),
            "count": self.var_count.get(), "auto_mods": self.var_auto.get(), "extra_mods": self.var_extra.get(),
            "server": (self._selected_server() or {}).get("name", self.cfg.get("server")),
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def _on_close(self):
        self._save_settings()
        self.destroy()

    # ------------------------------------------------------------------ layout
    def _build(self):
        pad = {"padx": 6, "pady": 3}
        bar = ttk.Frame(self, padding=(8, 0, 8, 6))
        bar.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self.progress.pack(side="right")
        ttk.Label(bar, textvariable=self.var_status).pack(side="left")

        self.main_nb = ttk.Notebook(self)
        self.main_nb.pack(fill="both", expand=True, padx=4, pady=(6, 2))
        calc = ttk.Frame(self.main_nb)
        self.main_nb.add(calc, text="  Calcolo materiali  ")
        test = ttk.Frame(self.main_nb, padding=8)
        self.main_nb.add(test, text="  Mondo di test locale (da server)  ")

        top = ttk.Frame(calc, padding=8)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)

        # --- sorgente definizioni
        src = ttk.LabelFrame(top, text="1. Definizioni di gioco", padding=6)
        src.grid(row=0, column=0, columnspan=3, sticky="ew", **pad)
        src.columnconfigure(2, weight=1)
        ttk.Radiobutton(src, text="Vanilla (solo gioco base, nessuna mod)", value=VANILLA,
                        variable=self.var_mode, command=self._on_mode).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(src, text="Mondo salvato:", value=WORLD,
                        variable=self.var_mode, command=self._on_mode).grid(row=1, column=0, sticky="w")
        self.cb_world = ttk.Combobox(src, textvariable=self.var_world, state="readonly", height=25)
        self.cb_world.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        self.cb_world.bind("<<ComboboxSelected>>", lambda e: self._on_world())
        self.btn_world = ttk.Button(src, text="Sfoglia...", command=self._browse_world)
        self.btn_world.grid(row=1, column=3, **pad)
        self.btn_world_r = ttk.Button(src, text="Aggiorna", command=lambda: self._refresh_worlds())
        self.btn_world_r.grid(row=1, column=4, **pad)
        ttk.Radiobutton(src, text="Server online:", value=SERVER,
                        variable=self.var_mode, command=self._on_mode).grid(row=2, column=0, sticky="w")
        self.cb_server = ttk.Combobox(src, textvariable=self.var_server, state="readonly", height=15)
        self.cb_server.grid(row=2, column=1, columnspan=2, sticky="ew", **pad)
        self.cb_server.bind("<<ComboboxSelected>>", lambda e: self._on_server())
        self.btn_server_r = ttk.Button(src, text="Aggiorna", command=lambda: self._refresh_servers())
        self.btn_server_r.grid(row=2, column=4, **pad)
        ttk.Label(src, text="Solo per il CALCOLO: usa le mod del server lette dai log del gioco. "
                            "Per provare le navi usa la scheda «Mondo di test locale».",
                  foreground="#a05a00").grid(row=3, column=1, columnspan=4, sticky="w", padx=6)
        ttk.Label(src, textvariable=self.var_world_info, foreground="#555").grid(
            row=4, column=1, columnspan=4, sticky="w", padx=6)
        self.chk_auto = ttk.Checkbutton(src, text="Cerca tra le mod installate i blocchi non trovati",
                                        variable=self.var_auto)
        self.chk_auto.grid(row=5, column=1, columnspan=2, sticky="w", padx=6)
        ttk.Label(src, text="Mod aggiuntive (ID):").grid(row=6, column=0, sticky="w")
        self.ent_extra = ttk.Entry(src, textvariable=self.var_extra)
        self.ent_extra.grid(row=6, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Label(src, text="separate da spazio/virgola", foreground="#555").grid(row=6, column=3, columnspan=2, sticky="w")

        # --- nave
        ship = ttk.LabelFrame(top, text="2. Nave (blueprint bp.sbc)", padding=6)
        ship.grid(row=1, column=0, columnspan=3, sticky="ew", **pad)
        ship.columnconfigure(1, weight=1)
        ttk.Label(ship, text="Blueprint:").grid(row=0, column=0, sticky="w")
        self.cb_ship = ttk.Combobox(ship, textvariable=self.var_ship, height=25)
        self.cb_ship.grid(row=0, column=1, sticky="ew", **pad)
        self.cb_ship.bind("<<ComboboxSelected>>", lambda e: self._on_ship_pick())
        ttk.Button(ship, text="Sfoglia...", command=self._browse_ship).grid(row=0, column=2, **pad)
        ttk.Label(ship, text="Numero di navi:").grid(row=0, column=3, sticky="e")
        ttk.Spinbox(ship, from_=1, to=1000, width=6, textvariable=self.var_count).grid(row=0, column=4, **pad)

        # --- parametri
        par = ttk.LabelFrame(top, text="3. Parametri", padding=6)
        par.grid(row=2, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(par, text="Efficienza assemblatore:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(par, textvariable=self.var_assembler, values=["1", "3", "10"], width=6).grid(row=0, column=1, **pad)
        ttk.Label(par, text="Resa raffinazione:").grid(row=0, column=2, sticky="w", padx=(18, 0))
        ttk.Combobox(par, textvariable=self.var_refinery, width=34,
                     values=[p[0] for p in REFINERY_PRESETS]).grid(row=0, column=3, **pad)
        ttk.Label(par, text="(o scrivi un numero, es. 1,2)", foreground="#555").grid(row=0, column=4, sticky="w")
        self.btn_calc = ttk.Button(par, text="Calcola", command=self._start)
        self.btn_calc.grid(row=0, column=5, padx=(30, 6))
        self.btn_csv = ttk.Button(par, text="Esporta CSV...", command=self._export, state="disabled")
        self.btn_csv.grid(row=0, column=6, **pad)
        ttk.Button(par, text="Info", width=6, command=self._about).grid(row=0, column=7, **pad)

        # --- percorsi (riga piccola)
        paths = ttk.Frame(top)
        paths.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        paths.columnconfigure(1, weight=1)
        paths.columnconfigure(4, weight=1)
        ttk.Label(paths, text="Gioco:").grid(row=0, column=0)
        ttk.Entry(paths, textvariable=self.var_game).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(paths, text="...", width=3, command=lambda: self._browse_dir(self.var_game)).grid(row=0, column=2)
        ttk.Label(paths, text="Mod workshop:").grid(row=0, column=3, padx=(12, 0))
        ttk.Entry(paths, textvariable=self.var_mods).grid(row=0, column=4, sticky="ew", padx=4)
        ttk.Button(paths, text="...", width=3, command=lambda: self._browse_dir(self.var_mods)).grid(row=0, column=5)

        # --- risultati
        self.nb = ttk.Notebook(calc)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.txt_summary = self._text_tab("Riepilogo")
        self.tv_blocks = self._tree_tab("Blocchi", [("name", "Blocco", 260), ("id", "Tipo / Sottotipo", 300),
                                                    ("qty", "Quantità", 90), ("src", "Origine", 260)])
        self.tv_comps = self._tree_tab("Componenti", [("name", "Componente", 220), ("qty", "Pezzi", 80),
                                                      ("unit", "Lingotti per pezzo", 360), ("tot", "Lingotti totali", 330)])
        self.tv_ingots = self._tree_tab("Lingotti", [("name", "Lingotto", 220), ("qty", "kg", 110),
                                                     ("unit", "Minerale per 1 kg", 260), ("ore", "Minerale necessario", 300)])
        self.tv_ores = self._tree_tab("Minerali", [("name", "Minerale", 240), ("qty", "kg", 120),
                                                   ("direct", "di cui usati direttamente", 180), ("id", "Sottotipo", 200)])
        self.txt_warn = self._text_tab("Avvisi")
        self.txt_log = self._text_tab("Log")

        self._build_test(test)

    def _tree_tab(self, title, cols):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text=title)
        tv = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings")
        for key, head, width in cols:
            numeric = key in ("qty", "direct")
            tv.heading(key, text=head, command=lambda k=key, t=tv: self._sort(t, k))
            tv.column(key, width=width, anchor="e" if numeric else "w", stretch=True)
        sb = ttk.Scrollbar(f, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tv._sortnum = {}         # iid -> {col: valore numerico}
        tv._sortdir = {}
        return tv

    def _text_tab(self, title):
        f = ttk.Frame(self.nb)
        self.nb.add(f, text=title)
        t = tk.Text(f, wrap="word", font=("Consolas", 10), state="disabled")
        sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return t

    # ------------------------------------------------------------------ mondo
    def _refresh_worlds(self, select=None):
        cur = select or (self._selected_world() or {}).get("path")
        self.worlds = sc.find_worlds()
        labels = [f"{w['name']}   —   {time.strftime('%d/%m/%Y %H:%M', time.localtime(w['mtime']))}"
                  for w in self.worlds]
        self.cb_world["values"] = labels
        idx = next((i for i, w in enumerate(self.worlds) if w["path"] == cur), None)
        if idx is None and cur and os.path.isdir(cur):          # mondo scelto con "Sfoglia"
            self.worlds.insert(0, {"name": os.path.basename(cur), "path": cur, "user": "", "mtime": 0})
            labels.insert(0, f"{os.path.basename(cur)}   —   {cur}")
            self.cb_world["values"] = labels
            idx = 0
        if idx is not None:
            self.cb_world.current(idx)
            if self.var_mode.get() == WORLD:
                self._on_world()

    def _selected_world(self):
        i = self.cb_world.current() if hasattr(self, "cb_world") else -1
        return self.worlds[i] if 0 <= i < len(self.worlds) else None

    def _browse_world(self):
        d = filedialog.askdirectory(title="Cartella del salvataggio (contiene Sandbox.sbc)",
                                    initialdir=os.path.join(sc.SE_APPDATA, "Saves"))
        if not d:
            return
        d = os.path.normpath(d)
        if not any(os.path.isfile(os.path.join(d, f)) for f in ("Sandbox_config.sbc", "Sandbox.sbc")):
            messagebox.showerror(APP_TITLE, "In questa cartella non c'è Sandbox.sbc / Sandbox_config.sbc.")
            return
        self.var_mode.set(WORLD)
        self._refresh_worlds(select=d)
        self._on_mode()

    def _on_world(self):
        w = self._selected_world()
        self.world_info = None
        if not w:
            self.var_world_info.set("")
            return
        try:
            info = sc.read_world(w["path"], log=None)
        except Exception as e:
            self.var_world_info.set(f"Errore: {e}")
            return
        mods_dir = self.var_mods.get()
        missing = [m for m in info["mods"] if sc.mod_root(m["id"], mods_dir, m["local_name"]) is None]
        self.world_info = info
        self.var_assembler.set(nfmt(info["assembler_eff"]))
        txt = (f"Modalità {info['game_mode'] or '?'} · assemblatore x{nfmt(info['assembler_eff'])} "
               f"· {len(info['mods'])} mod")
        if missing:
            txt += f" ({len(missing)} non installate)"
        self.var_world_info.set(txt)

    def _on_mode(self):
        mode = self.var_mode.get()
        world, server = mode == WORLD, mode == SERVER
        self.cb_world.configure(state="readonly" if world else "disabled")
        for w in (self.btn_world, self.btn_world_r):
            w.configure(state="normal" if world else "disabled")
        self.cb_server.configure(state="readonly" if server else "disabled")
        self.btn_server_r.configure(state="normal" if server else "disabled")
        for w in (self.chk_auto, self.ent_extra):
            w.configure(state="normal" if (world or server) else "disabled")
        if world:
            if self._selected_world() is None and self.worlds:
                self.cb_world.current(0)
            self._on_world()
        elif server:
            if self._selected_server() is None and self.servers:
                self.cb_server.current(0)
            self._on_server()
        else:
            self.var_world_info.set("Solo definizioni del gioco base: i blocchi delle mod risulteranno sconosciuti.")
            self.world_info = None
            self.var_assembler.set(self.cfg.get("assembler_vanilla", "1"))

    # ------------------------------------------------------------------ server (dai log)
    def _refresh_servers(self, select=None):
        cur = select or (self._selected_server() or {}).get("name")
        self.servers = sc.find_servers_from_logs()
        labels = [f"{s['name']}   —   {len(s['mods'])} mod, ultimo accesso {s['time']}" for s in self.servers]
        for cb in (self.cb_server, getattr(self, "cb_tserver", None)):
            if cb is not None:
                cb["values"] = labels
        idx = next((i for i, s in enumerate(self.servers) if s["name"] == cur), 0 if self.servers else None)
        if idx is not None:
            self.cb_server.current(idx)
            if hasattr(self, "cb_tserver"):
                self.cb_tserver.current(idx)
                self._on_tserver()
        if self.var_mode.get() == SERVER:
            self._on_server()

    def _selected_server(self, cb=None):
        cb = cb or getattr(self, "cb_server", None)
        i = cb.current() if cb is not None else -1
        return self.servers[i] if 0 <= i < len(self.servers) else None

    def _on_server(self):
        s = self._selected_server()
        self.world_info = None
        if not s:
            self.var_world_info.set("Nessun server nei log: entra almeno una volta nel server con il gioco, "
                                    "poi premi Aggiorna.")
            return
        missing = [m for m in s["mods"] if sc.mod_root(m["id"], self.var_mods.get(), m["local_name"]) is None]
        self.world_info = sc.server_as_world(s)
        txt = f"{len(s['mods'])} mod ({s['connection']})"
        if missing:
            txt += f", {len(missing)} non installate"
        txt += " · l'efficienza assemblatore del server non è nei log: impostala in «3. Parametri»"
        self.var_world_info.set(txt)

    def _build_test(self, root):
        pad = {"padx": 6, "pady": 3}
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # --- 1. server
        f1 = ttk.LabelFrame(root, text="1. Server di origine (mod lette dai log del gioco)", padding=6)
        f1.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)
        f1.columnconfigure(1, weight=1)
        ttk.Label(f1, text="Server:").grid(row=0, column=0, sticky="w")
        self.cb_tserver = ttk.Combobox(f1, state="readonly", height=15)
        self.cb_tserver.grid(row=0, column=1, sticky="ew", **pad)
        self.cb_tserver.bind("<<ComboboxSelected>>", lambda e: self._on_tserver())
        ttk.Button(f1, text="Aggiorna", command=lambda: self._refresh_servers(
            select=(self._selected_server(self.cb_tserver) or {}).get("name"))).grid(row=0, column=2, **pad)
        ttk.Label(f1, foreground="#a05a00", wraplength=1050, justify="left", text=(
            "Il gioco non salva sul PC la mappa dei server: il mondo di test avrà le STESSE MOD (stesso ordine) e i "
            "parametri che imposti qui sotto, ma sarà vuoto (niente pianeti, basi o navi del server). "
            "I parametri del server non sono nei log: copiali dalla schermata informazioni del server, "
            "altrimenti restano i valori vanilla.")).grid(row=1, column=0, columnspan=3, sticky="w", padx=6)

        # --- 2. nuovo mondo
        f2 = ttk.LabelFrame(root, text="2. Nuovo mondo locale", padding=6)
        f2.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)
        f2.columnconfigure(1, weight=1)
        f2.columnconfigure(4, weight=1)
        self.var_tname = tk.StringVar()
        self.var_ttemplate = tk.StringVar()
        self.var_tsaves = tk.StringVar(value=sc.default_saves_dir() or "")
        ttk.Label(f2, text="Nome:").grid(row=0, column=0, sticky="w")
        ttk.Entry(f2, textvariable=self.var_tname).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Label(f2, text="Modello del gioco:").grid(row=0, column=3, sticky="e", padx=(12, 0))
        self.cb_ttemplate = ttk.Combobox(f2, textvariable=self.var_ttemplate, state="readonly", width=24)
        self.cb_ttemplate.grid(row=0, column=4, sticky="w", **pad)
        self.cb_ttemplate.bind("<<ComboboxSelected>>", lambda e: self._detect_player())
        ttk.Label(f2, text="Salva in:").grid(row=1, column=0, sticky="w")
        ttk.Entry(f2, textvariable=self.var_tsaves).grid(row=1, column=1, columnspan=4, sticky="ew", **pad)
        ttk.Button(f2, text="...", width=3, command=lambda: self._browse_dir(self.var_tsaves)).grid(row=1, column=5)
        self.var_tsuit = tk.BooleanVar(value=True)
        self.var_tplayer = tk.StringVar()
        ttk.Checkbutton(f2, text="Entra subito con la tuta (crea il tuo personaggio)",
                        variable=self.var_tsuit).grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(f2, textvariable=self.var_tplayer, foreground="#555").grid(
            row=2, column=2, columnspan=4, sticky="w", **pad)
        self.var_tsaves.trace_add("write", lambda *a: self._detect_player())

        # --- 3. parametri
        f3 = ttk.LabelFrame(root, text="3. Parametri del server (predefiniti: valori vanilla del gioco)", padding=6)
        f3.grid(row=2, column=0, sticky="nsew", **pad)
        self.tvars = {}
        for gi, (gname, fields) in enumerate(TEST_FIELDS):
            g = ttk.LabelFrame(f3, text=gname, padding=4)
            g.grid(row=gi // 2, column=gi % 2, sticky="nsew", padx=4, pady=2)
            for r, (key, label, kind) in enumerate(fields):
                ttk.Label(g, text=label).grid(row=r, column=0, sticky="w", pady=1)
                v = tk.StringVar()
                self.tvars[key] = v
                if kind == "num":
                    w = ttk.Entry(g, textvariable=v, width=12, justify="right")
                else:
                    w = ttk.Combobox(g, textvariable=v, state="readonly", width=17,
                                     values=["true", "false"] if kind == "bool" else kind)
                w.grid(row=r, column=1, sticky="e", padx=(8, 0), pady=1)
        bottom = ttk.Frame(f3)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        bottom.columnconfigure(1, weight=1)
        ttk.Label(bottom, text="Altre impostazioni\n(Nome=valore, una per riga):").grid(row=0, column=0, sticky="nw")
        self.txt_textra = tk.Text(bottom, height=3, width=40, font=("Consolas", 9))
        self.txt_textra.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(bottom, text="Ripristina valori vanilla", command=self._reset_test_params).grid(
            row=0, column=2, sticky="n")

        # --- 4. mod
        f4 = ttk.LabelFrame(root, text="4. Mod da includere", padding=6)
        f4.grid(row=2, column=1, sticky="nsew", **pad)
        f4.rowconfigure(0, weight=1)
        f4.columnconfigure(0, weight=1)
        self.lb_tmods = tk.Listbox(f4, selectmode="multiple", width=46, exportselection=False, activestyle="none")
        self.lb_tmods.grid(row=0, column=0, columnspan=3, sticky="nsew")
        sb = ttk.Scrollbar(f4, orient="vertical", command=self.lb_tmods.yview)
        sb.grid(row=0, column=3, sticky="ns")
        self.lb_tmods.configure(yscrollcommand=sb.set)
        self.lb_tmods.bind("<<ListboxSelect>>", lambda e: self._update_tmods_count())
        ttk.Button(f4, text="Tutte", command=lambda: (self.lb_tmods.select_set(0, "end"),
                                                      self._update_tmods_count())).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Button(f4, text="Nessuna", command=lambda: (self.lb_tmods.select_clear(0, "end"),
                                                        self._update_tmods_count())).grid(row=1, column=1, sticky="w", pady=4)
        self.var_tmods_count = tk.StringVar()
        ttk.Label(f4, textvariable=self.var_tmods_count, foreground="#555").grid(row=1, column=2, sticky="e")
        ttk.Label(f4, foreground="#555", wraplength=330, justify="left", text=(
            "Selezionate = incluse (clic per togliere/rimettere). Puoi togliere le mod che servono "
            "solo al server, es. TorchServerMod o quelle che generano NPC.")).grid(row=2, column=0, columnspan=4, sticky="w")

        # --- azioni
        act = ttk.Frame(root)
        act.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)
        ttk.Button(act, text="Crea mondo di test", command=self._create_test_world).pack(side="left")
        ttk.Button(act, text="Apri cartella salvataggi", command=lambda: self._open_dir(self.var_tsaves.get())).pack(
            side="left", padx=8)
        ttk.Label(act, foreground="#555", text="Non modifica né sovrascrive mai i salvataggi esistenti: "
                                               "crea sempre una cartella nuova.").pack(side="left", padx=8)

        self._load_templates()
        self._reset_test_params()
        self._detect_player()

    def _detect_player(self):
        self.tplayer = sc.find_local_player(self.var_tsaves.get().strip())
        p = self.tplayer
        if p.get("hashed_id") or p.get("steam_id"):
            tdir = self._template_dir() if hasattr(self, "cb_ttemplate") else None
            if tdir and sc.template_has_player_character(tdir):
                where = "usi il personaggio del modello · nessun avviso all'apertura"
            else:
                where = ("personaggio creato nel punto iniziale del modello · alla 1ª apertura il gioco "
                         "avvisa che converte il mondo (innocuo)")
            self.var_tplayer.set(f"giocatore: {(p.get('name') or 'Engineer').lstrip(chr(0xE030))}  ·  {where}")
        else:
            self.var_tplayer.set("giocatore non riconosciuto: scegli la cartella Saves\\<tuo SteamID>")

    def _load_templates(self):
        data_dir = sc.find_game(self.var_game.get().strip() or None)
        self.templates = sc.find_templates(data_dir) if data_dir else []
        names = [n for n, _ in self.templates]
        self.cb_ttemplate["values"] = names
        if names:
            self.var_ttemplate.set(DEFAULT_TEMPLATE if DEFAULT_TEMPLATE in names else names[0])

    def _template_dir(self):
        return dict(self.templates).get(self.var_ttemplate.get())

    def _reset_test_params(self):
        tdir = self._template_dir()
        vals = sc.read_template_settings(tdir) if tdir else {}
        for key, v in self.tvars.items():
            v.set(vals.get(key, TEST_FALLBACK.get(key, "")))
        self.txt_textra.delete("1.0", "end")

    def _on_tserver(self):
        s = self._selected_server(self.cb_tserver)
        self.lb_tmods.delete(0, "end")
        if not s:
            self.var_tmods_count.set("")
            return
        self.var_tname.set(f"{s['name']} - TEST")
        mods_dir = self.var_mods.get()
        for m in s["mods"]:
            miss = sc.mod_root(m["id"], mods_dir, m["local_name"]) is None
            self.lb_tmods.insert("end", f"{m['name']}  [{m['id']}]" + ("  (NON INSTALLATA)" if miss else ""))
        self.lb_tmods.select_set(0, "end")
        self._update_tmods_count()

    def _update_tmods_count(self):
        n = self.lb_tmods.size()
        self.var_tmods_count.set(f"{len(self.lb_tmods.curselection())} / {n} incluse" if n else "")

    def _open_dir(self, path):
        if path and os.path.isdir(path):
            os.startfile(path)

    def _create_test_world(self):
        s = self._selected_server(self.cb_tserver)
        tdir = self._template_dir()
        saves = self.var_tsaves.get().strip()
        name = self.var_tname.get().strip()
        if not tdir:
            messagebox.showerror(APP_TITLE, "Non trovo i modelli di mondo del gioco: controlla la cartella del gioco.")
            return
        if not name:
            messagebox.showerror(APP_TITLE, "Scrivi il nome del nuovo mondo.")
            return
        if not saves or not os.path.isdir(saves):
            messagebox.showerror(APP_TITLE, "La cartella dei salvataggi non esiste.")
            return
        settings = {}
        for _, fields in TEST_FIELDS:
            for key, label, kind in fields:
                v = self.tvars[key].get().strip()
                if v == "":
                    continue
                if kind == "num":
                    try:
                        float(v.replace(",", "."))
                    except ValueError:
                        messagebox.showerror(APP_TITLE, f"Valore non numerico per «{label}»: {v}")
                        return
                    v = v.replace(",", ".")
                settings[key] = v
        for line in self.txt_textra.get("1.0", "end").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                messagebox.showerror(APP_TITLE, f"Riga non valida in «Altre impostazioni» (manca '='):\n{line}")
                return
            k, v = (x.strip() for x in line.split("=", 1))
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", k):
                messagebox.showerror(APP_TITLE, f"Nome di impostazione non valido: {k}")
                return
            settings[k] = v
        settings["OnlineMode"] = "OFFLINE"
        mods = [s["mods"][i] for i in self.lb_tmods.curselection()] if s else []
        dest = os.path.join(saves, sc.safe_folder_name(name))
        if os.path.exists(dest):
            messagebox.showerror(APP_TITLE, f"Esiste già un salvataggio con questo nome:\n{dest}\n\n"
                                            "Scegli un altro nome (il salvataggio esistente non viene toccato).")
            return
        if not messagebox.askyesno(APP_TITLE, (
                f"Creo il mondo «{name}»\n\nin: {dest}\nmodello: {self.var_ttemplate.get()}\n"
                f"mod incluse: {len(mods)}{' (dal server ' + s['name'] + ')' if s else ''}\n"
                f"modalità: {settings.get('GameMode', '?')}, assemblatore x{settings.get('AssemblerEfficiencyMultiplier', '?')}\n"
                f"entra con la tuta: {'sì' if self.var_tsuit.get() else 'no (schermata di respawn)'}\n\n"
                "Procedo?")):
            return
        player = None
        if self.var_tsuit.get():
            p = dict(self.tplayer or {})
            src = os.path.join(dict(self.templates).get(DEFAULT_TEMPLATE, ""), "SANDBOX_0_0_0_.sbs")
            if not (p.get("hashed_id") or p.get("steam_id")) or not os.path.isfile(src):
                messagebox.showerror(APP_TITLE, "Non riesco a riconoscere il tuo giocatore (o manca il modello "
                                                "Empty World): togli la spunta «Entra subito con la tuta» oppure "
                                                "scegli la cartella Saves\\<tuo SteamID>.")
                return
            p["char_source"] = src
            player = p
        try:
            dest = sc.create_test_world(tdir, saves, name, settings, mods, log=lambda m: None, player=player)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Creazione non riuscita:\n{e}")
            return
        self._refresh_worlds()
        self.var_status.set(f"Mondo di test creato: {dest}")
        if messagebox.askyesno(APP_TITLE, (
                f"Mondo creato:\n{dest}\n\n"
                "Per usarlo: avvia Space Engineers → Carica partita → «" + name + "».\n"
                "Al primo avvio il gioco carica le mod" + (" e parti già in tuta" if player else "") +
                ". Poi incolla la nave dal menu blueprint (F10).\n\n"
                "Il mondo compare anche in «Mondo salvato» nella scheda Calcolo materiali.\n\n"
                "Aprire la cartella del mondo?")):
            self._open_dir(dest)

    # ------------------------------------------------------------------ nave
    def _refresh_blueprints(self):
        self.blueprints = sc.find_local_blueprints()
        self.cb_ship["values"] = [self._bp_label(b) for b in self.blueprints]

    @staticmethod
    def _bp_label(b):
        return f"[{b['kind']}] {b['name']}"

    def _on_ship_pick(self):
        i = self.cb_ship.current()
        if 0 <= i < len(self.blueprints):
            self.var_ship.set(self.blueprints[i]["path"])

    def _browse_ship(self):
        start = os.path.join(sc.SE_APPDATA, "Blueprints", "local")
        cur = self.var_ship.get()
        if cur and os.path.exists(cur):
            start = os.path.dirname(cur)
        p = filedialog.askopenfilename(title="Scegli il file della nave", initialdir=start,
                                       filetypes=[("Blueprint Space Engineers", "bp.sbc *.sbc"), ("Tutti i file", "*.*")])
        if p:
            self.var_ship.set(os.path.normpath(p))

    def _browse_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or None)
        if d:
            var.set(os.path.normpath(d))

    # ------------------------------------------------------------------ calcolo (thread)
    def _start(self):
        if self.busy:
            return
        ship = self.var_ship.get().strip()
        if not ship or not os.path.exists(ship):
            messagebox.showerror(APP_TITLE, "Scegli il file della nave (bp.sbc).")
            return
        data_dir = sc.find_game(self.var_game.get().strip() or None)
        if not data_dir:
            messagebox.showerror(APP_TITLE, "Non trovo i file del gioco: imposta la cartella di Space Engineers.")
            return
        mode = self.var_mode.get()
        world = None
        if mode in (WORLD, SERVER):
            if not self.world_info:
                self._on_world() if mode == WORLD else self._on_server()
            world = self.world_info
            if not world:
                messagebox.showerror(APP_TITLE, "Scegli un mondo/server valido oppure usa la modalità Vanilla.")
                return
        ref_label = self.var_refinery.get()
        refinery = dict(REFINERY_PRESETS).get(ref_label)
        if refinery is None:
            refinery = pfloat(ref_label, 1.0)
        params = {
            "ship": ship, "data_dir": data_dir, "mods_dir": self.var_mods.get().strip(), "world": world,
            "auto": mode in (WORLD, SERVER) and self.var_auto.get(),
            "extra": [m for m in self.var_extra.get().replace(",", " ").split() if m] if mode in (WORLD, SERVER) else [],
            "assembler": max(pfloat(self.var_assembler.get(), 1.0), 0.0001),
            "refinery": max(refinery, 0.0001),
            "count": max(int(pfloat(self.var_count.get(), 1)), 1),
        }
        if mode == VANILLA:
            self.cfg["assembler_vanilla"] = self.var_assembler.get()
        self._save_settings()
        self.busy = True
        self.btn_calc.configure(state="disabled")
        self.progress.start(12)
        self._set_text(self.txt_log, "")
        self.var_status.set("Calcolo in corso...")
        threading.Thread(target=self._worker, args=(params,), daemon=True).start()

    def _worker(self, p):
        log = lambda m: self.queue.put(("log", m))
        try:
            ships, blocks = sc.read_ships([p["ship"]], p["count"], log)
            world = p["world"]
            key = (p["data_dir"], p["mods_dir"], world["path"] if world else None,
                   tuple(m["id"] for m in world["mods"]) if world else (), tuple(p["extra"]),
                   p["auto"], frozenset(blocks) if p["auto"] else None)
            if key in self.db_cache:
                db, info = self.db_cache[key]
                log("Definizioni già caricate (cache).")
            else:
                db, info = sc.build_database(p["data_dir"], p["mods_dir"], blocks, world, p["extra"], p["auto"], log=log)
                self.db_cache = {key: (db, info)}       # tengo solo l'ultima
            res = sc.compute(db, blocks, p["assembler"], p["refinery"])
            self.queue.put(("done", {"db": db, "info": info, "ships": ships, "blocks": blocks, "res": res, "p": p}))
        except Exception as e:
            log(traceback.format_exc())
            self.queue.put(("error", str(e)))

    def _poll(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._append(self.txt_log, payload + "\n")
                    self.var_status.set(payload[:120])
                elif kind == "done":
                    self._finish()
                    self._show(payload)
                elif kind == "error":
                    self._finish()
                    self.var_status.set("Errore.")
                    messagebox.showerror(APP_TITLE, payload)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _finish(self):
        self.busy = False
        self.progress.stop()
        self.btn_calc.configure(state="normal")

    # ------------------------------------------------------------------ risultati
    def _show(self, r):
        self.last = r
        db, res, blocks, p = r["db"], r["res"], r["blocks"], r["p"]
        disp = db.display

        def mix(d, mult=1.0, kinds=("Ingot", "Ore", "?")):
            parts = []
            for k, v in sorted(d.items(), key=lambda kv: -kv[1]):
                if k[0] not in kinds:
                    continue
                name = disp(k[0], k[1]) if k[0] != "?" else f"{k[2]} (senza ricetta)"
                parts.append(f"{name} {nfmt(v * mult)}")
            return ", ".join(parts)

        # Blocchi
        rows = []
        for k, n in sorted(blocks.items(), key=lambda kv: -kv[1]):
            d = db.find_block(*k)
            src = "SCONOSCIUTO (non conteggiato)" if d is None else ("gioco base" if d["source"] == "vanilla" else d["source"])
            rows.append(((db.block_display(*k), f"{k[0]} / {k[1]}", nfmt(n), src), {"qty": n}))
        self._fill(self.tv_blocks, rows)

        # Componenti
        rows = []
        for c, q in sorted(res["components"].items(), key=lambda kv: -kv[1]):
            u = res["comp_unit"].get(c, {})
            rows.append(((disp("Component", c), nfmt(q), mix(u) + " kg", mix(u, q) + " kg"), {"qty": q}))
        self._fill(self.tv_comps, rows)

        # Lingotti
        rows = []
        for s, q in sorted(res["ingots"].items(), key=lambda kv: -kv[1]):
            u = res["ingot_unit"].get(s, {})
            rows.append(((disp("Ingot", s), nfmt(q), mix(u) + " kg", mix(u, q) + " kg"), {"qty": q}))
        self._fill(self.tv_ingots, rows)

        # Minerali
        rows = []
        for s, q in sorted(res["ores"].items(), key=lambda kv: -kv[1]):
            dq = res["ores_direct"].get(s, 0)
            rows.append(((disp("Ore", s), nfmt(q), nfmt(dq) if dq else "", s), {"qty": q, "direct": dq}))
        self._fill(self.tv_ores, rows)

        # Riepilogo
        w = p["world"]
        L = []
        L.append("NAVE")
        for s in r["ships"]:
            L.append(f"  {s['name']}")
            L.append(f"  file: {s['path']}")
            for g, size, n in s["grids"]:
                L.append(f"    griglia '{g}' [{size}]: {n} blocchi")
        if p["count"] > 1:
            L.append(f"  quantità: {p['count']} navi")
        L.append("")
        L.append("DEFINIZIONI")
        if w:
            kind = "Server (mod dai log del gioco, solo calcolo)" if w["path"].startswith("server ") else "Mondo"
            L.append(f"  {kind}: {w['name']}  ({w['path']})")
            L.append(f"  Mod caricate: {len(r['info']['world_mods_loaded'])} / {len(w['mods'])}")
        else:
            L.append("  Vanilla: solo gioco base")
        if r["info"]["auto_mods"]:
            L.append(f"  Mod trovate automaticamente: {', '.join(r['info']['auto_mods'])}")
        L.append(f"  Efficienza assemblatore x{nfmt(p['assembler'])} · resa raffinazione x{nfmt(p['refinery'])}")
        L.append("")
        nb = sum(blocks.values())
        nunk = sum(res["unknown_blocks"].values())
        L.append("TOTALI")
        L.append(f"  Blocchi:     {nfmt(nb)}" + (f"   ({nfmt(nunk)} sconosciuti, non conteggiati)" if nunk else ""))
        L.append(f"  Componenti:  {nfmt(sum(res['components'].values()))} pezzi, {len(res['components'])} tipi")
        L.append(f"  Lingotti:    {nfmt(sum(res['ingots'].values()))} kg")
        L.append(f"  Minerali:    {nfmt(sum(res['ores'].values()))} kg")
        for title, data, typ, unit in (("COMPONENTI", res["components"], "Component", " pz"),
                                       ("LINGOTTI", res["ingots"], "Ingot", " kg"),
                                       ("MINERALI", res["ores"], "Ore", " kg")):
            L.append("")
            L.append(title)
            for s, q in sorted(data.items(), key=lambda kv: -kv[1]):
                L.append(f"  {disp(typ, s):<38}{nfmt(q):>14}{unit}")
        self._set_text(self.txt_summary, "\n".join(L))

        # Avvisi
        W = []
        if res["unknown_blocks"]:
            W.append("Blocchi senza definizione (NON conteggiati):")
            W += [f"  {t}/{s}: {n}" for (t, s), n in res["unknown_blocks"].items()]
            W.append("")
        if res["unresolved"]:
            W.append("Materiali senza ricetta trovata (da considerare a parte):")
            W += [f"  {t}/{s}: {nfmt(q)}" for (t, s), q in res["unresolved"].items()]
            W.append("")
        miss = r["info"]["world_mods_missing"]
        if miss:
            W.append("Mod del mondo non installate (iscriviti nel Workshop o avvia il mondo una volta):")
            W += [f"  {m['name']} ({m['id']}, {m['service']})" for m in miss]
        self._set_text(self.txt_warn, "\n".join(W) if W else "Nessun avviso.")
        self.nb.tab(5, text=f"Avvisi ({len(res['unknown_blocks']) + len(res['unresolved']) + len(miss)})" if W else "Avvisi")

        self.btn_csv.configure(state="normal")
        self.nb.select(0)
        self.var_status.set(f"Fatto: {nfmt(nb)} blocchi → {nfmt(sum(res['components'].values()))} componenti → "
                            f"{nfmt(sum(res['ingots'].values()))} kg lingotti → {nfmt(sum(res['ores'].values()))} kg minerali")

    def _fill(self, tv, rows):
        tv.delete(*tv.get_children())
        tv._sortnum = {}
        for values, nums in rows:
            iid = tv.insert("", "end", values=values)
            tv._sortnum[iid] = nums

    def _sort(self, tv, col):
        rev = not tv._sortdir.get(col, col in ("qty", "direct"))   # numeri: prima decrescente
        tv._sortdir[col] = rev
        items = tv.get_children("")
        if col in ("qty", "direct"):
            keyf = lambda i: tv._sortnum.get(i, {}).get(col, 0)
        else:
            keyf = lambda i: str(tv.set(i, col)).lower()
        for pos, i in enumerate(sorted(items, key=keyf, reverse=rev)):
            tv.move(i, "", pos)

    def _set_text(self, t, s):
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", s)
        t.configure(state="disabled")

    def _append(self, t, s):
        t.configure(state="normal")
        t.insert("end", s)
        t.see("end")
        t.configure(state="disabled")

    def _about(self):
        messagebox.showinfo(f"Info - {APP_TITLE}", (
            f"{APP_TITLE}\n\n"
            f"Autore: {__author__}\n"
            f"E-mail: {__email__}\n\n"
            f"{__copyright__}\n"
            "Software open source distribuito con licenza MIT (vedi LICENSE.txt).\n\n"
            "Il programma è fornito \"così com'è\", senza alcuna garanzia. "
            "L'autore non è responsabile di eventuali danni o errori derivanti dal suo uso: "
            "i risultati sono stime da verificare in gioco.\n\n"
            "Space Engineers è un marchio di Keen Software House. "
            "Questo è un progetto amatoriale non affiliato né approvato da Keen Software House."))

    def _export(self):
        if not self.last:
            return
        r = self.last
        base = os.path.splitext(os.path.basename(os.path.dirname(r["ships"][0]["path"])) or "nave")[0]
        p = filedialog.asksaveasfilename(title="Salva CSV", defaultextension=".csv", initialfile=f"{base}.csv",
                                         filetypes=[("CSV (Excel)", "*.csv")])
        if not p:
            return
        w = r["p"]["world"]
        sc.write_csv(p, r["db"], r["ships"],
                     {"world": w["name"] if w else "vanilla", "assembler": r["p"]["assembler"],
                      "refinery": r["p"]["refinery"], "count": r["p"]["count"]}, r["blocks"], r["res"])
        self.var_status.set(f"CSV salvato in {p}")


if __name__ == "__main__":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)      # testo nitido su schermi ad alta risoluzione
    except Exception:
        pass
    App().mainloop()
