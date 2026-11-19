import base64
import datetime
import difflib
import hashlib
import json
import math
import os
import random
import re
import secrets
import subprocess
import sys
import textwrap
import time
import tkinter as tk
import turtle
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

API = "https://pokeapi.co/api/v2"
SPRITES = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/"
HOME = os.path.join(os.path.expanduser("~"), ".turtle_pokedex")
CACHE = os.path.join(HOME, "cache")
DB_FILE = os.path.join(HOME, "trainers.json")
MUSIC_DIR = os.path.join(HOME, "music")
os.makedirs(CACHE, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)


def load_db():
    try:
        with open(DB_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_db(db):
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)
    os.replace(tmp, DB_FILE)


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 100_000).hex()


TYPES = ["normal", "fire", "water", "electric", "grass", "ice", "fighting",
         "poison", "ground", "flying", "psychic", "bug", "rock", "ghost",
         "dragon", "dark", "steel", "fairy"]

_RAW = {
    "normal":   ("", "rock steel", "ghost"),
    "fire":     ("grass ice bug steel", "fire water rock dragon", ""),
    "water":    ("fire ground rock", "water grass dragon", ""),
    "electric": ("water flying", "electric grass dragon", "ground"),
    "grass":    ("water ground rock", "fire grass poison flying bug dragon steel", ""),
    "ice":      ("grass ground flying dragon", "fire water ice steel", ""),
    "fighting": ("normal ice rock dark steel", "poison flying psychic bug fairy", "ghost"),
    "poison":   ("grass fairy", "poison ground rock ghost", "steel"),
    "ground":   ("fire electric poison rock steel", "grass bug", "flying"),
    "flying":   ("grass fighting bug", "electric rock steel", ""),
    "psychic":  ("fighting poison", "psychic steel", "dark"),
    "bug":      ("grass psychic dark", "fire fighting poison flying ghost steel fairy", ""),
    "rock":     ("fire ice flying bug", "fighting ground steel", ""),
    "ghost":    ("psychic ghost", "dark", "normal"),
    "dragon":   ("dragon", "steel", "fairy"),
    "dark":     ("psychic ghost", "fighting dark fairy", ""),
    "steel":    ("ice rock fairy", "fire water electric steel", ""),
    "fairy":    ("fighting dragon dark", "fire poison steel", ""),
}
CHART = {a: {} for a in TYPES}
for _a, (_se, _nve, _im) in _RAW.items():
    for _d in _se.split():
        CHART[_a][_d] = 2.0
    for _d in _nve.split():
        CHART[_a][_d] = 0.5
    for _d in _im.split():
        CHART[_a][_d] = 0.0

TYPE_COLORS = {
    "normal": "#A8A878", "fire": "#F08030", "water": "#6890F0", "electric": "#F8D030",
    "grass": "#78C850", "ice": "#98D8D8", "fighting": "#C03028", "poison": "#A040A0",
    "ground": "#E0C068", "flying": "#A890F0", "psychic": "#F85888", "bug": "#A8B820",
    "rock": "#B8A038", "ghost": "#705898", "dragon": "#7038F8", "dark": "#705848",
    "steel": "#B8B8D0", "fairy": "#EE99AC",
}


def eff(attack_type, defender_types):
    m = 1.0
    for d in defender_types:
        m *= CHART.get(attack_type, {}).get(d, 1.0)
    return m


def weakness_table(defender_types):
    return {a: eff(a, defender_types) for a in TYPES}


class NotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# Networking: one request at a time, with a small gap between requests.
# Everything that was fetched once is saved to disk and reused afterwards.
# ---------------------------------------------------------------------------
_last_call = [0.0]


def throttle(gap=0.2):
    wait = gap - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def http_json(path):
    url = API + path
    last_error = None
    delay = 1

    for attempt in range(5):
        throttle()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "TurtlePokedexAdvance/1.0",
                "Accept": "application/json",
                "Connection": "close",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(path)
            if e.code not in (408, 429, 500, 502, 503, 504):
                raise
            last_error = e
            retry_after = e.headers.get("Retry-After")
            try:
                delay = min(float(retry_after), 20) if retry_after else min(2 ** attempt, 16)
            except (TypeError, ValueError):
                delay = min(2 ** attempt, 16)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                json.JSONDecodeError) as e:
            last_error = e
            delay = min(2 ** attempt, 16)

        if attempt < 4:
            time.sleep(delay + random.uniform(0, 0.5))

    raise urllib.error.URLError(
        "PokeAPI connection failed after 5 attempts: %s" % last_error
    )


def norm(q):
    q = q.strip().lower().replace(" ", "-").replace(".", "").replace("'", "")
    q = q.replace("\u2640", "-f").replace("\u2642", "-m")
    if not q or not re.fullmatch(r"[a-z0-9-]+", q):
        raise ValueError("Enter a valid Pokemon name or number.")
    return q


