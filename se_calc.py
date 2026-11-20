#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Autore:  Capitan Blooz <capitanblooz@gmail.com>
# Copyright (c) 2026 Capitan Blooz
# Licenza: MIT (open source) - vedi LICENSE.txt.
# Il software e' fornito "cosi' com'e'", senza alcuna garanzia; l'autore non e'
# responsabile di eventuali danni derivanti dal suo uso.
# Space Engineers e' un marchio di Keen Software House; progetto non affiliato.
"""
se_calc.py - Calcolatore materiali per blueprint di Space Engineers 1.

Legge una o piu' blueprint (bp.sbc, XML) e calcola:
  blocchi -> componenti -> lingotti -> minerali
usando le definizioni reali del gioco (Content\\Data) e le mod del mondo scelto
(o, in mancanza, quelle scaricate dal Workshop che contengono i blocchi sconosciuti).

Interfaccia grafica: python se_gui.py

Esempi da riga di comando:
  python se_calc.py bp.sbc
  python se_calc.py bp.sbc --world "%AppData%\\SpaceEngineers\\Saves\\<id>\\<mondo>"
  python se_calc.py bp.sbc --assembler 3 --refinery-yield 1.0 --csv risultato.csv
  python se_calc.py bp.sbc --mod 2877712858
"""

__author__ = "Capitan Blooz"
__email__ = "capitanblooz@gmail.com"
__license__ = "MIT"
__copyright__ = "Copyright (c) 2026 Capitan Blooz"


import argparse
import csv
import io
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict, OrderedDict

XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
OB = "MyObjectBuilder_"

DEFAULT_GAME_PATHS = [
    r"D:\SteamLibrary\steamapps\common\SpaceEngineers",
    r"C:\Program Files (x86)\Steam\steamapps\common\SpaceEngineers",
    r"C:\Program Files\Steam\steamapps\common\SpaceEngineers",
    r"E:\SteamLibrary\steamapps\common\SpaceEngineers",
]
DEFAULT_MODS_DIRS = [
    r"D:\SteamLibrary\steamapps\workshop\content\244850",
    r"C:\Program Files (x86)\Steam\steamapps\workshop\content\244850",
    r"E:\SteamLibrary\steamapps\workshop\content\244850",
]
SE_APPDATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "SpaceEngineers")
SKIP_DIRS = {"prefabs", "blueprints", "missions", "scenarios", "localization", "game"}


# --------------------------------------------------------------------------- utility

def strip_ob(t):
    t = (t or "").strip()
    return t[len(OB):] if t.startswith(OB) else t


def read_id(el):
    """Ritorna (TypeId, SubtypeId) da un nodo <Id> in entrambi i formati del gioco."""
    idn = el.find("Id")
    if idn is None:
        return None, None
    t = idn.get("Type") or idn.get("TypeId") or idn.findtext("TypeId")
    s = idn.get("Subtype") or idn.get("SubtypeId") or idn.findtext("SubtypeId")
    return strip_ob(t), (s or "").strip()


def item(el):
    return (strip_ob(el.get("TypeId")), el.get("SubtypeId", "").strip(), float(el.get("Amount", "1")))


def _log_stderr(msg):
    print(msg, file=sys.stderr)


def safe_parse(path_or_bytes, log=_log_stderr, label=None):
    try:
        if isinstance(path_or_bytes, (bytes, bytearray)):
            return ET.fromstring(path_or_bytes)
        return ET.parse(path_or_bytes).getroot()
    except Exception as e:  # file di mod malformati, ecc.
        if log:
            log(f"  ! impossibile leggere {label or path_or_bytes}: {e}")
        return None


def fmt(x):
    if abs(x - round(x)) < 1e-6:
        return f"{int(round(x)):,}".replace(",", ".")
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def first_existing(paths):
    for p in paths:
        if p and os.path.isdir(p):
            return p
    return paths[0] if paths else None


# --------------------------------------------------------------------------- sorgenti di mod
# Una mod puo' essere una cartella <id>\Data\*.sbc oppure (mod vecchie) un archivio zip
# "<numero>_legacy.bin" che contiene Data/*.sbc.

def mod_root(mod_id, mods_dir, local_name=None):
    """Cartella della mod (workshop o locale in %AppData%\\SpaceEngineers\\Mods) o None."""
    if mod_id and mod_id != "0" and mods_dir:
        p = os.path.join(mods_dir, mod_id)
        if os.path.isdir(p):
            return p
    if local_name:
        p = os.path.join(SE_APPDATA, "Mods", local_name)
        if os.path.isdir(p):
            return p
    return None


def iter_sbc(root_dir):
    """Generatore di (etichetta, bytes) per tutti gli .sbc di definizione di una mod/del gioco."""
    data = None
    for cand in (os.path.join(root_dir, "Data"), root_dir):
        if os.path.isdir(cand) and cand.endswith("Data"):
            data = cand
            break
    if data:
        files = []
        for dp, dirs, fn in os.walk(data):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
            files += [os.path.join(dp, f) for f in fn if f.lower().endswith(".sbc")]
        for f in sorted(files):
            try:
                with open(f, "rb") as fh:
                    yield f, fh.read()
            except OSError:
                continue
        return
    # mod legacy compressa
    for f in sorted(os.listdir(root_dir)):
        if not f.lower().endswith((".bin", ".sbm")):
            continue
        try:
            with zipfile.ZipFile(os.path.join(root_dir, f)) as z:
                for n in sorted(z.namelist()):
                    parts = n.replace("\\", "/").lower().split("/")
                    if not n.lower().endswith(".sbc") or not parts or parts[0] != "data":
                        continue
                    if any(p in SKIP_DIRS for p in parts[1:-1]):
                        continue
                    yield f"{f}:{n}", z.read(n)
        except (zipfile.BadZipFile, OSError):
            continue


# --------------------------------------------------------------------------- database

class Database:
    def __init__(self, log=_log_stderr):
        self.log = log
        self.blocks = {}             # (TypeId, SubtypeId) -> {"name", "size", "components": OrderedDict, "source"}
        self.blocks_by_subtype = defaultdict(list)
        self.blueprints = []         # dict(id, prereq=[(t,s,a)], results=[(t,s,a)], primary, source)
        self.item_names = {}         # (TypeId, SubtypeId) -> DisplayName (chiave di localizzazione o testo)
        self.texts = {}              # chiave localizzazione -> testo
        self.sources = []

    # -- caricamento -------------------------------------------------------
    def load_bytes(self, blob, label, source, secondary=False):
        root = safe_parse(blob, self.log, label)
        if root is None:
            return
        for el in root.iter():
            tag = el.tag
            if tag == "Definition" or tag == "Component" or tag == "PhysicalItem":
                if el.find("Id") is None:
                    continue
                if tag == "Definition" and el.find("Components") is not None:
                    self._add_block(el, source, secondary)
                dn = el.findtext("DisplayName")
                if dn:
                    key = read_id(el)
                    if not (secondary and key in self.item_names):
                        self.item_names[key] = dn.strip()
            elif tag == "Blueprint":
                self._add_blueprint(el, source, secondary)

    def load_dir(self, root_dir, source, secondary=False):
        """secondary=True (mod trovate in automatico): aggiunge solo blocchi mancanti e
        ricette per oggetti che non hanno gia' una ricetta; non altera i valori esistenti."""
        for label, blob in iter_sbc(root_dir):
            self.load_bytes(blob, label, source, secondary)
        self.sources.append(source)

    def load_localization(self, data_dir, lang="it"):
        """Testi del gioco (inglese, poi sovrascritti dalla lingua scelta se presente)."""
        loc = os.path.join(data_dir, "Localization")
        for fn in ("MyTexts.resx", f"MyTexts.{lang}.resx"):
            p = os.path.join(loc, fn)
            if not os.path.isfile(p):
                continue
            root = safe_parse(p, self.log)
            if root is None:
                continue
            for d in root.iter("data"):
                v = d.findtext("value")
                if d.get("name") and v:
                    self.texts[d.get("name")] = v

    def _add_block(self, d, source, secondary=False):
        t, s = read_id(d)
        if t is None:
            return
        comps = OrderedDict()
        for c in d.find("Components").findall("Component"):
            sub = c.get("Subtype")
            if not sub:
                continue
            comps[sub] = comps.get(sub, 0) + int(float(c.get("Count", "0")))
        if not comps:
            return
        key = (t, s)
        if secondary and (key in self.blocks or self.blocks_by_subtype.get(s)):
            return
        name = (d.findtext("DisplayName") or s or t).strip()
        size = (d.findtext("CubeSize") or "").strip()
        if key not in self.blocks:
            self.blocks_by_subtype[s].append(key)
        self.blocks[key] = {"name": name, "size": size, "components": comps, "source": source}

    def _add_blueprint(self, b, source, secondary=False):
        _, bid = read_id(b)
        pre = [item(i) for i in b.findall("Prerequisites/Item")]
        res = []
        r = b.find("Result")
        if r is not None:
            res.append(item(r))
        res += [item(i) for i in b.findall("Results/Item")]
        if not pre or not res:
            return
        bp = {"id": bid, "prereq": pre, "results": res,
              "primary": (b.findtext("IsPrimary") or "").strip().lower() == "true",
              "source": source, "secondary": secondary}
        if secondary:
            if any(x["id"] == bid for x in self.blueprints):
                return
        else:
            # una mod caricata esplicitamente che ridefinisce lo stesso blueprint lo sovrascrive
            self.blueprints = [x for x in self.blueprints if x["id"] != bid]
        self.blueprints.append(bp)

    # -- ricerche ----------------------------------------------------------
    def find_block(self, t, s):
        if (t, s) in self.blocks:
            return self.blocks[(t, s)]
        cands = self.blocks_by_subtype.get(s, [])
        if cands:
            return self.blocks[cands[-1]]
        return None

    def text(self, key):
        if not key:
            return key
        if key in self.texts:
            return self.texts[key]
        return None if key.startswith("DisplayName_") else key

    def display(self, t, s):
        """Nome leggibile di un oggetto (componente, lingotto, minerale)."""
        return self.text(self.item_names.get((t, s))) or s

    def block_display(self, t, s):
        d = self.find_block(t, s)
        return (self.text(d["name"]) if d else None) or s or t

    def recipe_for(self, t, s):
        """Miglior blueprint che produce (t, s). Ritorna (prereq, quantita_prodotta) o None."""
        best, best_score = None, None
        for bp in self.blueprints:
            out = [r for r in bp["results"] if r[0] == t and r[1] == s]
            if not out:
                continue
            pre_types = {p[0] for p in bp["prereq"]}
            pre_subs = {p[1] for p in bp["prereq"]}
            if t == "Ingot" and "Scrap" in pre_subs:
                continue                       # il ferro da rottami non e' la ricetta "normale"
            score = 0 if bp.get("secondary") else 100   # ricette vanilla/esplicite sempre prima
            score += 4 if len(bp["results"]) == 1 else 0
            score += 3 if bp["id"] == s or s in pre_subs else 0
            score += 2 if bp["primary"] else 0
            score += 1 if (t == "Ingot" and pre_types == {"Ore"}) or (t == "Component" and "Ore" not in pre_types) else 0
            if best_score is None or score > best_score:
                best, best_score = (bp["prereq"], out[0][2]), score
        return best


def mods_containing(mods_dir, needles, exclude=()):
    """ID delle mod installate i cui .sbc contengono una delle stringhe cercate (scansione testo)."""
    found = set()
    if not mods_dir or not os.path.isdir(mods_dir):
        return found
    needles_b = [n.encode("utf-8") for n in needles]
    for mod in sorted(os.listdir(mods_dir)):
        if mod in exclude:
            continue
        root = os.path.join(mods_dir, mod)
        if not os.path.isdir(root):
            continue
        for _, blob in iter_sbc(root):
            if any(n in blob for n in needles_b):
                found.add(mod)
                break
    return found


# --------------------------------------------------------------------------- mondi (salvataggi)

def find_worlds(saves_root=None):
    """Elenco dei salvataggi: lista di dict(name, path, user, mtime), piu' recenti per primi."""
    saves_root = saves_root or os.path.join(SE_APPDATA, "Saves")
    out = []
    if not os.path.isdir(saves_root):
        return out
    for user in os.listdir(saves_root):
        udir = os.path.join(saves_root, user)
        if not os.path.isdir(udir):
            continue
        for w in os.listdir(udir):
            wdir = os.path.join(udir, w)
            for f in ("Sandbox_config.sbc", "Sandbox.sbc"):
                p = os.path.join(wdir, f)
                if os.path.isfile(p):
                    out.append({"name": w, "path": wdir, "user": user, "mtime": os.path.getmtime(p)})
                    break
    out.sort(key=lambda w: -w["mtime"])
    return out