def cache_load(name):
    try:
        with open(os.path.join(CACHE, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def cache_save(name, data):
    try:
        with open(os.path.join(CACHE, name), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


POKE_MEM = {}
MOVE_MEM = {}


def get_pokemon(query):
    q = norm(query)
    if q in POKE_MEM:
        return POKE_MEM[q]
    data = cache_load(f"pk2_{q}.json")
    if data:
        POKE_MEM[q] = data
        return data
    raw = http_json(f"/pokemon/{q}")
    level_moves = {}
    for m in raw["moves"]:
        last = None
        for d in m["version_group_details"]:
            if d["move_learn_method"]["name"] == "level-up":
                last = d["level_learned_at"]
        if last is not None:
            level_moves[m["move"]["name"]] = last
    data = {
        "id": raw["id"],
        "name": raw["name"],
        "types": [t["type"]["name"] for t in raw["types"]],
        "stats": {s["stat"]["name"]: s["base_stat"] for s in raw["stats"]},
        "abilities": [a["ability"]["name"] + (" (hidden)" if a["is_hidden"] else "")
                      for a in raw["abilities"]],
        "height": raw["height"] / 10,
        "weight": raw["weight"] / 10,
        "moves": [m["move"]["name"] for m in raw["moves"]],
        "level_moves": sorted(level_moves.items(), key=lambda kv: kv[1]),
        "games": sorted({g["version"]["name"] for g in raw.get("game_indices", [])}),
        "past_types": [[p["generation"]["name"], [t["type"]["name"] for t in p["types"]]]
                       for p in raw.get("past_types", [])],
    }
    cache_save(f"pk2_{data['name']}.json", data)
    cache_save(f"pk2_{q}.json", data)
    POKE_MEM[q] = data
    POKE_MEM[data["name"]] = data
    return data


def get_move(name):
    if name in MOVE_MEM:
        return MOVE_MEM[name]
    data = cache_load(f"mv2_{name}.json")
    if data:
        MOVE_MEM[name] = data
        return data
    raw = http_json(f"/move/{name}")
    data = {
        "name": raw["name"],
        "type": raw["type"]["name"],
        "power": raw["power"],
        "accuracy": raw["accuracy"],
        "pp": raw["pp"],
        "priority": raw.get("priority", 0),
        "class": raw["damage_class"]["name"],
    }
    cache_save(f"mv2_{name}.json", data)
    MOVE_MEM[name] = data
    return data


def get_moves(names, limit=80):
    """Fetch moves ONE AT A TIME. Cached moves come from memory/disk."""
    out = []
    for n in names[:limit]:
        try:
            out.append(get_move(n))
        except Exception:
            continue
    return out


def all_pokemon_names():
    data = cache_load("all_names.json")
    if data:
        return data
    raw = http_json("/pokemon?limit=3000")
    names = [p["name"] for p in raw["results"]]
    cache_save("all_names.json", names)
    return names


REGIONS = ["kanto", "johto", "hoenn", "sinnoh", "unova", "kalos", "alola", "galar"]


def get_region_locations(region):
    data = cache_load("rg_%s.json" % region)
    if data:
        return data
    raw = http_json("/region/%s" % region)
    names = [l["name"] for l in raw["locations"]]
    cache_save("rg_%s.json" % region, names)
    return names


def _num_key(n):
    d = re.findall(r"\d+", n)
    return (int(d[0]) if d else 999, n)


def region_routes(region):
    names = [n for n in get_region_locations(region) if re.search(r"(^|-)route-\d+", n)]
    return sorted(names, key=_num_key)


def find_route(region, num):
    pat = re.compile(r"(^|-)route-%d($|-)" % num)
    hits = sorted((n for n in get_region_locations(region) if pat.search(n)), key=len)
    if not hits:
        raise NotFound("route %d" % num)
    return hits[0]


def get_route(loc):
    """Wild Pokemon of a route. Areas are fetched one at a time and cached."""
    data = cache_load("rt_%s.json" % loc)
    if data:
        return data
    raw = http_json("/location/%s" % loc)
    areas = []
    for a in raw.get("areas", [])[:4]:
        ar = http_json("/location-area/%s" % a["name"])
        enc = {}
        for pe in ar["pokemon_encounters"]:
            pname = pe["pokemon"]["name"]
            e = enc.setdefault(pname, {"name": pname, "methods": [], "min": 999,
                                       "max": 0, "chance": 0, "games": []})
            for vd in pe["version_details"]:
                g = vd["version"]["name"]
                if g not in e["games"]:
                    e["games"].append(g)
                for d in vd["encounter_details"]:
                    m = d["method"]["name"]
                    if m not in e["methods"]:
                        e["methods"].append(m)
                    e["min"] = min(e["min"], d["min_level"])
                    e["max"] = max(e["max"], d["max_level"])
                    e["chance"] = max(e["chance"], d["chance"])
        rows = sorted(enc.values(), key=lambda e: (-e["chance"], e["name"]))
        areas.append({"name": a["name"], "rows": rows})
    region = raw.get("region")
    data = {"name": raw["name"], "region": region["name"] if region else "", "areas": areas}
    cache_save("rt_%s.json" % loc, data)
    return data


def get_sprite_bytes(pid, back):
    fn = os.path.join(CACHE, "sp_%s_%s.png" % (pid, "b" if back else "f"))
    if os.path.exists(fn):
        with open(fn, "rb") as f:
            return f.read()
    sub = "back/" if back else ""
    urls = []
    if pid <= 386:
        urls.append(SPRITES + "versions/generation-iii/firered-leafgreen/%s%d.png" % (sub, pid))
    urls.append(SPRITES + "%s%d.png" % (sub, pid))
    for u in urls:
        try:
            throttle(0.1)
            req = urllib.request.Request(u, headers={"User-Agent": "turtle-pokedex"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
            try:
                with open(fn, "wb") as f:
                    f.write(data)
            except OSError:
                pass
            return data
        except Exception:
            continue
    return None


def rank_team(team, opp):
    rows = []
    for p in team:
        off = max(eff(t, opp["types"]) for t in p["types"])
        dfn = max(eff(t, p["types"]) for t in opp["types"])
        fast = 0.4 if p["stats"]["speed"] > opp["stats"]["speed"] else 0.0
        bulk = (p["stats"]["hp"] + p["stats"]["defense"] + p["stats"]["special-defense"]) / 600
        power = max(p["stats"]["attack"], p["stats"]["special-attack"]) / 200
        score = off * 2 - dfn * 1.5 + fast + bulk + power
        rows.append((score, p, off, dfn))
    rows.sort(key=lambda r: -r[0])
    return rows


def best_moves(p, opp, count=4):
    scored = []
    for m in get_moves(p["moves"], 80):
        if not m["power"] or m["class"] == "status":
            continue
        mult = eff(m["type"], opp["types"])
        stab = 1.5 if m["type"] in p["types"] else 1.0
        stat = p["stats"]["attack" if m["class"] == "physical" else "special-attack"]
        acc = (m["accuracy"] or 100) / 100
        scored.append((m["power"] * acc * mult * stab * stat / 100, m, mult))
    scored.sort(key=lambda r: -r[0])
    picked, used = [], set()
    for s in scored:
        if s[1]["type"] not in used or len(picked) >= 2:
            picked.append(s)
            used.add(s[1]["type"])
        if len(picked) == count:
            break
    return picked


LEVEL = 50
BAD_MOVES = {
    "explosion", "self-destruct", "misty-explosion", "final-gambit", "memento",
    "hyper-beam", "giga-impact", "blast-burn", "hydro-cannon", "frenzy-plant",
    "roar-of-time", "rock-wrecker", "prismatic-laser", "meteor-assault", "eternabeam",
    "fly", "dig", "dive", "bounce", "solar-beam", "solar-blade", "skull-bash",
    "razor-wind", "sky-attack", "freeze-shade", "geomancy", "shadow-force",
    "phantom-force", "sky-drop", "focus-punch", "last-resort", "dream-eater",
    "fake-out", "first-impression", "belch", "mind-blown", "steel-beam", "chloroblast",
    "struggle", "synchronoise", "sucker-punch", "natural-gift", "fling", "uproar",
    "outrage", "petal-dance", "thrash", "future-sight", "doom-desire", "spit-up",
}
TACKLE = {"name": "tackle", "type": "normal", "power": 40, "accuracy": 100, "pp": 35,
          "priority": 0, "class": "physical"}
STRUGGLE = {"name": "struggle", "type": "normal", "power": 50, "acc": None,
            "class": "physical", "priority": 0, "pp": 1, "max": 1}


def pick_moveset(p, level=LEVEL):
    learned = [n for n, lv in p.get("level_moves", []) if lv <= level]
    pool = get_moves(learned, 60)

    def damaging(ms):
        return [m for m in ms if m["power"] and m["class"] != "status"
                and m["name"] not in BAD_MOVES]
    dmg = damaging(pool)
    if len(dmg) < 4:
        have = set(learned)
        dmg += damaging(get_moves([n for n in p["moves"] if n not in have][:25], 25))

    def score(m):
        return m["power"] * (m["accuracy"] or 100) / 100 * (1.5 if m["type"] in p["types"] else 1)
    dmg.sort(key=score, reverse=True)
    chosen, per_type = [], {}
    for m in dmg:
        if per_type.get(m["type"], 0) < 2:
            chosen.append(m)
            per_type[m["type"]] = per_type.get(m["type"], 0) + 1
        if len(chosen) == 4:
            break
    for m in dmg:
        if len(chosen) == 4:
            break
        if m not in chosen:
            chosen.append(m)
    if not chosen:
        chosen = [dict(TACKLE)]
    out = []
    for m in chosen:
        pp = min(m["pp"] or 20, 25)
        out.append({"name": m["name"], "type": m["type"], "power": m["power"],
                    "acc": m["accuracy"], "class": m["class"],
                    "priority": m.get("priority", 0), "pp": pp, "max": pp})
    return out


def make_mon(p, level=LEVEL):
    s = p["stats"]

    def st(k):
        return int((2 * s[k] + 15) * level / 100) + 5
    hp = int((2 * s["hp"] + 15) * level / 100) + level + 10
    return {"name": p["name"], "id": p["id"], "types": p["types"], "lv": level,
            "maxhp": hp, "hp": hp, "disp": float(hp), "atk": st("attack"),
            "def": st("defense"), "spa": st("special-attack"), "spd": st("special-defense"),
            "spe": st("speed"), "moves": pick_moveset(p, level), "data": p}


def calc_damage(att, dfn, mv):
    if mv["class"] == "physical":
        a, d = att["atk"], dfn["def"]
    else:
        a, d = att["spa"], dfn["spd"]
    base = ((2 * att["lv"] / 5 + 2) * mv["power"] * a / max(d, 1)) / 50 + 2
    stab = 1.5 if mv["type"] in att["types"] else 1.0
    te = eff(mv["type"], dfn["types"])
    crit = random.random() < 1 / 16
    dmg = int(base * stab * te * (1.5 if crit else 1.0) * random.uniform(0.85, 1.0))
    if te > 0:
        dmg = max(1, dmg)
    return dmg, te, crit


def cpu_pick_move(me, foe):
    opts = [(k, m) for k, m in enumerate(me["moves"]) if m["pp"] > 0]
    if not opts:
        return -1
    if random.random() < 0.1:
        return random.choice(opts)[0]

    def val(m):
        return (m["power"] * (m["acc"] or 100) / 100 * eff(m["type"], foe["types"])
                * (1.5 if m["type"] in me["types"] else 1.0))
    return max(opts, key=lambda km: val(km[1]))[0]


def replacement_score(m, foe):
    off = max(eff(t, foe["types"]) for t in m["types"])
    dfn = max(eff(t, m["types"]) for t in foe["types"])
    return off * 2 - dfn * 1.5 + m["hp"] / m["maxhp"]


W, H = 980, 720
UI_FONT = "Arial"
GBA_FONT = "Courier"
screen = cv = None
pen = writer = bg_t = hud_t = box_t = dlg_t = mnu_t = None
buttons = []
modal = None
busy = False
user = None
sprite_items = []
IMGS = {}


def new_turtle():
    t = turtle.Turtle(visible=False)
    t.penup()
    t.speed(0)
    return t


def setup():
    global screen, cv, pen, writer, bg_t, hud_t, box_t, dlg_t, mnu_t
    screen = turtle.Screen()
    screen.setup(W, H)
    screen.title("Turtle Pokedex Advance")
    screen.tracer(0)
    cv = screen.getcanvas()
    pen, writer, bg_t, hud_t, box_t, dlg_t, mnu_t = [new_turtle() for _ in range(7)]
    screen.onclick(on_click)


def pretty(s):
    return s.replace("-", " ").title()


def rect(x, y, w, h, fill, outline="black", t=None, width=1):
    t = t or pen
    t.goto(x, y)
    t.setheading(0)
    t.pensize(width)
    t.color(outline, fill)
    t.pendown()
    t.begin_fill()
    for d in (w, h, w, h):
        t.forward(d)
        t.left(90)
    t.end_fill()
    t.penup()


def ellipse(t, cx, cy, rx, ry, fill, outline, width=3):
    t.goto(cx + rx, cy)
    t.pensize(width)
    t.color(outline, fill)
    t.pendown()
    t.begin_fill()
    for k in range(1, 49):
        a = 2 * math.pi * k / 48
        t.goto(cx + rx * math.cos(a), cy + ry * math.sin(a))
    t.end_fill()
    t.penup()


def text(x, y, s, size=12, color="black", bold=False, align="left", t=None, font=None):
    t = t or writer
    t.goto(x, y)
    t.color(color)
    t.write(s, align=align, font=(font or UI_FONT, size, "bold" if bold else "normal"))


def button(x, y, label, cb, w=200, h=38, color="#f8d860"):
    rect(x - w / 2, y - h / 2, w, h, color, "#383838", width=3)
    text(x, y - 8, label, 12, "black", True, "center")
    buttons.append((x - w / 2, y - h / 2, x + w / 2, y + h / 2, cb))


def badge(x, y, t):
    rect(x, y, 90, 24, TYPE_COLORS.get(t, "#999"))
    text(x + 45, y + 4, t.upper(), 10, "white", True, "center")


def clear_all():
    global buttons
    buttons = []
    for t in (pen, writer, bg_t, hud_t, box_t, dlg_t, mnu_t):
        t.clear()
    for it in sprite_items:
        try:
            cv.delete(it)
        except tk.TclError:
            pass
    del sprite_items[:]


def page(title, subtitle=""):
    clear_all()
    screen.bgcolor("#3050a0")
    rect(-W / 2 + 14, -H / 2 + 14, W - 28, H - 28, "#f8f8e8", "#182030", width=6)
    rect(-W / 2 + 20, H / 2 - 90, W - 40, 70, "#283858", "#e0a040", width=4)
    text(-W / 2 + 45, H / 2 - 68, title, 24, "white", True)
    if subtitle:
        text(W / 2 - 45, H / 2 - 62, subtitle, 12, "#f8d860", True, "right")
    screen.update()


def finish():
    screen.update()


def on_click(x, y):
    global busy
    if modal is not None:
        for x1, y1, x2, y2, val in modal["hits"]:
            if x1 <= x <= x2 and y1 <= y <= y2:
                modal["val"] = val
                modal["done"] = True
                return
        return
    if busy:
        return
    for x1, y1, x2, y2, cb in reversed(buttons):
        if x1 <= x <= x2 and y1 <= y <= y2:
            busy = True
            try:
                cb()
            except (turtle.Terminator, tk.TclError):
                pass
            except Exception as e:
                try:
                    notice("Something went wrong", [str(e)[:110]],
                           menu if user else login_screen)
                except Exception:
                    pass
            finally:
                busy = False
            return


def wait_click(hits):
    global modal
    modal = {"hits": hits, "done": False, "val": None}
    screen.update()
    while not modal["done"]:
        screen.update()
        time.sleep(0.02)
    val = modal["val"]
    modal = None
    return val


def pause(sec):
    end = time.time() + sec
    while time.time() < end:
        screen.update()
        time.sleep(0.02)


def ask(title, prompt):
    v = screen.textinput(title, prompt)
    return v.strip() if v else None


def loading(msg="Loading... (talking to PokeAPI)"):
    text(0, 0, msg, 18, "#444", True, "center")
    screen.update()


def progress(msg):
    dlg_t.clear()
    text(0, -40, msg, 16, "#283858", True, "center", dlg_t)
    screen.update()


def notice(title, lines, back):
    page(title, user or "")
    y = 220
    for ln in lines:
        text(-400, y, ln, 14)
        y -= 28
    button(0, -300, "OK", back)
    finish()


def lookup(query):
    try:
        query = query.strip()
        if not query:
            raise RuntimeError("Enter a Pokemon name or number.")
        return get_pokemon(query)
    except ValueError as e:
        raise RuntimeError(str(e))
    except NotFound:
        try:
            close = difflib.get_close_matches(norm(query), all_pokemon_names(), 5, 0.5)
        except Exception:
            close = []
        raise RuntimeError("Not found: '%s'. %s" % (
            query, ("Did you mean: " + ", ".join(close)) if close else "Check the spelling."))
    except urllib.error.HTTPError as e:
        raise RuntimeError("PokeAPI returned HTTP %d for '%s'." % (e.code, query))
    except (urllib.error.URLError, OSError) as e:
        reason = str(getattr(e, "reason", e))[:90]
        raise RuntimeError("Could not get '%s' (not cached yet): %s" % (query, reason))


SIDES = []
PVP = False
ITEM = [None, None]
SLOT = {0: (-240, -30), 1: (250, 150)}


def hp_color(f):
    return "#40c040" if f > 0.5 else "#f0c020" if f > 0.2 else "#e03030"


def active(i):
    s = SIDES[i]
    return s["mons"][s["active"]]


def alive(s):
    return [k for k, m in enumerate(s["mons"]) if m["hp"] > 0]


def lab(i):
    n = active(i)["name"].upper()
    if PVP:
        return "%s's %s" % (SIDES[i]["name"].upper(), n)
    return n if i == 0 else "Foe " + n


def make_image(pid, back):
    key = (pid, back)
    if key in IMGS:
        return IMGS[key]
    data = get_sprite_bytes(pid, back)
    img = None
    if data:
        try:
            img = tk.PhotoImage(master=cv, data=base64.b64encode(data).decode())
            z = max(1, 192 // max(img.width(), 1))
            if z > 1:
                img = img.zoom(z)
        except tk.TclError:
            img = None
    IMGS[key] = img
    return img


def show_active(i):
    m = active(i)
    x, y = SLOT[i]
    img = make_image(m["id"], i == 0)
    if img is not None:
        ITEM[i] = cv.create_image(x, -y, image=img)
        sprite_items.append(ITEM[i])
    else:
        ITEM[i] = None
        ellipse(bg_t, x, y, 70, 60, TYPE_COLORS.get(m["types"][0], "#999"), "#202020", 4)
    SIDES[i]["shown"] = True


def clear_battle():
    for t in (bg_t, hud_t, box_t, dlg_t, mnu_t):
        t.clear()
    for it in sprite_items:
        try:
            cv.delete(it)
        except tk.TclError:
            pass
    del sprite_items[:]
    ITEM[0] = ITEM[1] = None


def draw_hud():
    hud_t.clear()
    for i in (0, 1):
        s = SIDES[i]
        if not s.get("shown"):
            continue
        m = active(i)
        top = (i == 1)
        x0, y0, w, h = (-470, 235, 370, 100) if top else (100, -160, 370, 120)
        rect(x0, y0, w, h, "#f8f0d0", "#484848", hud_t, 5)
        text(x0 + 16, y0 + h - 40, m["name"].upper(), 19, "#202020", True, t=hud_t, font=GBA_FONT)
        text(x0 + w - 16, y0 + h - 40, "Lv%d" % m["lv"], 17, "#202020", True, "right", hud_t, GBA_FONT)
        by = y0 + (22 if top else 44)
        text(x0 + 16, by - 3, "HP", 13, "#c08020", True, t=hud_t, font=GBA_FONT)
        rect(x0 + 62, by, 280, 14, "#505050", "#202020", hud_t, 2)
        f = max(0.0, min(1.0, m["disp"] / m["maxhp"]))
        if f > 0:
            rect(x0 + 62, by, 280 * f, 14, hp_color(f), hp_color(f), hud_t, 1)
        if PVP or not top:
            text(x0 + w - 16, by - 22, "%d/%d" % (round(m["disp"]), m["maxhp"]), 13,
                 "#202020", True, "right", hud_t, GBA_FONT)


def draw_field(sprites=True):
    clear_battle()
    screen.bgcolor("#d8ecc0")
    rect(-W / 2, -170, W, 540, "#d8ecc0", "#d8ecc0", bg_t)
    rect(-W / 2, 60, W, 310, "#e6f2d4", "#e6f2d4", bg_t)
    ellipse(bg_t, 250, 70, 190, 30, "#a8c878", "#708848")
    ellipse(bg_t, -240, -110, 215, 36, "#a8c878", "#708848")
    if sprites:
        for i in (0, 1):
            show_active(i)
    draw_hud()
    screen.update()


def box_full():
    box_t.clear()
    rect(-478, -348, 956, 176, "#2c3c50", "#e0a040", box_t, 8)
    rect(-466, -336, 932, 152, "#2c3c50", "#f8f8f8", box_t, 2)


def box_split():
    box_t.clear()
    dlg_t.clear()
    rect(-478, -348, 956, 176, "#2c3c50", "#e0a040", box_t, 8)


def say(msg, hold=0.9):
    mnu_t.clear()
    box_full()
    lines = textwrap.wrap(msg, 46) or [""]
    total = sum(len(l) for l in lines)
    for count in range(0, total + 1, 2):
        dlg_t.clear()
        left = count
        for j, l in enumerate(lines[:3]):
            seg = l[:left]
            left -= len(seg)
            y = -228 - j * 40
            text(-449, y - 2, seg, 22, "#585858", True, t=dlg_t, font=GBA_FONT)
            text(-451, y, seg, 22, "white", True, t=dlg_t, font=GBA_FONT)
        screen.update()
        time.sleep(0.012)
    pause(hold)


def prompt_left(lines):
    for j, l in enumerate(lines):
        y = -228 - j * 46
        text(-449, y - 2, l, 22, "#585858", True, t=dlg_t, font=GBA_FONT)
        text(-451, y, l, 22, "white", True, t=dlg_t, font=GBA_FONT)


def choose(items, x0, ytop, bw, bh, gap=6, cols=2, extra=()):
    mnu_t.clear()
    hits = []
    for k, it in enumerate(items):
        r, c = divmod(k, cols)
        x = x0 + c * (bw + gap)
        y = ytop - r * (bh + gap)
        on = it.get("on", True)
        rect(x, y - bh, bw, bh, it.get("fill", "#f8f8f8") if on else "#a8a8a8", "#404858", mnu_t, 4)
        sub = it.get("sub")
        size = 17 if len(it["label"]) <= 12 else 13
        text(x + 14, y - (34 if sub else bh / 2 + 8), it["label"], size, "#202020", True,
             t=mnu_t, font=GBA_FONT)
        if sub:
            text(x + 14, y - 60, sub, 11, it.get("subcol", "#404040"), True, t=mnu_t, font=GBA_FONT)
        if on:
            hits.append((x, y - bh, x + bw, y, it["val"]))
    for label, val, x, y, w, h in extra:
        rect(x, y - h, w, h, "#f8d860", "#404858", mnu_t, 4)
        text(x + w / 2, y - h / 2 - 8, label, 16, "#202020", True, "center", mnu_t, GBA_FONT)
        hits.append((x, y - h, x + w, y, val))
    return wait_click(hits)


def blink(i):
    it = ITEM[i]
    if it is None:
        pause(0.3)
        return
    for _ in range(4):
        cv.itemconfigure(it, state="hidden")
        pause(0.05)
        cv.itemconfigure(it, state="normal")
        pause(0.05)


def faint_anim(i):
    it = ITEM[i]
    if it is None:
        pause(0.3)
        return
    for _ in range(8):
        cv.move(it, 0, 8)
        screen.update()
        time.sleep(0.02)
    cv.delete(it)
    ITEM[i] = None


def drain(m, new_hp):
    start = m["disp"]
    for k in range(1, 21):
        m["disp"] = start + (new_hp - start) * k / 20
        draw_hud()
        screen.update()
        time.sleep(0.02)
    m["disp"] = float(new_hp)
    m["hp"] = new_hp
    draw_hud()


def pass_screen(i):
    clear_battle()
    screen.bgcolor("#202838")
    text(0, 120, "Pass the controls to", 26, "white", True, "center", dlg_t, GBA_FONT)
    text(0, 60, SIDES[i]["name"].upper(), 40, "#f8d030", True, "center", dlg_t, GBA_FONT)
    text(0, 15, "(Don't peek at the other trainer's moves!)", 14, "#a0a8b8", False, "center",
         dlg_t, GBA_FONT)
    choose([{"label": "READY", "val": True}], -150, -60, 300, 80, cols=1)


def confirm(question):
    box_split()
    prompt_left(textwrap.wrap(question, 24)[:2])
    return choose([{"label": "YES", "val": True}, {"label": "NO", "val": False}],
                  12, -180, 228, 72)


def party_screen(i, forced):
    s = SIDES[i]
    clear_battle()
    screen.bgcolor("#385078")
    hits = []
    for k, m in enumerate(s["mons"]):
        c, r = k % 2, k // 2
        x, ytop = -470 + c * 490, 320 - r * 122
        fnt, act = m["hp"] <= 0, k == s["active"]
        fill = "#a0a0a0" if fnt else "#f8e0a8" if act else "#f8f8f8"
        rect(x, ytop - 112, 450, 112, fill, "#303848", bg_t, 5)
        text(x + 16, ytop - 44, m["name"].upper(), 20, "#202020", True, t=dlg_t, font=GBA_FONT)
        text(x + 434, ytop - 44, "Lv%d" % m["lv"], 17, "#202020", True, "right", dlg_t, GBA_FONT)
        f = max(0.0, m["hp"] / m["maxhp"])
        rect(x + 16, ytop - 76, 280, 14, "#505050", "#202020", bg_t, 2)
        if f > 0:
            rect(x + 16, ytop - 76, 280 * f, 14, hp_color(f), hp_color(f), bg_t, 1)
        tag = "FNT" if fnt else "ACTIVE" if act else ""
        text(x + 434, ytop - 74, tag, 13, "#b02020", True, "right", dlg_t, GBA_FONT)
        text(x + 16, ytop - 102, "%s  %d/%d" % ("/".join(m["types"]).upper(), m["hp"], m["maxhp"]),
             11, "#404040", True, t=dlg_t, font=GBA_FONT)
        if not fnt and not act:
            hits.append((x, ytop - 112, x + 450, ytop, k))
    text(-470, -120, "Choose a POK\u00e9MON." if forced else "Switch to which POK\u00e9MON?",
         20, "white", True, t=dlg_t, font=GBA_FONT)
    if not forced:
        rect(-470, -230, 200, 56, "#f8d860", "#404858", bg_t, 4)
        text(-370, -212, "CANCEL", 16, "#202020", True, "center", dlg_t, GBA_FONT)
        hits.append((-470, -230, -270, -174, None))
    screen.update()
    return wait_click(hits)


def main_menu(i):
    box_split()
    prompt_left(["What will", "%s do?" % active(i)["name"].upper()])
    return choose([{"label": "FIGHT", "val": "fight", "fill": "#f8c8c0"},
                   {"label": "BAG", "val": "bag", "fill": "#f8e8a0"},
                   {"label": "POK\u00e9MON", "val": "mon", "fill": "#c8f0c0"},
                   {"label": "RUN", "val": "run", "fill": "#c8d8f8"}],
                  12, -180, 228, 72)


def move_menu(i):
    m = active(i)
    if all(mv["pp"] <= 0 for mv in m["moves"]):
        say("%s has no moves left!" % m["name"].upper())
        return -1
    box_split()
    prompt_left(["Choose a move."])
    items = [{"label": mv["name"].replace("-", " ").upper(), "val": k,
              "sub": "%s  PP %d/%d" % (mv["type"].upper(), mv["pp"], mv["max"]),
              "fill": TYPE_COLORS.get(mv["type"], "#f8f8f8"), "on": mv["pp"] > 0,
              "subcol": "#101010"} for k, mv in enumerate(m["moves"])]
    return choose(items, 12, -180, 228, 72, extra=[("BACK", None, -440, -280, 200, 56)])


def bag_menu(i):
    s, m = SIDES[i], active(i)
    box_split()
    prompt_left(["Choose an item."])
    c = choose([{"label": "POTION x%d" % s["potions"], "val": "potion", "sub": "Heals 60 HP"}],
               12, -180, 228, 72, extra=[("BACK", None, 250, -180, 200, 72)])
    if c == "potion":
        if s["potions"] <= 0 or m["hp"] >= m["maxhp"]:
            say("It won't have any effect.")
            return None
        return "potion"
    return None


def human_action(i):
    if PVP:
        pass_screen(i)
    while True:
        draw_field()
        c = main_menu(i)
        if c == "fight":
            r = move_menu(i)
            if r is not None:
                return ("move", r)
        elif c == "bag":
            r = bag_menu(i)
            if r:
                return (r,)
        elif c == "mon":
            idx = party_screen(i, False)
            if idx is not None:
                return ("switch", idx)
        elif c == "run":
            if confirm("Give up the battle?"):
                return ("forfeit",)


def cpu_action(i):
    return ("move", cpu_pick_move(active(i), active(1 - i)))


def get_action(i):
    return cpu_action(i) if SIDES[i]["cpu"] else human_action(i)


def pick_replacement(i):
    s = SIDES[i]
    ok = alive(s)
    if len(ok) == 1:
        return ok[0]
    if s["cpu"]:
        foe = active(1 - i)
        return max(ok, key=lambda k: replacement_score(s["mons"][k], foe))
    if PVP:
        pass_screen(i)
    idx = party_screen(i, True)
    return idx


def send_out(i, idx, first=False):
    s = SIDES[i]
    old = active(i)
    if not first and old["hp"] > 0:
        say("%s, come back!" % old["name"].upper(), 0.5)
    if ITEM[i] is not None:
        cv.delete(ITEM[i])
        ITEM[i] = None
    s["active"] = idx
    m = active(i)
    m["disp"] = float(m["hp"])
    s["shown"] = False
    show_active(i)
    draw_hud()
    if not PVP and i == 0:
        say("Go! %s!" % m["name"].upper())
    else:
        say("%s sent out %s!" % (s["name"].upper(), m["name"].upper()))


def check_faint(i):
    m = active(i)
    if m["hp"] <= 0 and not m.get("fnt_shown"):
        m["fnt_shown"] = True
        say("%s fainted!" % lab(i))
        faint_anim(i)


def use_move(i, idx):
    am, dm = active(i), active(1 - i)
    mv = STRUGGLE if idx < 0 else am["moves"][idx]
    if idx >= 0:
        mv["pp"] -= 1
    say("%s used %s!" % (lab(i), mv["name"].replace("-", " ").upper()), 0.5)
    if mv["acc"] is not None and random.random() * 100 >= mv["acc"]:
        say("%s's attack missed!" % lab(i))
        return
    dmg, te, crit = calc_damage(am, dm, mv)
    if te == 0:
        say("It doesn't affect %s..." % lab(1 - i))
        return
    blink(1 - i)
    drain(dm, max(0, dm["hp"] - dmg))
    if crit:
        say("A critical hit!", 0.6)
    if te > 1:
        say("It's super effective!", 0.7)
    elif te < 1:
        say("It's not very effective...", 0.7)
    if idx < 0:
        say("%s is hit with recoil!" % lab(i), 0.5)
        drain(am, max(0, am["hp"] - max(1, dmg // 4)))
    check_faint(1 - i)
    check_faint(i)


def use_potion(i):
    s, m = SIDES[i], active(i)
    s["potions"] -= 1
    say("%s used a POTION on %s!" % (s["name"].upper(), m["name"].upper()), 0.5)
    drain(m, min(m["maxhp"], m["hp"] + 60))
    say("%s's HP was restored." % m["name"].upper())


def resolve(acts):
    order = []
    for i, a in enumerate(acts):
        m = active(i)
        if a[0] == "move":
            pri = STRUGGLE["priority"] if a[1] < 0 else m["moves"][a[1]]["priority"]
        else:
            pri = 10
        order.append((pri, m["spe"], random.random(), i))
    order.sort(reverse=True)
    for _, _, _, i in order:
        a = acts[i]
        if a[0] == "switch":
            send_out(i, a[1])
        elif a[0] == "potion":
            use_potion(i)
        else:
            if active(i)["hp"] <= 0 or active(1 - i)["hp"] <= 0:
                continue
            use_move(i, a[1])


def run_battle(s0, s1):
    global SIDES, PVP
    SIDES = [s0, s1]
    PVP = not (s0["cpu"] or s1["cpu"])
    for s in SIDES:
        s["active"] = alive(s)[0]
        s["shown"] = False
    draw_field(sprites=False)
    if PVP:
        say("%s VS %s!" % (s0["name"].upper(), s1["name"].upper()), 1.1)
    else:
        say("%s wants to battle!" % s1["name"].upper(), 1.1)
    send_out(1, s1["active"], first=True)
    send_out(0, s0["active"], first=True)
    while True:
        acts = [get_action(0), get_action(1)]
        for i in (0, 1):
            if acts[i][0] == "forfeit":
                say("%s gave up the battle!" % SIDES[i]["name"].upper())
                return 1 - i
        resolve(acts)
        for i in (0, 1):
            if not alive(SIDES[i]):
                say("%s is out of usable POK\u00e9MON!" % SIDES[i]["name"].upper())
                return 1 - i
        for i in (0, 1):
            if active(i)["hp"] <= 0:
                send_out(i, pick_replacement(i))


def pokeball(cx, cy, r):
    t = pen
    t.pensize(5)
    t.goto(cx + r, cy)
    t.setheading(90)
    t.color("#202020", "#e03030")
    t.pendown()
    t.begin_fill()
    t.circle(r, 180)
    t.end_fill()
    t.penup()
    t.goto(cx - r, cy)
    t.setheading(270)
    t.color("#202020", "#f8f8f8")
    t.pendown()
    t.begin_fill()
    t.circle(r, 180)
    t.end_fill()
    t.penup()
    t.goto(cx - r, cy)
    t.pensize(8)
    t.pendown()
    t.goto(cx + r, cy)
    t.penup()
    t.goto(cx, cy - r * 0.28)
    t.setheading(0)
    t.pensize(5)
    t.color("#202020", "#f8f8f8")
    t.pendown()
    t.begin_fill()
    t.circle(r * 0.28)
    t.end_fill()
    t.penup()


def title_screen():
    global user
    user = None
    clear_all()
    screen.bgcolor("#1c2c68")
    for k in range(0, 720, 60):
        rect(-W / 2, -360 + k, W, 30, "#243a80", "#243a80")
    pokeball(0, 90, 120)
    text(4, 232, "POK\u00e9DEX", 54, "#202020", True, "center", font=GBA_FONT)
    text(0, 236, "POK\u00e9DEX", 54, "#f8d030", True, "center", font=GBA_FONT)
    text(0, 205, "T U R T L E   A D V A N C E", 18, "white", True, "center", font=GBA_FONT)
    button(0, -150, "PRESS START", login_screen, 320, 60, "#f8d030")
    text(0, -250, "Trainer accounts  |  2-player battles  |  Live Pokedex", 14, "#a8b8f0",
         False, "center", font=GBA_FONT)
    finish()


def login_screen():
    global user
    user = None
    page("POK\u00e9DEX", "Not signed in")
    text(0, 150, "Trainer Account", 26, "#222", True, "center")
    text(0, 110, "Log in to use your team, box and battles.", 14, "#444", False, "center")
    button(0, 40, "Log In", do_login, 260, 48)
    button(0, -30, "Create Account", do_register, 260, 48)
    button(0, -100, "Quick Pokedex (guest)", guest, 260, 48, "#ddd")
    button(0, -170, "Quit", lambda: turtle.bye(), 260, 48, "#ddd")
    finish()


def guest():
    global user
    user = "guest"
    menu()


def do_register():
    db = load_db()
    name = ask("Create account", "Choose a trainer name:")
    if not name:
        return
    if name.lower() in (k.lower() for k in db) or name.lower() in ("guest", "rival"):
        return notice("Name taken", ["That trainer name already exists."], login_screen)
    pw = ask("Create account", "Choose a password (4+ characters):")
    if not pw or len(pw) < 4:
        return notice("Too short", ["Password needs at least 4 characters."], login_screen)
    salt = secrets.token_hex(16)
    db[name] = {"salt": salt, "hash": hash_pw(pw, salt), "team": [], "box": [],
                "favs": [], "wins": 0, "losses": 0}
    save_db(db)
    notice("Welcome, " + name + "!", ["Account created. Log in to begin."], login_screen)


def do_login():
    global user
    db = load_db()
    name = ask("Log in", "Trainer name:")
    if not name:
        return
    pw = ask("Log in", "Password:") or ""
    key = next((k for k in db if k.lower() == name.lower()), None)
    acct = db.get(key) if key is not None else None
    if (not acct or "salt" not in acct or "hash" not in acct
            or hash_pw(pw, acct["salt"]) != acct["hash"]):
        return notice("Login failed", ["Wrong trainer name or password."], login_screen)
    user = key
    menu()


def rank_title(wins):
    return ("CHAMPION" if wins >= 25 else "ACE TRAINER" if wins >= 10
            else "YOUNGSTER" if wins >= 3 else "ROOKIE")


def menu():
    page("POK\u00e9DEX", "Trainer: " + user)
    text(0, 222, "What would you like to do?", 20, "#222", True, "center")
    button(-150, 160, "Pokedex Lookup", dex_prompt, 270, 50)
    button(150, 160, "Weakness Checker", weakness_prompt, 270, 50)
    button(-150, 98, "My Team & Box", team_screen, 270, 50)
    button(150, 98, "Battle Advisor", battle_prompt, 270, 50, "#a8e0a0")
    button(-150, 36, "TRAINER BATTLE", open_battle_setup, 270, 50, "#f8a0a0")
    button(150, 36, "Trainer Card", trainer_card, 270, 50, "#a8c8f8")
    button(-150, -26, "Browse All Pokemon", browse_screen, 270, 50)
    button(150, -26, "Compare Pokemon", compare_prompt, 270, 50, "#e8c8f8")
    button(-150, -88, "Pokemon of the Day", daily_pokemon, 270, 50, "#f8e8a0")
    button(150, -88, "Favorites", favorites_screen, 270, 50, "#f8c8d8")
    button(-150, -150, "Route Maps", route_home, 270, 50, "#a8e8e0")
    button(150, -150, "Cache", cache_screen, 270, 50, "#ddd")
    button(-150, -212, "Music", music_home, 270, 50, "#f8d0f0")
    button(150, -212, "Log Out", title_screen, 270, 50, "#ddd")
    if user == "guest":
        text(0, -275, "Guest mode: nothing is saved. Create an account to save and battle.",
             12, "#a00", False, "center")
    finish()


def trainer_card():
    if user == "guest":
        return notice("Guest mode", ["Create an account to get a Trainer Card."], menu)
    acct = load_db()[user]
    wins, losses = acct.get("wins", 0), acct.get("losses", 0)
    page("TRAINER CARD", "Trainer: " + user)
    rect(-420, -250, 840, 450, "#e8c850", "#806010", width=6)
    rect(-400, -230, 800, 410, "#f8f0b0", "#c0a030", width=2)
    tid = int(hashlib.sha1(user.encode()).hexdigest()[:8], 16) % 100000
    rows = ["NAME   %s" % user.upper(), "ID No. %05d" % tid,
            "RANK   %s" % rank_title(wins), "BATTLES WON %d   LOST %d" % (wins, losses),
            "TEAM %d/6   BOX %d   FAVS %d" % (len(acct["team"]), len(acct["box"]),
                                              len(acct.get("favs", [])))]
    for k, r in enumerate(rows):
        text(-370, 130 - k * 50, r, 22, "#403000", True, font=GBA_FONT)
    text(-370, -150, "BADGES", 16, "#403000", True, font=GBA_FONT)
    colors = ["#c0c0c0", "#60a0f0", "#f8d030", "#f08030", "#78c850", "#f85888", "#a040a0", "#e03030"]
    for k, need in enumerate([1, 3, 5, 8, 12, 16, 20, 25]):
        got = wins >= need
        ellipse(pen, -280 + k * 80, -195, 26, 26, colors[k] if got else "#d8d0a0", "#403000", 3)
    button(0, -300, "Back", menu, 200, 38, "#ddd")
    finish()


def dex_prompt():
    q = ask("Pokedex", "Pokemon name or number (e.g. pikachu or 25):")
    if q:
        show_pokemon(q)


def show_pokemon(q):
    page("POK\u00e9DEX", "Trainer: " + user)
    loading()
    try:
        p = lookup(q)
    except RuntimeError as e:
        return notice("Lookup failed", [str(e)], menu)
    page("#%03d  %s" % (p["id"], p["name"].upper()), "Trainer: " + user)
    for i, t in enumerate(p["types"]):
        badge(-440 + i * 100, 235, t)
    text(-440, 205, "BASE STATS", 14, "#222", True)
    y = 170
    labels = [("hp", "HP"), ("attack", "Attack"), ("defense", "Defense"),
              ("special-attack", "Sp. Atk"), ("special-defense", "Sp. Def"), ("speed", "Speed")]
    for key, label in labels:
        v = p["stats"][key]
        text(-440, y - 4, label, 12, "#222", True)
        rect(-340, y - 8, 300, 20, "#ddd")
        col = "#e74c3c" if v < 60 else "#f39c12" if v < 90 else "#27ae60" if v < 120 else "#2980b9"
        rect(-340, y - 8, min(v, 255) / 255 * 300, 20, col)
        text(-30, y - 4, str(v), 12, "#222", True)
        y -= 36
    text(-440, y - 4, "TOTAL", 12, "#222", True)
    text(-340, y - 4, str(sum(p["stats"].values())), 12, "#222", True)

    x2 = 90
    text(x2, 205, "INFO", 14, "#222", True)
    text(x2, 175, "Height: %.1f m    Weight: %.1f kg" % (p["height"], p["weight"]), 12)
    text(x2, 150, "Abilities: " + ", ".join(pretty(a) for a in p["abilities"]), 12)
    text(x2, 125, "Moves learnable: %d" % len(p["moves"]), 12)
    yy = 100
    for gen, types in p["past_types"]:
        text(x2, yy, "Type up to %s: %s" % (pretty(gen), "/".join(types)), 12, "#a00")
        yy -= 22
    if p["games"]:
        games = ", ".join(pretty(g) for g in p["games"])
        text(x2, yy, "Main-series games (Gen 1-5 index):", 11, "#222", True)
        yy -= 20
        line = ""
        for word in games.split(", "):
            if len(line) + len(word) > 52:
                text(x2, yy, line, 10)
                yy -= 16
                line = ""
            line += (", " if line else "") + word
        if line:
            text(x2, yy, line, 10)
            yy -= 16
    yy -= 14
    table = weakness_table(p["types"])
    weak = [pretty(a) + (" x4" if m == 4 else "") for a, m in table.items() if m > 1]
    text(x2, yy, "Weak to: " + ", ".join(weak) if weak else "Weak to: nothing", 11, "#a00", True)

    button(-330, -300, "Add to My Team", lambda: add_to("team", p["name"]), 200)
    button(-110, -300, "Add to My Box", lambda: add_to("box", p["name"]), 200)
    button(110, -300, "Weaknesses", lambda: show_weakness(p["name"]), 200, 38, "#ff9f9f")
    button(330, -300, "Back", menu, 200, 38, "#ddd")
    button(-330, -250, "Add to Favorites", lambda: add_to("favs", p["name"]), 200, 38, "#f8c8d8")
    button(-110, -250, "Compare with...", lambda: compare_with(p["name"]), 200, 38, "#e8c8f8")
    finish()


WHERE_LABEL = {"team": "team", "box": "box", "favs": "favorites"}


def add_to(where, name):
    if user == "guest":
        return notice("Guest mode", ["Create an account to save Pokemon."], menu)
    db = load_db()
    acct = db[user]
    acct.setdefault(where, [])
    label = WHERE_LABEL.get(where, where)
    if name in acct[where]:
        return notice("Already there", [pretty(name) + " is already in your " + label + "."], menu)
    if where == "team" and len(acct["team"]) >= 6:
        return notice("Team full", ["Your team has 6 Pokemon. Remove one first."], team_screen)
    acct[where].append(name)
    save_db(db)
    notice("Saved!", [pretty(name) + " was added to your " + label + "."], menu)


def weakness_prompt():
    q = ask("Weakness checker", "Pokemon name or number:")
    if q:
        show_weakness(q)


def show_weakness(q):
    page("WEAKNESS CHECKER", "Trainer: " + user)
    loading()
    try:
        p = lookup(q)
    except RuntimeError as e:
        return notice("Lookup failed", [str(e)], menu)
    page("%s  -  Weaknesses" % p["name"].upper(), "Trainer: " + user)
    for i, t in enumerate(p["types"]):
        badge(-440 + i * 100, 235, t)
    table = weakness_table(p["types"])
    groups = [("4x damage", 4.0, "#a00"), ("2x damage", 2.0, "#e67e22"),
              ("1/2 damage", 0.5, "#2980b9"), ("1/4 damage", 0.25, "#1a5276"),
              ("No damage (immune)", 0.0, "#555")]
    y = 190
    for label, val, col in groups:
        names = [a for a, m in table.items() if m == val]
        text(-440, y, label + ":", 14, col, True)
        for i, t in enumerate(names):
            badge(-190 + (i % 6) * 100, y - 6 - (i // 6) * 30, t)
        y -= 40 + max(0, (len(names) - 1) // 6) * 30
        if not names:
            text(-190, y + 40, "none", 12, "#777")
    button(-110, -300, "Pokedex Entry", lambda: show_pokemon(p["name"]), 200)
    button(110, -300, "Back", menu, 200, 38, "#ddd")
    finish()


# ---------------------------------------------------------------------------
# NEW: compare two Pokemon
# ---------------------------------------------------------------------------
def compare_prompt():
    a = ask("Compare", "First Pokemon:")
    if not a:
        return
    b = ask("Compare", "Second Pokemon:")
    if b:
        show_compare(a, b)


def compare_with(first):
    b = ask("Compare", "Compare %s with which Pokemon?" % pretty(first))
    if b:
        show_compare(first, b)


def show_compare(qa, qb):
    page("COMPARE", "Trainer: " + user)
    loading()
    try:
        pa, pb = lookup(qa), lookup(qb)
    except RuntimeError as e:
        return notice("Lookup failed", [str(e)], menu)
    page("%s  vs  %s" % (pa["name"].upper(), pb["name"].upper()), "Trainer: " + user)
    keys = [("hp", "HP"), ("attack", "Attack"), ("defense", "Defense"),
            ("special-attack", "Sp. Atk"), ("special-defense", "Sp. Def"), ("speed", "Speed")]
    for i, t in enumerate(pa["types"]):
        badge(-440 + i * 95, 235, t)
    for i, t in enumerate(pb["types"]):
        badge(350 - i * 95, 235, t)
    y = 185
    for k, label in keys:
        va, vb = pa["stats"][k], pb["stats"][k]
        text(0, y, label, 13, "#222", True, "center")
        rect(-40 - va / 255 * 380, y - 8, va / 255 * 380, 18, "#27ae60" if va >= vb else "#e74c3c")
        rect(40, y - 8, vb / 255 * 380, 18, "#27ae60" if vb >= va else "#e74c3c")
        text(-440, y - 4, str(va), 12, "#222", True)
        text(440, y - 4, str(vb), 12, "#222", True, "right")
        y -= 46
    ta, tb = sum(pa["stats"].values()), sum(pb["stats"].values())
    text(0, y - 10, "TOTAL: %d  vs  %d" % (ta, tb), 16, "#222", True, "center")
    wa = max(eff(t, pb["types"]) for t in pa["types"])
    wb = max(eff(t, pa["types"]) for t in pb["types"])
    text(0, y - 45, "%s hits %s for x%g  |  %s hits %s for x%g" % (
        pa["name"].upper(), pb["name"].upper(), wa, pb["name"].upper(), pa["name"].upper(), wb),
        12, "#444", False, "center")
    button(-110, -300, "Compare Others", compare_prompt, 200, 38, "#e8c8f8")
    button(110, -300, "Back", menu, 200, 38, "#ddd")
    finish()


# ---------------------------------------------------------------------------
# NEW: Pokemon of the day (same Pokemon all day, changes at midnight)
# ---------------------------------------------------------------------------
def daily_pokemon():
    today = datetime.date.today().isoformat()
    pid = random.Random(today).randint(1, 386)
    show_pokemon(str(pid))


# ---------------------------------------------------------------------------
# NEW: favorites
# ---------------------------------------------------------------------------
def favorites_screen():
    if user == "guest":
        return notice("Guest mode", ["Create an account to keep favorites."], menu)
    acct = load_db()[user]
    favs = acct.get("favs", [])
    page("%s'S FAVORITES" % user.upper(), "%d saved" % len(favs))
    if not favs:
        text(0, 120, "No favorites yet. Open a Pokemon and press 'Add to Favorites'.",
             14, "#555", False, "center")
    else:
        text(0, 215, "Click a Pokemon to open its Pokedex entry.", 13, "#444", False, "center")
    for k, n in enumerate(favs[:16]):
        r, c = divmod(k, 2)
        button(-230 + c * 460, 170 - r * 50, pretty(n), lambda n=n: show_pokemon(n), 430, 40,
               "#f8c8d8")
    button(-110, -300, "Remove Favorite", remove_favorite, 200, 38, "#ff9f9f")
    button(110, -300, "Back", menu, 200, 38, "#ddd")
    finish()


def remove_favorite():
    q = ask("Remove favorite", "Which Pokemon do you want to remove?")
    if not q:
        return
    try:
        n = norm(q)
    except ValueError as e:
        return notice("Invalid", [str(e)], favorites_screen)
    db = load_db()
    favs = db[user].setdefault("favs", [])
    if n in favs:
        favs.remove(n)
        save_db(db)
        favorites_screen()
    else:
        notice("Not found", [pretty(n) + " is not in your favorites."], favorites_screen)


# ---------------------------------------------------------------------------
# NEW: cache manager
# ---------------------------------------------------------------------------
def cache_screen():
    try:
        files = os.listdir(CACHE)
    except OSError:
        files = []
    size = 0
    for f in files:
        try:
            size += os.path.getsize(os.path.join(CACHE, f))
        except OSError:
            pass
    pokes = sum(1 for f in files if f.startswith("pk2_"))
    moves = sum(1 for f in files if f.startswith("mv2_"))
    sprites = sum(1 for f in files if f.startswith("sp_"))
    routes = sum(1 for f in files if f.startswith("rt_"))
    notice("Cache", ["Saved files: %d" % len(files),
                     "  Pokemon: %d   Moves: %d   Sprites: %d   Routes: %d" % (
                         pokes, moves, sprites, routes),
                     "Size: %.0f KB" % (size / 1024),
                     "Cached Pokemon, moves and sprites work offline.",
                     "Folder: " + CACHE], menu)
    button(0, -230, "Clear Cache", clear_cache, 200, 38, "#ff9f9f")
    finish()


def clear_cache():
    for f in os.listdir(CACHE):
        try:
            os.remove(os.path.join(CACHE, f))
        except OSError:
            pass
    MOVE_MEM.clear()
    POKE_MEM.clear()
    IMGS.clear()
    notice("Done", ["Cache cleared."], menu)


# ---------------------------------------------------------------------------
# NEW: route maps (wild Pokemon per route + real map in the browser)
# ---------------------------------------------------------------------------
def route_home():
    page("ROUTE MAPS", "Trainer: " + user)
    text(0, 225, "Check the Pokemon on any route, and open its map.", 15, "#222", True, "center")
    button(0, 175, "Search a route (e.g. 104 or kanto route 1)", route_prompt, 520, 46, "#f8d860")
    text(0, 125, "Or browse by region:", 14, "#444", False, "center")
    for k, r in enumerate(REGIONS):
        row, c = divmod(k, 2)
        button(-150 + c * 300, 80 - row * 58, pretty(r), lambda r=r: route_list(r), 270, 46, "#a8e8e0")
    button(0, -300, "Back", menu, 200, 38, "#ddd")
    finish()


def route_prompt():
    q = ask("Route maps", "Route name or number (e.g. 104, hoenn route 104, kanto route 1):")
    if not q:
        return
    s = q.lower()
    region = next((r for r in REGIONS if r in s), None)
    m = re.search(r"\d+", s)
    if not m:
        if region:
            return route_list(region)
        return notice("Invalid", ["Enter a route number like 104, or a region like Hoenn."], route_home)
    n = int(m.group())
    if region:
        return show_route_by(region, n)
    if 101 <= n <= 134:
        return show_route_by("hoenn", n)
    if 201 <= n <= 230:
        return show_route_by("sinnoh", n)
    route_pick_region(n)


def route_pick_region(n):
    page("ROUTE %d" % n, "Trainer: " + user)
    text(0, 225, "Route %d exists in several regions. Which one?" % n, 16, "#222", True, "center")
    for k, r in enumerate(REGIONS):
        row, c = divmod(k, 2)
        button(-150 + c * 300, 160 - row * 58, pretty(r), lambda r=r: show_route_by(r, n),
               270, 46, "#a8e8e0")
    button(0, -300, "Back", route_home, 200, 38, "#ddd")
    finish()


def route_error(e, back):
    if isinstance(e, NotFound):
        return notice("Not found", ["That route was not found. Try another number or region."], back)
    reason = str(getattr(e, "reason", e))[:90]
    return notice("Lookup failed", ["Could not load route data (not cached yet):", reason], back)


def show_route_by(region, n):
    page("ROUTE MAPS", "Trainer: " + user)
    loading("Finding %s route %d..." % (pretty(region), n))
    try:
        loc = find_route(region, n)
    except (NotFound, urllib.error.URLError, OSError) as e:
        return route_error(e, route_home)
    show_route(loc)


def route_list(region, pg=0):
    page("ROUTE MAPS", "Trainer: " + user)
    loading("Loading %s routes..." % pretty(region))
    try:
        names = region_routes(region)
    except (NotFound, urllib.error.URLError, OSError) as e:
        return route_error(e, route_home)
    if not names:
        return notice("No routes", ["No numbered routes found for " + pretty(region) + "."], route_home)
    per = 16
    page("%s ROUTES" % region.upper(), "%d-%d of %d" % (
        pg * per + 1, min((pg + 1) * per, len(names)), len(names)))
    for k, n in enumerate(names[pg * per:(pg + 1) * per]):
        row, c = divmod(k, 2)
        button(-230 + c * 460, 215 - row * 50, pretty(n), lambda n=n: show_route(n), 430, 40, "#a8e8e0")
    if pg > 0:
        button(-330, -300, "< Prev", lambda: route_list(region, pg - 1), 150)
    if (pg + 1) * per < len(names):
        button(330, -300, "Next >", lambda: route_list(region, pg + 1), 150)
    button(0, -300, "Back", route_home, 200, 38, "#ddd")
    finish()


def area_label(loc, name):
    s = name.replace(loc + "-", "").replace("-area", "")
    if s == name or not s:
        return "Main area"
    return pretty(s)[:24]


def open_route_map(loc, region):
    q = pretty(loc)
    if region and region.lower() not in q.lower():
        q = pretty(region) + " " + q
    url = "https://bulbapedia.bulbagarden.net/w/index.php?search=" + urllib.parse.quote_plus(q)
    try:
        webbrowser.open(url)
        msg = ["Opening the %s page on Bulbapedia in your browser." % q,
               "It has the full map, trainers and items."]
    except Exception:
        msg = ["Could not open a browser. Search this on Bulbapedia:", q]
    notice("Route map", msg, lambda: show_route(loc))


def show_route(loc, ai=0, pg=0):
    page("ROUTE MAPS", "Trainer: " + user)
    loading("Loading route data...")
    try:
        data = get_route(loc)
    except (NotFound, urllib.error.URLError, OSError) as e:
        return route_error(e, route_home)
    areas = data["areas"]
    region = data.get("region", "")
    page(pretty(loc).upper(), "Trainer: " + user)
    per = 12
    rows = []
    if not areas:
        text(-440, 200, "No wild Pokemon encounter data for this location.", 14, "#555")
    else:
        ai = max(0, min(ai, len(areas) - 1))
        rows = areas[ai]["rows"]
        text(-440, 235, "Region: %s    Area: %s    (%d Pokemon)" % (
            pretty(region) if region else "-", area_label(loc, areas[ai]["name"]), len(rows)),
            13, "#222", True)
        methods = sorted({m for r in rows for m in r["methods"]})
        text(-440, 210, ("Encounter types: " + ", ".join(pretty(m) for m in methods))[:115],
             12, "#444")
        if not rows:
            text(-440, 170, "No wild Pokemon listed for this area.", 14, "#555")
        else:
            for x, h in ((-440, "POKEMON"), (-270, "HOW"), (-20, "LEVEL"), (80, "CHANCE"),
                         (170, "GAMES")):
                text(x, 182, h, 11, "#a00", True)
            y = 155
            for r in rows[pg * per:(pg + 1) * per]:
                text(-440, y, pretty(r["name"]), 13, "#222", True)
                text(-270, y, ", ".join(pretty(m) for m in r["methods"])[:26], 12)
                lv = "%d-%d" % (r["min"], r["max"]) if r["min"] <= r["max"] else "-"
                text(-20, y, "Lv " + lv, 12)
                text(80, y, "up to %d%%" % r["chance"] if r["chance"] else "-", 12)
                games = ", ".join(pretty(g) for g in r["games"])
                text(170, y, games if len(games) <= 54 else games[:51] + "...", 10, "#555")
                y -= 27
    if len(areas) > 1:
        for k, a in enumerate(areas[:4]):
            button(-330 + k * 220, -245, area_label(loc, a["name"]),
                   lambda k=k: show_route(loc, k, 0), 200, 34,
                   "#a8e8e0" if k == ai else "#ddd")
    if pg > 0:
        button(-400, -300, "< Prev", lambda: show_route(loc, ai, pg - 1), 120)
    if (pg + 1) * per < len(rows):
        button(-270, -300, "Next >", lambda: show_route(loc, ai, pg + 1), 120)
    button(-60, -300, "Open Map in Browser", lambda: open_route_map(loc, region), 230, 38, "#f8d860")
    button(150, -300, "Route List", lambda: route_home(), 130, 38, "#a8e8e0")
    button(310, -300, "Back", menu, 130, 38, "#ddd")
    finish()


# ---------------------------------------------------------------------------
# NEW: music (opens official-music searches, plus a folder for your own files)
# ---------------------------------------------------------------------------
MUSIC_GAMES = ["Red Blue", "Gold Silver", "Ruby Sapphire", "Diamond Pearl",
               "Black White", "X Y", "Sun Moon", "Sword Shield"]
MUSIC_THEMES = ["Title Screen", "Route", "Wild Pokemon Battle", "Trainer Battle",
                "Gym Leader Battle", "Champion Battle", "Pokemon Center", "Victory"]
AUDIO_EXT = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")


def open_external(target):
    if sys.platform.startswith("win"):
        os.startfile(target)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def music_home():
    page("MUSIC", "Trainer: " + user)
    text(0, 225, "Listen to the original Pokemon music", 18, "#222", True, "center")
    button(0, 185, "Search any theme (e.g. Lavender Town)", music_prompt, 520, 46, "#f8d860")
    text(0, 140, "Or pick a game:", 14, "#444", False, "center")
    for k, g in enumerate(MUSIC_GAMES):
        row, c = divmod(k, 2)
        button(-150 + c * 300, 95 - row * 58, g.replace(" ", " / "),
               lambda g=g: music_game(g), 270, 46, "#f8d0f0")
    button(0, -150, "My Music Folder (play your own files)", lambda: music_folder(0), 520, 46,
           "#a8e0a0")
    text(0, -215, "Searches open YouTube in your browser. The music itself is not stored in this app.",
         11, "#666", False, "center")
    button(0, -300, "Back", menu, 200, 38, "#ddd")
    finish()


def open_music_search(query, back):
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    try:
        webbrowser.open(url)
        msg = ["Opening a search in your browser:", query]
    except Exception:
        msg = ["Could not open a browser. Search this on YouTube:", query]
    notice("Music", msg, back)


def music_prompt():
    q = ask("Music", "Which theme? (e.g. Lavender Town, Route 104, Hoenn Champion battle):")
    if q:
        open_music_search("Pokemon " + q + " music", music_home)


def music_game(game):
    page(game.replace(" ", " / ").upper(), "Trainer: " + user)
    text(0, 225, "Pick a theme to search for", 16, "#222", True, "center")
    for k, t in enumerate(MUSIC_THEMES):
        row, c = divmod(k, 2)
        button(-230 + c * 460, 170 - row * 62, t,
               lambda t=t: open_music_search("Pokemon %s %s music" % (game, t),
                                             lambda: music_game(game)),
               430, 48, "#f8d0f0")
    button(0, -300, "Back", music_home, 200, 38, "#ddd")
    finish()


def music_folder(pg=0):
    try:
        files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(AUDIO_EXT))
    except OSError:
        files = []
    per = 12
    page("MY MUSIC", "%d file(s)" % len(files))
    if not files:
        text(0, 150, "No music files yet.", 16, "#222", True, "center")
        text(0, 115, "Put your own audio files (mp3, wav, ogg, m4a, flac) in this folder:", 13,
             "#444", False, "center")
        text(0, 90, MUSIC_DIR, 11, "#444", False, "center")
    for k, f in enumerate(files[pg * per:(pg + 1) * per]):
        row, c = divmod(k, 2)
        button(-230 + c * 460, 215 - row * 50, f[:36],
               lambda f=f: play_file(f, pg), 430, 40, "#a8e0a0")
    if pg > 0:
        button(-330, -300, "< Prev", lambda: music_folder(pg - 1), 150)
    if (pg + 1) * per < len(files):
        button(330, -300, "Next >", lambda: music_folder(pg + 1), 150)
    button(-110, -245, "Open Folder", open_music_folder, 200, 38, "#f8d860")
    button(110, -245, "Refresh", lambda: music_folder(pg), 200, 38, "#ddd")
    button(0, -300, "Back", music_home, 200, 38, "#ddd")
    finish()


def open_music_folder():
    try:
        open_external(MUSIC_DIR)
    except Exception as e:
        notice("Could not open folder", [str(e)[:100], MUSIC_DIR], music_folder)


def play_file(name, pg):
    path = os.path.join(MUSIC_DIR, name)
    try:
        open_external(path)
        notice("Now playing", [name, "Opened in your default music player."],
               lambda: music_folder(pg))
    except Exception as e:
        notice("Could not play", [str(e)[:100]], lambda: music_folder(pg))


def team_screen():
    if user == "guest":
        return notice("Guest mode", ["Create an account to keep a team and box."], menu)
    acct = load_db()[user]
    sub = "Team %d/6  |  Box %d" % (len(acct["team"]), len(acct["box"]))
    page("%s'S TEAM" % user.upper(), sub)
    loading()
    page("%s'S TEAM" % user.upper(), sub)
    y = 235
    if not acct["team"]:
        text(-440, y, "Your team is empty. Look up a Pokemon and add it, or use Random Team!",
             14, "#555")
    for n in acct["team"]:
        try:
            p = get_pokemon(n)
            text(-440, y, "%s" % pretty(p["name"]), 15, "#222", True)
            for i, t in enumerate(p["types"]):
                badge(-250 + i * 100, y - 4, t)
            text(-30, y, "BST %d   Spd %d" % (sum(p["stats"].values()), p["stats"]["speed"]), 12)
        except Exception:
            text(-440, y, pretty(n) + " (offline, no cached data)", 13, "#a00")
        y -= 38
    text(-440, -100, "BOX: " + (", ".join(pretty(n) for n in acct["box"]) or "empty"), 11, "#333")
    button(-330, -190, "Add to Team (by name)", team_add_by_name, 230)
    button(-90, -190, "Remove from Team", lambda: remove_from("team"), 200, 38, "#ff9f9f")
    button(130, -190, "Remove from Box", lambda: remove_from("box"), 200, 38, "#ff9f9f")
    button(-330, -245, "Random Team (fill to 6)", random_fill, 230, 38, "#a8e0a0")
    button(0, -300, "Back", menu, 200, 38, "#ddd")
    finish()


def team_add_by_name():
    q = ask("Add to team", "Pokemon name or number:")
    if not q:
        return
    try:
        p = lookup(q)
    except RuntimeError as e:
        return notice("Lookup failed", [str(e)], team_screen)
    add_to("team", p["name"])


def random_team_names(n=6, have=()):
    """Fetch random Pokemon one at a time (cached ones are instant)."""
    names = []
    for i in random.sample(range(1, 387), n + len(have) + 2):
        try:
            nm = get_pokemon(str(i))["name"]
        except Exception:
            continue
        if nm not in have and nm not in names:
            names.append(nm)
        if len(names) == n:
            break
    return names


def random_fill():
    db = load_db()
    team = db[user]["team"]
    need = 6 - len(team)
    if need <= 0:
        return notice("Team full", ["Your team already has 6 Pokemon."], team_screen)
    page("RANDOM TEAM", "Trainer: " + user)
    loading("Scouting for Pokemon...")
    try:
        team.extend(random_team_names(need, team))
    except Exception:
        return notice("Offline", ["Random team needs internet to fetch new Pokemon."], team_screen)
    save_db(db)
    team_screen()


def remove_from(where):
    q = ask("Remove", "Which Pokemon do you want to remove from your %s?" % where)
    if not q:
        return
    db = load_db()
    n = norm(q)
    if n in db[user][where]:
        db[user][where].remove(n)
        save_db(db)
        team_screen()
    else:
        notice("Not found", [pretty(n) + " is not in your " + where + "."], team_screen)


def browse_screen(start=0, order="num"):
    page("BROWSE ALL POKEMON", "Trainer: " + user)
    loading()
    try:
        names = all_pokemon_names()
    except Exception:
        return notice("Offline", ["Browsing the full list needs internet (first time only)."], menu)
    if order == "az":
        names = sorted(names)
    per = 28
    page("BROWSE ALL POKEMON", "%d-%d of %d  (%s)" % (
        start + 1, min(start + per, len(names)), len(names),
        "A-Z" if order == "az" else "by number"))
    for i, n in enumerate(names[start:start + per]):
        col, row = divmod(i, 14)
        label = "%s" % pretty(n) if order == "az" else "%d. %s" % (start + i + 1, pretty(n))
        text(-440 + col * 450, 235 - row * 30, label, 13)
    if start > 0:
        button(-330, -300, "< Prev", lambda: browse_screen(max(0, start - per), order), 150)
    if start + per < len(names):
        button(-110, -300, "Next >", lambda: browse_screen(start + per, order), 150)
    button(110, -300, "Search...", dex_prompt, 150)
    button(330, -300, "Back", menu, 150, 38, "#ddd")
    other = "num" if order == "az" else "az"
    button(0, -245, "Sort: %s" % ("by number" if order == "az" else "A-Z"),
           lambda: browse_screen(0, other), 200, 38, "#e8c8f8")
    finish()


def battle_prompt():
    if user == "guest":
        return notice("Guest mode", ["Create an account so the advisor can use your team."], menu)
    q = ask("Battle advisor", "Which Pokemon are you fighting? (name or number)")
    if q:
        battle(q)


def battle(q):
    acct = load_db()[user]
    pool_names = acct["team"] or acct["box"]
    if not pool_names:
        return notice("No Pokemon", ["Add Pokemon to your team or box first",
                                     "(Pokedex Lookup > Add to My Team)."], menu)
    page("BATTLE ADVISOR", "Trainer: " + user)
    loading("Analysing the matchup...")
    try:
        opp = lookup(q)
        team = [lookup(n) for n in pool_names]
    except RuntimeError as e:
        return notice("Lookup failed", [str(e)], menu)
    ranked = rank_team(team, opp)
    page("VS %s" % opp["name"].upper(), "Trainer: " + user)
    for i, t in enumerate(opp["types"]):
        badge(-440 + i * 100, 235, t)
    table = weakness_table(opp["types"])
    weak = [pretty(a) for a, m in table.items() if m > 1]
    text(-230, 240, "Weak to: " + (", ".join(weak) or "nothing"), 12, "#a00", True)
    text(-230, 220, "Opp. base stats: Spd %d  Atk %d  SpA %d" % (
        opp["stats"]["speed"], opp["stats"]["attack"], opp["stats"]["special-attack"]), 11)

    score, best, off, dfn = ranked[0]
    text(-440, 180, "BEST PICK: " + best["name"].upper(), 20, "#1e8449", True)
    text(-440, 154, "You hit it for x%g (best STAB type); it hits you for up to x%g." % (off, dfn),
         12, "#222")
    text(-440, 120, "RECOMMENDED MOVES", 14, "#222", True)
    y = 92
    moves = best_moves(best, opp)
    if not moves:
        text(-440, y, "No damaging moves found (offline?). Try again online.", 12, "#a00")
    for _, m, mult in moves:
        tag = "SUPER EFFECTIVE" if mult > 1 else "resisted" if 0 < mult < 1 else \
              "NO EFFECT" if mult == 0 else "neutral"
        badge(-440, y - 6, m["type"])
        text(-335, y, "%s  (%s, power %s, acc %s)  x%g  %s" % (
            pretty(m["name"]), m["class"], m["power"], m["accuracy"] or "-", mult, tag), 12,
            "#1e8449" if mult > 1 else "#222", mult > 1)
        y -= 32

    text(-440, y - 10, "TEAM RANKING", 14, "#222", True)
    y -= 36
    for rank, (sc, p, o, d) in enumerate(ranked[:6], 1):
        text(-440, y, "%d. %s  -  attack x%g / takes x%g  (score %.1f)" % (
            rank, pretty(p["name"]), o, d, sc), 12, "#222", rank == 1)
        y -= 22
    button(-110, -310, "Another Opponent", battle_prompt, 200, 38, "#a8e0a0")
    button(110, -310, "Back", menu, 200, 38, "#ddd")
    finish()


BS = {"p1": None, "p2": None}


def open_battle_setup():
    if user == "guest":
        return notice("Guest mode", ["Create an account to battle."], menu)
    BS["p1"], BS["p2"] = ("acct", user), None
    battle_setup()


def ent_name(ent):
    return ent[1]


def battle_setup():
    db = load_db()
    page("TRAINER BATTLE", "Trainer: " + user)
    for slot, x in ((1, -245), (2, 245)):
        ent = BS["p%d" % slot]
        rect(x - 215, 20, 430, 230, "#e8f0f8", "#304060", width=5)
        text(x, 215, "TRAINER %d" % slot, 20, "#304060", True, "center", font=GBA_FONT)
        if not ent:
            text(x, 130, "- not selected -", 16, "#808080", False, "center", font=GBA_FONT)
            continue
        text(x, 175, ent[1].upper(), 24, "#202020", True, "center", font=GBA_FONT)
        if ent[0] == "cpu":
            text(x, 130, "Random team of 6", 14, "#404040", False, "center", font=GBA_FONT)
            text(x, 95, "CPU controlled", 14, "#404040", False, "center", font=GBA_FONT)
        else:
            a = db.get(ent[1], {})
            text(x, 130, "%s  |  %d Pokemon" % (rank_title(a.get("wins", 0)), len(a.get("team", []))),
                 14, "#404040", False, "center", font=GBA_FONT)
            text(x, 95, "W %d   L %d" % (a.get("wins", 0), a.get("losses", 0)), 14, "#404040",
                 False, "center", font=GBA_FONT)
            names = ", ".join(pretty(n) for n in a.get("team", []))
            for j, ln in enumerate(textwrap.wrap(names, 40)[:2]):
                text(x, 62 - j * 18, ln, 11, "#606060", False, "center", font=GBA_FONT)
    text(0, 120, "VS", 34, "#c02020", True, "center", font=GBA_FONT)
    button(-245, -40, "Select Trainer 1", lambda: pick_trainer(1), 260, 46)
    button(245, -40, "Select Trainer 2", lambda: pick_trainer(2), 260, 46)
    button(245, -100, "Rival (CPU)", pick_rival, 260, 46, "#a8c8f8")
    button(0, -180, "START BATTLE!", start_battle, 320, 60, "#f8a0a0")
    button(0, -300, "Back", menu, 200, 38, "#ddd")
    finish()


def pick_rival():
    BS["p2"] = ("cpu", "RIVAL")
    battle_setup()


def pick_trainer(slot, pg=0):
    db = load_db()
    other = BS["p2" if slot == 1 else "p1"]
    names = sorted(k for k in db if not (other and other[0] == "acct" and other[1] == k))
    per = 8
    page("SELECT TRAINER %d" % slot, "Trainer: " + user)
    if not names:
        text(0, 100, "No other trainer accounts yet. Create one from the login screen.",
             14, "#444", False, "center")
    for k, n in enumerate(names[pg * per:(pg + 1) * per]):
        r, c = divmod(k, 2)
        a = db[n]
        label = "%s  (%d Pokemon)" % (n, len(a.get("team", [])))
        button(-230 + c * 460, 190 - r * 62, label[:30], lambda n=n: chosen_trainer(slot, n),
               430, 48, "#f8d860" if a.get("team") else "#cccccc")
    if pg > 0:
        button(-330, -300, "< Prev", lambda: pick_trainer(slot, pg - 1), 150)
    if (pg + 1) * per < len(names):
        button(330, -300, "Next >", lambda: pick_trainer(slot, pg + 1), 150)
    button(0, -300, "Back", battle_setup, 200, 38, "#ddd")
    text(0, -240, "Other trainers must enter their password.", 12, "#555", False, "center")
    finish()


def chosen_trainer(slot, name):
    db = load_db()
    acct = db[name]
    if name != user:
        pw = ask("Trainer login", "Password for %s:" % name) or ""
        if hash_pw(pw, acct["salt"]) != acct["hash"]:
            return notice("Wrong password", ["That password is not correct."],
                          lambda: pick_trainer(slot))
    if not acct.get("team"):
        return notice("No team", [name + " has no Pokemon on their team yet.",
                                  "Add some in My Team & Box first."], lambda: pick_trainer(slot))
    BS["p%d" % slot] = ("acct", name)
    battle_setup()


def build_side(ent):
    if ent[0] == "cpu":
        progress("RIVAL is picking a team...")
        names, cpu = random_team_names(6), True
    else:
        names, cpu = load_db()[ent[1]]["team"][:6], False
    if not names:
        raise RuntimeError("%s has no Pokemon on their team." % ent[1])
    mons = []
    for k, n in enumerate(names, 1):
        progress("%s: preparing %s (%d/%d)" % (ent[1].upper(), pretty(n), k, len(names)))
        mons.append(make_mon(lookup(n)))
    return {"name": ent[1], "mons": mons, "active": 0, "potions": 3, "cpu": cpu}


def preload_sprites(sides):
    """Download sprites one at a time; cached ones are skipped instantly."""
    for i, s in enumerate(sides):
        for m in s["mons"]:
            get_sprite_bytes(m["id"], i == 0)


def start_battle():
    p1, p2 = BS["p1"], BS["p2"]
    if not p1 or not p2:
        return notice("Pick both trainers", ["Select Trainer 1 and Trainer 2 (or the Rival)."],
                      battle_setup)
    page("TRAINER BATTLE", "Trainer: " + user)
    try:
        s0, s1 = build_side(p1), build_side(p2)
        progress("Loading sprites...")
        preload_sprites([s0, s1])
    except RuntimeError as e:
        return notice("Cannot start", [str(e)], battle_setup)
    except Exception as e:
        return notice("Could not load data", ["Battles need internet the first time to fetch Pokemon and",
                                              "moves. After that they are cached.",
                                              str(e)[:100]], battle_setup)
    winner = run_battle(s0, s1)
    record_result(p1, p2, winner)
    result_screen(p1, p2, winner)


def record_result(p1, p2, winner):
    db = load_db()
    for ent, won in ((p1, winner == 0), (p2, winner == 1)):
        if ent[0] == "acct" and ent[1] in db:
            key = "wins" if won else "losses"
            db[ent[1]][key] = db[ent[1]].get(key, 0) + 1
    save_db(db)


def result_screen(p1, p2, winner):
    clear_battle()
    page("BATTLE RESULT", "Trainer: " + user)
    w, l = (p1, p2) if winner == 0 else (p2, p1)
    text(0, 170, "%s WINS!" % w[1].upper(), 44, "#c02020", True, "center", font=GBA_FONT)
    text(0, 110, "%s defeated %s." % (w[1], l[1]), 18, "#303030", False, "center", font=GBA_FONT)
    if w[0] == "acct" or l[0] == "acct":
        text(0, 60, "Records saved to the trainer accounts.", 14, "#505050", False, "center",
            font=GBA_FONT)
    button(-130, -60, "Rematch", battle_setup, 220, 50, "#f8a0a0")
    button(130, -60, "Main Menu", menu, 220, 50)
    finish()


def main():
    try:
        setup()
        title_screen()
        turtle.mainloop()
    except turtle.Terminator:
        pass


if __name__ == "__main__":
    main()