def read_world(path, log=_log_stderr):
    """Legge impostazioni e mod di un mondo (cartella del salvataggio o suo Sandbox*.sbc)."""
    folder = os.path.dirname(path) if os.path.isfile(path) else path
    cfg = None
    for f in ("Sandbox_config.sbc", "Sandbox.sbc"):
        if os.path.isfile(os.path.join(folder, f)):
            cfg = os.path.join(folder, f)
            break
    if cfg is None:
        raise FileNotFoundError(f"Nessun Sandbox_config.sbc / Sandbox.sbc in {folder}")
    root = safe_parse(cfg, log)
    if root is None:
        raise ValueError(f"Impossibile leggere {cfg}")
    settings = root.find(".//Settings")

    def num(tag, default=1.0):
        try:
            return float(settings.findtext(tag)) if settings is not None and settings.findtext(tag) else default
        except ValueError:
            return default

    mods = []
    mods_el = root.find("Mods")
    if mods_el is None:
        mods_el = root.find(".//Mods")
    if mods_el is not None:
        for mi in mods_el.findall("ModItem"):
            fname = (mi.findtext("Name") or "").strip()
            mods.append({
                "id": (mi.findtext("PublishedFileId") or "0").strip(),
                "name": mi.get("FriendlyName") or fname,
                "local_name": fname[:-4] if fname.lower().endswith(".sbm") else fname,
                "service": (mi.findtext("PublishedServiceName") or "Steam").strip(),
            })
    return {
        "name": root.findtext("SessionName") or os.path.basename(folder.rstrip("\\/")),
        "path": folder,
        "game_mode": settings.findtext("GameMode") if settings is not None else "",
        "assembler_eff": num("AssemblerEfficiencyMultiplier"),
        "assembler_speed": num("AssemblerSpeedMultiplier"),
        "refinery_speed": num("RefinerySpeedMultiplier"),
        "mods": mods,
    }


def find_local_blueprints():
    """Blueprint locali e del workshop: lista di dict(name, path, kind)."""
    out = []
    base = os.path.join(SE_APPDATA, "Blueprints")
    for kind in ("local", "cloud", "workshop"):
        d = os.path.join(base, kind)
        if not os.path.isdir(d):
            continue
        for n in os.listdir(d):
            p = os.path.join(d, n, "bp.sbc")
            if os.path.isfile(p):
                out.append({"name": n, "path": p, "kind": kind})
    out.sort(key=lambda b: (b["kind"] != "local", b["name"].lower()))
    return out


# --------------------------------------------------------------------------- server online (dai log)
# Il client non salva i mondi dei server dedicati, ma il log del gioco
# (%AppData%\SpaceEngineers\SpaceEngineers_*.log) registra a ogni accesso riuscito
# la "List of used mods" del server, nell'ordine del mondo.

_RE_JOIN = re.compile(r"Joining server: Name:(.*?) ConnectionString:(\S+)")
_RE_MOD = re.compile(r"Id = ([\w.\-]+):(\d+), Filename = '([^']*)', Name = '(.*)'\s*$")
_RE_TIME = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


def find_servers_from_logs(log_dir=None):
    """Server a cui si e' entrati con successo, con le loro mod (dall'accesso piu' recente).
    Ritorna lista di dict(name, connection, mods, time, log), piu' recenti per primi."""
    log_dir = log_dir or SE_APPDATA
    try:
        files = [os.path.join(log_dir, f) for f in os.listdir(log_dir)
                 if f.startswith("SpaceEngineers_") and f.endswith(".log")]
    except OSError:
        return []
    files.sort(key=os.path.getmtime)
    servers = {}
    for f in files:
        pending = current = None
        collecting = None
        start_time = ""
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if collecting is not None:
                    if "List of used mods - END" in line:
                        name, conn = current
                        servers[name] = {"name": name, "connection": conn, "mods": collecting,
                                         "time": start_time, "log": f}
                        collecting = current = pending = None
                        continue
                    m = _RE_MOD.search(line)
                    if m:
                        service, mid, fname, title = m.groups()
                        collecting.append({"id": mid, "name": title, "service": service,
                                           "local_name": fname[:-4] if fname.lower().endswith(".sbm") else fname})
                    continue
                if "Joining server:" in line:
                    m = _RE_JOIN.search(line)
                    pending, current = ((m.group(1).strip(), m.group(2)) if m else None), None
                elif "World requested" in line and pending:
                    current = pending
                elif "Loading Sandbox world configuration file" in line or "Loading available saves" in line:
                    pending = current = None          # l'utente ha caricato un mondo locale
                elif "List of used mods (" in line and "START" in line and current:
                    collecting = []
                    t = _RE_TIME.match(line)
                    start_time = t.group(1) if t else ""
    return sorted(servers.values(), key=lambda s: s["time"], reverse=True)


def server_as_world(srv, assembler_eff=1.0):
    """Adatta un server (da find_servers_from_logs) al formato di read_world, per il calcolo."""
    return {"name": srv["name"], "path": f"server {srv['connection']} (log del {srv['time']})",
            "game_mode": "", "assembler_eff": assembler_eff, "assembler_speed": None,
            "refinery_speed": None, "mods": list(srv["mods"])}


# --------------------------------------------------------------------------- mondo di test locale

XSD_NS = "http://www.w3.org/2001/XMLSchema"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
ET.register_namespace("xsd", XSD_NS)
ET.register_namespace("xsi", XSI_NS)


def find_templates(data_dir):
    """Modelli di mondo del gioco (Content\\CustomWorlds): lista di (nome, cartella)."""
    base = os.path.join(os.path.dirname(data_dir), "CustomWorlds")
    out = []
    if os.path.isdir(base):
        for n in sorted(os.listdir(base)):
            p = os.path.join(base, n)
            if os.path.isfile(os.path.join(p, "Sandbox.sbc")):
                out.append((n, p))
    return out


def read_template_settings(template_dir):
    """Impostazioni (solo valori semplici) del modello: OrderedDict nome -> testo."""
    out = OrderedDict()
    for fn in ("Sandbox_config.sbc", "Sandbox.sbc"):
        p = os.path.join(template_dir, fn)
        if not os.path.isfile(p):
            continue
        root = safe_parse(p, None)
        st = root.find("Settings") if root is not None else None
        if st is None:
            continue
        for el in st:
            if len(el) == 0 and el.text is not None:
                out.setdefault(el.tag, el.text.strip())
    return out


def default_saves_dir():
    """Cartella Saves\\<SteamID> dell'utente (quella usata piu' di recente)."""
    base = os.path.join(SE_APPDATA, "Saves")
    best, best_t = None, -1
    if os.path.isdir(base):
        for d in os.listdir(base):
            p = os.path.join(base, d)
            if not os.path.isdir(p):
                continue
            ls = os.path.join(p, "LastSession.sbl")
            t = os.path.getmtime(ls) if os.path.isfile(ls) else 0
            if t > best_t:
                best, best_t = p, t
    return best


def safe_folder_name(name):
    bad = '<>:"/\\|?*'
    s = "".join("_" if c in bad or ord(c) < 32 else c for c in name).strip().rstrip(". ")
    return s or "Mondo di test"


def ensure_xml_namespaces(xml_text):
    """Rimette sul nodo radice le dichiarazioni xmlns:xsd / xmlns:xsi dei file del gioco.
    ElementTree elimina xmlns:xsd perche' lo vede usato solo dentro i valori
    (es. xsi:type="xsd:long"): senza, il gioco non riesce a leggere il mondo e lo scarta."""
    m = re.match(r"\s*<[\w.:-]+", xml_text)
    if not m:
        return xml_text
    end = xml_text.index(">", m.end())
    head = xml_text[:end]
    add = ""
    for prefix, uri in (("xsd", XSD_NS), ("xsi", XSI_NS)):
        if f"xmlns:{prefix}=" not in head:
            add += f' xmlns:{prefix}="{uri}"'
    return xml_text[:m.end()] + add + xml_text[m.end():]


def current_game_version(saves_dir=None):
    """Versione attuale del gioco nel formato di <AppVersion> (es. 1211018).
    Dal log piu' recente ("App Version: 01_211_018"), altrimenti la piu' alta tra i salvataggi recenti.
    Serve perche' i modelli del gioco hanno una versione vecchia (1166003) e il gioco, aprendoli,
    avvisa che il mondo e' in formato obsoleto."""
    try:
        logs = [os.path.join(SE_APPDATA, f) for f in os.listdir(SE_APPDATA)
                if f.startswith("SpaceEngineers_") and f.endswith(".log")]
    except OSError:
        logs = []
    for p in sorted(logs, key=os.path.getmtime, reverse=True)[:5]:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for _, line in zip(range(20), fh):
                    m = re.search(r"App Version:\s*(\d+)_(\d+)_(\d+)", line)
                    if m:
                        return str(int(m.group(1) + m.group(2).zfill(3) + m.group(3).zfill(3)))
        except OSError:
            continue
    best = None
    if saves_dir and os.path.isdir(saves_dir):
        files = [os.path.join(saves_dir, w, "Sandbox.sbc") for w in os.listdir(saves_dir)]
        files = [f for f in files if os.path.isfile(f)]
        for p in sorted(files, key=os.path.getmtime, reverse=True)[:10]:
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    m = re.search(r"<AppVersion>(\d+)</AppVersion>", fh.read(400000))
            except OSError:
                continue
            if m and (best is None or int(m.group(1)) > int(best)):
                best = m.group(1)
    return best


def guess_steam_id(saves_dir):
    """Lo SteamID e' il nome della cartella Saves\\<SteamID>."""
    n = os.path.basename(os.path.normpath(saves_dir or ""))
    return n if n.isdigit() and len(n) >= 15 else None


_RE_APD_FIRST = re.compile(
    r"<AllPlayersData>\s*<dictionary>\s*<item>\s*<Key[^>]*>\s*<ClientId>(\d+)</ClientId>\s*<SerialId>0</SerialId>"
    r"\s*(?:<HashedId>(\d+)</HashedId>\s*)?</Key>\s*<Value[^>]*>\s*<DisplayName>([^<]*)</DisplayName>")


def find_local_player(saves_dir, steam_id=None):
    """Identita' del giocatore locale ricavata dai suoi salvataggi in giocatore singolo.
    Le versioni recenti del gioco non scrivono lo SteamID: usano ClientId 0 + HashedId.
    Ritorna dict(steam_id, hashed_id, name); hashed_id/name None se non trovati."""
    from collections import Counter
    steam_id = steam_id or guess_steam_id(saves_dir)
    out = {"steam_id": steam_id, "hashed_id": None, "name": None}
    if not saves_dir or not os.path.isdir(saves_dir):
        return out
    files = []
    for w in os.listdir(saves_dir):
        p = os.path.join(saves_dir, w, "Sandbox.sbc")
        if os.path.isfile(p):
            files.append((os.path.getmtime(p), p))
    seen = Counter()
    names = {}
    for _, p in sorted(files, reverse=True)[:25]:            # i 25 salvataggi piu' recenti
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                m = _RE_APD_FIRST.search(fh.read())
        except OSError:
            continue
        if not m:
            continue
        cid, hid, name = m.groups()
        key = ("hash", hid) if cid == "0" and hid else ("steam", cid)
        if key[0] == "steam" and cid != steam_id:
            continue
        seen[key] += 1
        names.setdefault(key, name.strip())
    if seen:
        key = seen.most_common(1)[0][0]
        out["name"] = names[key]
        if key[0] == "hash":
            out["hashed_id"] = key[1]
    return out


def _player_key(parent, tag, player):
    """Scrive un PlayerId nel formato giusto (moderno con HashedId, oppure classico con SteamID)."""
    el = _sub(parent, tag)
    if player.get("hashed_id"):
        _sub(el, "ClientId", "0")
        _sub(el, "SerialId", "0")
        _sub(el, "HashedId", player["hashed_id"])
    else:
        _sub(el, "ClientId", player["steam_id"])
        _sub(el, "SerialId", "0")
    return el


def _is_player(el, player):
    if el is None:
        return False
    if player.get("hashed_id") and (el.findtext("HashedId") or "").strip() == player["hashed_id"]:
        return True
    return bool(player.get("steam_id")) and (el.findtext("ClientId") or "").strip() == player["steam_id"]


def _xsi(tag_type):
    return {f"{{{XSI_NS}}}type": tag_type}


def _sub(parent, tag, text=None, attrib=None):
    el = ET.SubElement(parent, tag, attrib or {})
    if text is not None:
        el.text = str(text)
    return el


def _ensure(parent, tag):
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    return el


def add_player_character(world_dir, player, char_source_sbs, log=print):
    """Rende il mondo giocabile subito: il giocatore controlla un personaggio in tuta.
    player = dict(steam_id, hashed_id, name) come da find_local_player().
    - se il modello ha gia' un personaggio controllato, lo intesta al giocatore;
    - altrimenti ne crea uno (copiato da char_source_sbs, di solito Empty World) nel punto di
      vista iniziale del modello (SpectatorPosition).
    Senza questo il gioco apre la schermata di respawn, spesso senza opzioni."""
    import copy
    import random
    player_name = player.get("name") or "Engineer"
    cp_path = os.path.join(world_dir, "Sandbox.sbc")
    sec_path = os.path.join(world_dir, "SANDBOX_0_0_0_.sbs")
    cp_tree, sec_tree = ET.parse(cp_path), ET.parse(sec_path)
    cp, sec = cp_tree.getroot(), sec_tree.getroot()
    xtype = f"{{{XSI_NS}}}type"
    objs = _ensure(sec, "SectorObjects")
    ctrl = (cp.findtext("ControlledObject") or "").strip()
    char = next((e for e in objs if e.get(xtype) == "MyObjectBuilder_Character"
                 and (e.findtext("EntityId") or "").strip() == ctrl), None)

    if char is None:
        src = ET.parse(char_source_sbs).getroot()
        tpl = next((e for e in src.iter("MyObjectBuilder_EntityBase")
                    if e.get(xtype) == "MyObjectBuilder_Character"), None)
        if tpl is None:
            raise ValueError(f"Nessun personaggio di esempio in {char_source_sbs}")
        char = copy.deepcopy(tpl)
        char.find("EntityId").text = str((1 << 56) | random.getrandbits(55))
        spec = cp.find("SpectatorPosition")
        po = char.find("PositionAndOrientation")
        if spec is not None and po is not None:
            for tag in ("Position", "Forward", "Up", "Orientation"):
                old, new = po.find(tag), spec.find(tag)
                if old is not None and new is not None:
                    po.insert(list(po).index(old), copy.deepcopy(new))
                    po.remove(old)
        objs.append(char)
        log("Creato un personaggio in tuta nel punto di vista iniziale del modello "
            "(settore modificato: alla prima apertura il gioco avvisa e lo converte)")
        for tag in ("PlayerSteamId", "PlayerSerialId"):     # i salvataggi recenti non li scrivono
            for el in char.findall(tag):
                char.remove(el)
        if not player.get("hashed_id"):
            _sub(char, "PlayerSteamId", player["steam_id"])
            _sub(char, "PlayerSerialId", "0")
        _ensure(char, "DisplayName").text = player_name
        lv = char.find("LinearVelocity")
        if lv is not None:
            lv.attrib.update({"x": "0", "y": "0", "z": "0"})
    else:
        # Il settore NON si tocca: cosi' resta valida la sua copia binaria (.sbsB5) del modello e il
        # gioco non mostra l'avviso "formato obsoleto". Il personaggio passa al giocatore solo tramite
        # Sandbox.sbc (Identity.CharacterEntityId + ControlledEntities).
        sec_tree = None
        log("Il personaggio del modello viene assegnato al giocatore (settore invariato)")
    char_id = char.findtext("EntityId").strip()

    idents = _ensure(cp, "Identities")
    apd = _ensure(_ensure(cp, "AllPlayersData"), "dictionary")

    # --- il giocatore esiste gia' (il gioco lo crea quando si apre il mondo): riuso la sua identita'
    existing = next((it for it in apd.findall("item") if _is_player(it.find("Key"), player)), None)
    ex_ident = None
    if existing is not None:
        ex_id = (existing.findtext("Value/IdentityId") or "").strip()
        ex_ident = next((i for i in idents.findall("MyObjectBuilder_Identity")
                         if (i.findtext("IdentityId") or "").strip() == ex_id), None)
    if ex_ident is not None:
        for ide in idents.findall("MyObjectBuilder_Identity"):
            if ide is not ex_ident and (ide.findtext("CharacterEntityId") or "").strip() == char_id:
                ide.find("CharacterEntityId").text = "0"
        _ensure(ex_ident, "CharacterEntityId").text = char_id
        _ensure(existing.find("Value"), "Connected").text = "true"
        log("Giocatore gia' presente nel mondo: gli collego il personaggio")
        _finish_player_control(cp, char_id, ctrl, player)
        _write_world_xml(cp_tree, cp_path, sec_tree, sec_path)
        return char_id

    # --- identita' del giocatore
    identity_id = str((2 << 56) | random.getrandbits(48))
    old_player_value = None
    for ide in idents.findall("MyObjectBuilder_Identity"):
        if (ide.findtext("CharacterEntityId") or "").strip() == char_id:
            old_identity = (ide.findtext("IdentityId") or "").strip()
            ide.find("CharacterEntityId").text = "0"          # il personaggio non e' piu' suo
            for it in cp.findall("AllPlayersData/dictionary/item"):
                if (it.findtext("Value/IdentityId") or "").strip() == old_identity:
                    old_player_value = it.find("Value")
    ide = _sub(idents, "MyObjectBuilder_Identity")
    _sub(ide, "IdentityId", identity_id)
    _sub(ide, "DisplayName", player_name)
    _sub(ide, "CharacterEntityId", char_id)
    _sub(ide, "Model", "Default_Astronaut")
    _sub(ide, "ColorMask", attrib={"x": "0", "y": "0", "z": "0.05"})
    _sub(ide, "BlockLimitModifier", "0")
    _sub(ide, "LastLoginTime", __import__("datetime").datetime.now().astimezone().isoformat())

    # --- giocatore (SteamID -> identita')
    for it in list(apd.findall("item")):
        if _is_player(it.find("Key"), player):
            apd.remove(it)
    it = _sub(apd, "item")
    _player_key(it, "Key", player)
    if old_player_value is not None:
        val = copy.deepcopy(old_player_value)               # stessa toolbar del modello
        it.append(val)
    else:
        val = _sub(it, "Value")
    _ensure(val, "DisplayName").text = player_name
    _ensure(val, "IdentityId").text = identity_id
    _ensure(val, "Connected").text = "true"

    _finish_player_control(cp, char_id, ctrl, player)
    _write_world_xml(cp_tree, cp_path, sec_tree, sec_path)
    return char_id


def _finish_player_control(cp, char_id, old_ctrl, player):
    """Il giocatore controlla il personaggio e la telecamera parte da li'."""
    ce = _ensure(_ensure(cp, "ControlledEntities"), "dictionary")
    for it in list(ce.findall("item")):
        if (it.findtext("Key") or "").strip() in (char_id, old_ctrl) or _is_player(it.find("Value"), player):
            ce.remove(it)
    it = _sub(ce, "item")
    _sub(it, "Key", char_id, _xsi("xsd:long"))
    _player_key(it, "Value", player)
    for tag in ("ControlledObject", "CameraEntity"):
        _ensure(cp, tag).text = char_id
    _ensure(cp, "CameraController").text = "Entity"


def template_has_player_character(template_dir):
    """True se il modello contiene gia' il personaggio controllato (Empty World, Red Ship...):
    in quel caso il mondo di test si crea senza toccare il settore e senza avviso di conversione."""
    try:
        cp = ET.parse(os.path.join(template_dir, "Sandbox.sbc")).getroot()
        sec = ET.parse(os.path.join(template_dir, "SANDBOX_0_0_0_.sbs")).getroot()
    except (OSError, ET.ParseError):
        return False
    ctrl = (cp.findtext("ControlledObject") or "").strip()
    xtype = f"{{{XSI_NS}}}type"
    objs = sec.find("SectorObjects")
    return bool(ctrl) and objs is not None and any(
        e.get(xtype) == "MyObjectBuilder_Character" and (e.findtext("EntityId") or "").strip() == ctrl for e in objs)


def _write_world_xml(cp_tree, cp_path, sec_tree, sec_path):
    """Scrive Sandbox.sbc e, solo se modificato (sec_tree non None), il settore."""
    for tree, path in ((cp_tree, cp_path), (sec_tree, sec_path)):
        if tree is None:
            continue
        ET.indent(tree, space="  ")
        with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write('<?xml version="1.0"?>\n')
            fh.write(ensure_xml_namespaces(ET.tostring(tree.getroot(), encoding="unicode")))
            fh.write("\n")
    b5 = sec_path + "B5"                 # versione binaria del settore: non piu' valida, la rigenera il gioco
    if sec_tree is not None and os.path.exists(b5):
        os.remove(b5)


def create_test_world(template_dir, saves_dir, name, settings, mods, log=print, player=None):
    """Crea un nuovo salvataggio locale copiando un modello del gioco e impostando nome,
    parametri (dict nome -> testo) e mod (lista di dict id/name/local_name/service).
    player = dict(steam_id, hashed_id, name, char_source) per entrare subito in tuta
    (vedi find_local_player / add_player_character).
    Non sovrascrive mai: se la cartella esiste solleva FileExistsError. Ritorna la cartella."""
    import datetime
    import shutil
    import uuid
    dest = os.path.join(saves_dir, safe_folder_name(name))
    if os.path.exists(dest):
        raise FileExistsError(f"Esiste gia' un salvataggio con questo nome:\n{dest}")
    # *.scf = descrittore di "campagna" presente in molti modelli (Star System, Earth Planet...):
    # una cartella che lo contiene viene trattata come campagna e NON compare in "Carica partita".
    shutil.copytree(template_dir, dest, ignore=shutil.ignore_patterns("*.scf"))
    for f in os.listdir(dest):                       # versioni binarie della config: le rigenera il gioco
        if f.lower().endswith(".sbcb5"):
            os.remove(os.path.join(dest, f))
    ET.register_namespace("xsd", XSD_NS)
    ET.register_namespace("xsi", XSI_NS)
    now = datetime.datetime.now().astimezone().isoformat()
    for fn in ("Sandbox.sbc", "Sandbox_config.sbc"):
        p = os.path.join(dest, fn)
        if not os.path.isfile(p):
            continue
        tree = ET.parse(p)
        root = tree.getroot()
        if fn == "Sandbox.sbc":
            el = root.find("SessionName")
            if el is None:
                el = ET.SubElement(root, "SessionName")
            el.text = name
            el = root.find("WorldId")                # ogni mondo ha il suo ID
            if el is not None:
                el.text = str(uuid.uuid4())
            ver = current_game_version(saves_dir)    # evita l'avviso "formato obsoleto"
            el = root.find("AppVersion")
            if ver and el is not None and int(ver) > int((el.text or "0").strip() or 0):
                log(f"AppVersion {el.text} -> {ver}")
                el.text = ver
        el = root.find("LastSaveTime")
        if el is not None:
            el.text = now
        st = root.find("Settings")
        if st is not None:
            for k, v in settings.items():
                el = st.find(k)
                if el is None:
                    el = ET.SubElement(st, k)
                el.text = str(v)
        me = root.find("Mods")
        if me is None:
            me = ET.SubElement(root, "Mods")
        for ch in list(me):
            me.remove(ch)
        me.text = None
        for m in mods:
            mi = ET.SubElement(me, "ModItem", {"FriendlyName": m.get("name") or m["id"]})
            ET.SubElement(mi, "Name").text = f"{m.get('local_name') or m['id']}.sbm"
            ET.SubElement(mi, "PublishedFileId").text = m["id"]
            ET.SubElement(mi, "PublishedServiceName").text = m.get("service") or "Steam"
        if fn == "Sandbox_config.sbc":
            # Struttura dei salvataggi veri: Settings, Mods, SessionName, LastSaveTime.
            # Il gioco usa questo file per sovrascrivere il checkpoint: senza SessionName
            # il mondo resta senza nome e non compare in "Carica partita".
            keep = [root.find("Settings"), me]
            for ch in list(root):
                root.remove(ch)
            for ch in keep:
                if ch is not None:
                    root.append(ch)
            ET.SubElement(root, "SessionName").text = name
            ET.SubElement(root, "LastSaveTime").text = now
        ET.indent(tree, space="  ")
        with open(p, "w", encoding="utf-8", newline="\r\n") as fh:   # stesso formato dei file del gioco
            fh.write('<?xml version="1.0"?>\n')
            fh.write(ensure_xml_namespaces(ET.tostring(root, encoding="unicode")))
            fh.write("\n")
        log(f"Scritto {p}")
    if player:
        add_player_character(dest, player, player["char_source"], log)
    return dest


# --------------------------------------------------------------------------- blueprint nave

def read_ship(path, log=_log_stderr):
    """Ritorna (nome, lista_griglie) con griglia = (nome, size, dict blocchi (TypeId, SubtypeId) -> n)."""
    if os.path.isdir(path):
        path = os.path.join(path, "bp.sbc")
    root = safe_parse(path, log)
    if root is None:
        raise ValueError(f"Impossibile leggere {path}")
    name = root.findtext(".//ShipBlueprint/DisplayName") or os.path.basename(os.path.dirname(os.path.abspath(path)))
    bp_id = root.find(".//ShipBlueprint/Id")
    if bp_id is not None and bp_id.get("Subtype"):
        name = f"{bp_id.get('Subtype')} ({name})"
    grids = []
    for g in root.iter("CubeGrid"):
        cb = g.find("CubeBlocks")
        if cb is None:
            continue
        counts = defaultdict(int)
        for b in cb:                                    # solo figli diretti: niente inventari
            t = strip_ob(b.get(XSI_TYPE) or "CubeBlock")
            s = (b.findtext("SubtypeName") or "").strip()
            counts[(t, s)] += 1
        grids.append((g.findtext("DisplayName") or "?", g.findtext("GridSizeEnum") or "?", counts))
    return name, grids


# --------------------------------------------------------------------------- calcolo

def _add_into(dst, src, mult):
    for k, v in src.items():
        dst[k] += v * mult


def compute(db, block_counts, assembler_eff, refinery_yield):
    """Calcola tutta la catena. Ritorna un dict con:
      components      {sottotipo: pezzi}
      comp_unit       {sottotipo: {("Ingot"|"Ore"|"?", sottotipo): kg per 1 pezzo}}
      ingots          {sottotipo: kg}
      ingot_unit      {sottotipo: {("Ore"|"?", sottotipo): kg per 1 kg di lingotto}}
      ores            {sottotipo: kg}   (da raffinare + usati direttamente)
      ores_direct     {sottotipo: kg}   minerali usati direttamente dall'assemblatore
      unknown_blocks  {(t, s): n}
      unresolved      {(t, s): quantita}  oggetti senza ricetta
    """
    components = defaultdict(float)
    unknown_blocks = {}
    for key, n in block_counts.items():
        d = db.find_block(*key)
        if d is None:
            unknown_blocks[key] = n
            continue
        for c, q in d["components"].items():
            components[c] += q * n

    unit_cache = {}

    def raw_per_unit(t, s, stack=()):
        """Materie prime (lingotti/minerali) per 1 unita' di (t, s) prodotta all'assemblatore."""
        key = (t, s)
        if key in unit_cache:
            return unit_cache[key]
        out = defaultdict(float)
        rec = db.recipe_for(t, s) if key not in stack and len(stack) < 10 else None
        if rec is None:
            out[("?", t, s)] = 1.0
        else:
            prereq, made = rec
            for pt, ps, pa in prereq:
                need = pa / made
                if t == "Component":
                    need /= assembler_eff
                if pt in ("Ingot", "Ore"):
                    out[(pt, ps)] += need
                else:
                    _add_into(out, raw_per_unit(pt, ps, stack + (key,)), need)
        unit_cache[key] = dict(out)
        return unit_cache[key]

    ore_cache = {}

    def ore_per_ingot(s, stack=()):
        """Minerale per 1 kg di lingotto s (raffineria)."""
        if s in ore_cache:
            return ore_cache[s]
        out = defaultdict(float)
        rec = db.recipe_for("Ingot", s) if s not in stack and len(stack) < 10 else None
        if rec is None:
            out[("?", "Ingot", s)] = 1.0
        else:
            prereq, made = rec
            batches = 1.0 / (made * refinery_yield)
            for pt, ps, pa in prereq:
                if pt == "Ore":
                    out[("Ore", ps)] += pa * batches
                elif pt == "Ingot":
                    _add_into(out, ore_per_ingot(ps, stack + (s,)), pa * batches)
                else:
                    out[("?", pt, ps)] += pa * batches
        ore_cache[s] = dict(out)
        return ore_cache[s]

    ingots = defaultdict(float)
    ores = defaultdict(float)
    ores_direct = defaultdict(float)
    unresolved = defaultdict(float)
    comp_unit = {}

    for c, q in components.items():
        u = raw_per_unit("Component", c)
        comp_unit[c] = u
        for k, v in u.items():
            if k[0] == "Ingot":
                ingots[k[1]] += v * q
            elif k[0] == "Ore":
                ores_direct[k[1]] += v * q
            else:
                unresolved[(k[1], k[2])] += v * q

    ingot_unit = {}
    for s, q in ingots.items():
        u = ore_per_ingot(s)
        ingot_unit[s] = u
        for k, v in u.items():
            if k[0] == "Ore":
                ores[k[1]] += v * q
            else:
                unresolved[(k[1], k[2])] += v * q
    for s, q in ores_direct.items():
        ores[s] += q

    return {"components": dict(components), "comp_unit": comp_unit, "ingots": dict(ingots),
            "ingot_unit": ingot_unit, "ores": dict(ores), "ores_direct": dict(ores_direct),
            "unknown_blocks": unknown_blocks, "unresolved": dict(unresolved)}


# --------------------------------------------------------------------------- orchestrazione

def find_game(arg=None):
    paths = [arg] if arg else DEFAULT_GAME_PATHS
    for p in paths:
        if not p:
            continue
        for cand in (os.path.join(p, "Content", "Data"), os.path.join(p, "Data"), p):
            if os.path.isfile(os.path.join(cand, "Blueprints.sbc")):
                return cand
    return None


def build_database(data_dir, mods_dir, block_keys, world=None, extra_mods=(), auto_mods=True,
                   lang="it", log=print):
    """Carica definizioni vanilla + mod del mondo (complete, nell'ordine del gioco) + mod esplicite,
    poi (se richiesto) cerca le mod installate che contengono i blocchi ancora sconosciuti.
    Ritorna (db, info) con info = dict(world_mods_loaded, world_mods_missing, auto_mods)."""
    db = Database(log=lambda m: log(m))
    log(f"Carico definizioni del gioco da: {data_dir}")
    db.load_dir(os.path.dirname(data_dir) if os.path.basename(data_dir).lower() == "data" else data_dir, "vanilla")
    db.load_localization(data_dir, lang)

    info = {"world_mods_loaded": [], "world_mods_missing": [], "auto_mods": []}
    loaded_ids = set()
    if world:
        # Nel gioco la mod piu' in alto nella lista ha la priorita': si caricano dal basso verso l'alto.
        for m in reversed(world["mods"]):
            root = mod_root(m["id"], mods_dir, m["local_name"])
            if root is None:
                info["world_mods_missing"].append(m)
                log(f"  ! mod del mondo non installata: {m['name']} ({m['id']})")
                continue
            log(f"Carico mod del mondo: {m['name']} ({m['id']})")
            db.load_dir(root, f"mod {m['id']} {m['name']}")
            loaded_ids.add(m["id"])
            info["world_mods_loaded"].append(m)

    for m in dict.fromkeys(extra_mods):
        if m in loaded_ids:
            continue
        root = mod_root(m, mods_dir)
        if root is None:
            log(f"  ! mod {m} non trovata in {mods_dir}")
            continue
        log(f"Carico mod {m} (completa)")
        db.load_dir(root, f"mod {m}")
        loaded_ids.add(m)

    if auto_mods:
        missing = [k for k in block_keys if db.find_block(*k) is None]
        if missing:
            log(f"Cerco {len(missing)} blocchi sconosciuti tra le mod installate...")
            subs = [f"<SubtypeId>{s}</SubtypeId>" for _, s in missing if s]
            for m in sorted(mods_containing(mods_dir, subs, exclude=loaded_ids)):
                log(f"Carico mod {m} (automatica, solo blocchi/ricette mancanti)")
                db.load_dir(os.path.join(mods_dir, m), f"mod {m} (auto)", secondary=True)
                info["auto_mods"].append(m)
    return db, info


def read_ships(paths, count=1, log=print):
    total_blocks = defaultdict(int)
    ships = []
    for p in paths:
        name, grids = read_ship(p, log)
        ships.append({"name": name, "path": p, "grids": [(g, s, sum(c.values())) for g, s, c in grids]})
        for _, _, counts in grids:
            for k, v in counts.items():
                total_blocks[k] += v * count
    return ships, dict(total_blocks)


def write_csv(path, db, ships, settings, blocks, res):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Nave", " + ".join(s["name"] for s in ships)])
        if settings.get("world"):
            w.writerow(["Mondo", settings["world"]])
        w.writerow(["Assemblatore", settings["assembler"], "Resa raffineria", settings["refinery"],
                    "Quantita", settings["count"]])
        w.writerow([])
        w.writerow(["Categoria", "Nome", "Id", "Quantita"])
        for k, n in sorted(blocks.items(), key=lambda kv: -kv[1]):
            w.writerow(["Blocco", db.block_display(*k), k[1] or k[0], n])
        for c, q in sorted(res["components"].items(), key=lambda kv: -kv[1]):
            w.writerow(["Componente", db.display("Component", c), c, round(q, 2)])
        for c, q in sorted(res["ingots"].items(), key=lambda kv: -kv[1]):
            w.writerow(["Lingotto (kg)", db.display("Ingot", c), c, round(q, 2)])
        for c, q in sorted(res["ores"].items(), key=lambda kv: -kv[1]):
            w.writerow(["Minerale (kg)", db.display("Ore", c), c, round(q, 2)])
        for (t, s), n in res["unknown_blocks"].items():
            w.writerow(["Blocco sconosciuto", s, f"{t}/{s}", n])
        for (t, s), q in res["unresolved"].items():
            w.writerow(["Senza ricetta", db.display(t, s), f"{t}/{s}", round(q, 2)])


# --------------------------------------------------------------------------- CLI

def print_table(title, rows, unit=""):
    print(f"\n=== {title} ===")
    if not rows:
        print("  (nessuno)")
        return
    w = max(len(r[0]) for r in rows)
    for name, val in rows:
        print(f"  {name.ljust(w)}  {fmt(val).rjust(14)} {unit}")


def main():
    ap = argparse.ArgumentParser(
        description="Calcola componenti, lingotti e minerali di una blueprint di Space Engineers.",
        epilog=f"Autore: {__author__} <{__email__}> - {__copyright__} - licenza MIT, nessuna garanzia.")
    ap.add_argument("blueprint", nargs="+", help="file bp.sbc (o cartella che lo contiene)")
    ap.add_argument("--world", help="cartella del salvataggio (o Sandbox_config.sbc): usa le sue mod e la sua efficienza assemblatore")
    ap.add_argument("--game", help="cartella di installazione di Space Engineers (o la sua Content\\Data)")
    ap.add_argument("--mods-dir", default=first_existing(DEFAULT_MODS_DIRS), help="cartella workshop delle mod (244850)")
    ap.add_argument("--mod", action="append", default=[],
                    help="ID mod da caricare sempre (ripetibile), es. mod che cambiano le ricette")
    ap.add_argument("--vanilla", action="store_true",
                    help="solo gioco base: ignora mondo, --mod e ricerca automatica delle mod")
    ap.add_argument("--no-auto-mods", action="store_true",
                    help="non cercare automaticamente le mod per i blocchi sconosciuti")
    ap.add_argument("--assembler", type=float, default=None,
                    help="efficienza assemblatore (1 = realistico, 3, 10). Default: quella del mondo o 1")
    ap.add_argument("--refinery-yield", type=float, default=1.0,
                    help="moltiplicatore resa raffineria (1 = raffineria senza moduli, 0.7 = altoforno). Default 1")
    ap.add_argument("--count", type=int, default=1, help="numero di navi da costruire. Default 1")
    ap.add_argument("--blocks", action="store_true", help="mostra anche l'elenco dei blocchi")
    ap.add_argument("--csv", help="salva anche il risultato in un file CSV")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):      # console Windows: niente crash su caratteri strani
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass

    data_dir = find_game(args.game)
    if not data_dir:
        sys.exit("Non trovo i file del gioco: usa --game \"<percorso>\\SpaceEngineers\"")

    if args.vanilla:
        args.world, args.mod, args.no_auto_mods = None, [], True
        print("Modalita' vanilla: nessuna mod")
    world = read_world(args.world) if args.world else None
    if world:
        print(f"Mondo: {world['name']}  (assemblatore x{fmt(world['assembler_eff'])}, {len(world['mods'])} mod)")
    assembler = args.assembler if args.assembler is not None else (world["assembler_eff"] if world else 1.0)

    ships, blocks = read_ships(args.blueprint, args.count)
    for s in ships:
        print(f"Nave: {s['name']}  ({len(s['grids'])} griglie)")
        for g, size, n in s["grids"]:
            print(f"  - griglia '{g}' [{size}]: {n} blocchi")

    db, _ = build_database(data_dir, args.mods_dir, blocks, world, args.mod, not args.no_auto_mods)
    res = compute(db, blocks, assembler, args.refinery_yield)

    title_mult = f" x{args.count}" if args.count > 1 else ""
    print(f"\nImpostazioni: assemblatore x{fmt(assembler)}, resa raffineria x{fmt(args.refinery_yield)}{title_mult}")

    if args.blocks:
        rows = []
        for k, n in sorted(blocks.items(), key=lambda kv: -kv[1]):
            d = db.find_block(*k)
            tag = "  [SCONOSCIUTO]" if d is None else ("" if d["source"] == "vanilla" else f"  [{d['source']}]")
            rows.append((f"{db.block_display(*k)}{tag}", n))
        print_table("Blocchi", rows)

    print_table("Componenti", [(db.display("Component", c), q) for c, q in sorted(res["components"].items(), key=lambda kv: -kv[1])])
    print_table("Lingotti (kg)", [(db.display("Ingot", c), q) for c, q in sorted(res["ingots"].items(), key=lambda kv: -kv[1])], "kg")
    print_table("Minerali (kg)", [(db.display("Ore", c), q) for c, q in sorted(res["ores"].items(), key=lambda kv: -kv[1])], "kg")

    if res["unknown_blocks"]:
        print("\n!! Blocchi senza definizione (non conteggiati):")
        for (t, s), n in res["unknown_blocks"].items():
            print(f"   {t}/{s}: {n}")
    if res["unresolved"]:
        print("\n!! Materiali senza ricetta trovata (considerali a parte):")
        for (t, s), q in res["unresolved"].items():
            print(f"   {t}/{s}: {fmt(q)}")

    if args.csv:
        write_csv(args.csv, db, ships, {"world": world["name"] if world else "", "assembler": assembler,
                                        "refinery": args.refinery_yield, "count": args.count}, blocks, res)
        print(f"\nCSV salvato in {args.csv}")


if __name__ == "__main__":
    main()
