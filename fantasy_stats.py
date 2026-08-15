"""
LoL Fantasy -> Spielerstatistiken einsammeln
--------------------------------------------
Holt für abgeschlossene Profi-Matches die Einzelwerte jedes Spielers (Kills,
Tode, Assists, Creep Score) aus dem Livestats-Feed von lolesports, rechnet
daraus Fantasy-Punkte und schreibt alles nach docs/fantasy-data.json. Die
Seite (docs/index.html) liest nur noch diese Datei - sie kann die Werte nicht
selbst holen, weil dafür pro Spiel eine eigene Abfrage nötig wäre.

Läuft in derselben GitHub Action wie der Kalender-Sync (alle 3 Stunden) und
arbeitet inkrementell: bereits verarbeitete Spiele werden übersprungen, pro
Lauf kommen höchstens MAX_NEW_GAMES neue dazu. Beim allerersten Lauf braucht
die laufende Saison deshalb ein paar Durchgänge, bis sie komplett ist.

Zwei Dinge, die die API *nicht* hergibt, und wie damit umgegangen wird:

1. Wer ein einzelnes Spiel gewonnen hat, steht nirgends - nur wer die Serie
   gewonnen hat. Der Sieg-Bonus hängt deshalb am Match, nicht am Spiel.
2. Riot markiert Matches gelegentlich als "completed", bevor der Endstand
   durchsynchronisiert ist. Solche Matches werden übersprungen, bis der Stand
   plausibel ist (Mehrheit der Spiele gewonnen) - sonst bekäme das falsche
   Team den Bonus und das Spiel wäre als verarbeitet abgehakt.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

# Derselbe öffentliche Key, den lolesports.com im Browser nutzt.
API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
BASE = "https://esports-api.lolesports.com/persisted/gw"
LIVESTATS = "https://feed.lolesports.com/livestats/v1/window"
HEADERS = {"x-api-key": API_KEY}

FANTASY_LEAGUES = ["LEC", "LCS", "LCK", "LPL"]
OUTPUT_PATH = "docs/fantasy-data.json"

# Rollen in der Reihenfolge, in der sie im Kader stehen.
ROLES = ["top", "jungle", "mid", "bottom", "support"]

# Punkte pro Spiel. Ändert man hier etwas, muss SCORING_VERSION hoch - dann
# rechnet der nächste Lauf die Saison von vorn durch, statt alte und neue
# Punkte zu mischen.
SCORING = {
    "kill": 2.0,
    "assist": 1.5,
    "death": -0.5,
    "cs": 0.02,
    "bigGame": 2.0,      # 10+ Kills oder 10+ Assists in einem Spiel
    "bigGameAt": 10,
    "deathless": 2.0,    # kein Tod im ganzen Spiel
    "seriesWin": 3.0,    # einmal pro gewonnener Serie, nicht pro Spiel
}
SCORING_VERSION = 1

# Budget für den Kader (5 Spieler). Preise liegen zwischen 4.0 und 12.0,
# ein Kader kostet also mindestens 20.0 - 35.0 lässt genau einen Topspieler
# zu, wenn der Rest günstig ist.
BUDGET = 35.0
PRICE_MIN = 4.0
PRICE_MAX = 12.0
PRICE_DEFAULT = 5.0
# Preis richtet sich nach dem Schnitt der letzten Runden, in denen der Spieler
# gespielt hat - so ziehen Formkurven und Kaderwechsel mit, ohne dass ein
# starker Split von vor einem Jahr ewig nachwirkt.
PRICE_WINDOW_ROUNDS = 10
PRICE_MIN_GAMES = 3

# Die Runde läuft Montag 12:00 bis Montag 12:00 - wie im Tippspiel. Feste
# Zeitzone, damit die Action (UTC) dieselben Grenzen zieht wie der Browser
# in Deutschland.
TZ = ZoneInfo("Europe/Berlin")
ROUND_WEEKDAY = 0   # Montag
ROUND_HOUR = 12

# Einzelne Spielzeilen (für die Aufschlüsselung im Frontend) nur für die
# letzten Wochen behalten; die Rundensummen bleiben dauerhaft.
LINE_RETENTION_DAYS = 35
ROUND_RETENTION_DAYS = 730
PROCESSED_RETENTION = 6000

MAX_NEW_GAMES = int(os.environ.get("FANTASY_MAX_GAMES", "250"))
REBUILD = os.environ.get("FANTASY_REBUILD", "").lower() in ("1", "true", "yes")

SESSION = requests.Session()


# ---------------------------------------------------------------- HTTP

def _get(url, params=None, headers=None, tries=3):
    """GET mit ein paar Wiederholungen. Gibt None zurück, statt zu werfen -
    ein einzelnes fehlendes Spiel darf den ganzen Lauf nicht kippen."""
    for attempt in range(tries):
        try:
            r = SESSION.get(url, params=params, headers=headers, timeout=25)
        except requests.RequestException as e:
            if attempt == tries - 1:
                print(f"  ! {url}: {e}")
                return None
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                print(f"  ! {url}: keine gültige JSON-Antwort")
                return None
        # 204 = Spiel existiert, hat aber (noch) keine Livestats.
        if r.status_code in (204, 404):
            return None
        if r.status_code in (429, 500, 502, 503, 504) and attempt < tries - 1:
            time.sleep(2.0 * (attempt + 1))
            continue
        print(f"  ! {url}: HTTP {r.status_code}")
        return None
    return None


def api(path, **params):
    params.setdefault("hl", "en-GB")
    time.sleep(0.15)
    return _get(f"{BASE}/{path}", params=params, headers=HEADERS)


def livestats_window(game_id):
    """Letztes Fenster eines Spiels. Ohne startingTime liefert der Feed das
    aktuellste - bei einem beendeten Spiel also den Schlussstand."""
    time.sleep(0.25)
    return _get(f"{LIVESTATS}/{game_id}")


# ---------------------------------------------------------------- Zeit/Runden

def parse_iso(s):
    """Robust gegen beide Formen, die die API liefert: volle Zeitstempel
    ("2026-02-09T17:00:00Z") und reine Daten ("2026-02-09", bei Turnieren)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def round_bounds(dt):
    """(Schlüssel, Start, Ende) der Runde, in der dt liegt."""
    local = dt.astimezone(TZ)
    start = local.replace(hour=ROUND_HOUR, minute=0, second=0, microsecond=0)
    start -= timedelta(days=(local.weekday() - ROUND_WEEKDAY) % 7)
    if start > local:
        start -= timedelta(days=7)
    end = start + timedelta(days=7)
    year, week, _ = start.isocalendar()
    return f"{year}-W{week:02d}", start, end


# ---------------------------------------------------------------- Kader

def load_roster():
    """Alle aktiven Teams der Fantasy-Ligen mit ihren Spielern.

    Gibt (spieler_nach_id, team_nach_id) zurück. Die Team-Zuordnung braucht
    der Sammler später, um Livestats-Teilnehmer einer Liga zuzuordnen.
    """
    data = api("getTeams")
    players, teams = {}, {}
    if not data:
        print("  ! getTeams nicht erreichbar - Kader bleibt wie er ist")
        return players, teams

    for team in data.get("data", {}).get("teams", []) or []:
        league = (team.get("homeLeague") or {}).get("name")
        if league not in FANTASY_LEAGUES:
            continue
        if team.get("status") not in (None, "active"):
            continue
        tid = team.get("id")
        if not tid:
            continue
        teams[tid] = {
            "name": team.get("name") or team.get("code") or "?",
            "code": team.get("code") or "",
            "league": league,
            "image": team.get("image") or "",
        }
        for p in team.get("players") or []:
            pid = p.get("id")
            role = (p.get("role") or "").lower()
            if not pid or role not in ROLES:
                continue
            full = " ".join(x for x in [p.get("firstName"), p.get("lastName")] if x)
            players[pid] = {
                "name": p.get("summonerName") or full or "?",
                "full": full,
                "role": role,
                "teamId": tid,
                "team": teams[tid]["name"],
                "code": teams[tid]["code"],
                "league": league,
                "image": p.get("image") or "",
                "teamImage": teams[tid]["image"],
            }
    return players, teams


# ---------------------------------------------------------------- Matches

def season_start():
    """Ab wann Matches überhaupt interessieren: Anfang des laufenden Jahres."""
    now = datetime.now(TZ)
    return datetime(now.year, 1, 1, tzinfo=TZ)


def collect_matches():
    """Abgeschlossene Matches der Fantasy-Ligen aus dieser Saison, neueste
    zuerst. Quellen: der laufende Spielplan plus die abgeschlossenen Events
    jedes Turniers, damit auch ältere Wochen nachgeholt werden."""
    leagues = api("getLeagues")
    if not leagues:
        print("  ! getLeagues nicht erreichbar - keine Matches")
        return []

    wanted = {l["id"]: l["name"] for l in leagues.get("data", {}).get("leagues", []) or []
              if l.get("name") in FANTASY_LEAGUES}
    if not wanted:
        print("  ! Keine der Fantasy-Ligen gefunden - Namen prüfen")
        return []

    cutoff = season_start()
    by_id = {}

    def take(events, league_hint=None):
        for ev in events or []:
            match = ev.get("match")
            if not match or not match.get("id"):
                continue
            if ev.get("state") != "completed":
                continue
            start = parse_iso(ev.get("startTime"))
            if not start or start < cutoff:
                continue
            league = (ev.get("league") or {}).get("name") or league_hint
            if league not in FANTASY_LEAGUES:
                continue
            by_id[match["id"]] = {
                "id": match["id"],
                "start": start,
                "league": league,
                "block": ev.get("blockName") or "",
                "teams": match.get("teams") or [],
                "games": match.get("games") or [],
                "strategy": match.get("strategy") or {},
            }

    for lid, lname in wanted.items():
        sched = api("getSchedule", leagueId=lid)
        if sched:
            take((sched.get("data", {}).get("schedule") or {}).get("events"), lname)

        tours = api("getTournamentsForLeague", leagueId=lid)
        tour_list = []
        for entry in (tours or {}).get("data", {}).get("leagues", []) or []:
            tour_list += entry.get("tournaments") or []
        for t in tour_list:
            t_start = parse_iso(t.get("startDate")) or parse_iso(t.get("endDate"))
            if not t_start or t_start < cutoff - timedelta(days=30):
                continue
            done = api("getCompletedEvents", tournamentId=t.get("id"))
            if done:
                take((done.get("data", {}).get("schedule") or {}).get("events"), lname)

    return sorted(by_id.values(), key=lambda m: m["start"], reverse=True)


def series_winner(match):
    """Team-ID des Serien-Siegers, oder None wenn der Endstand nicht
    plausibel ist (Riot meldet manchmal zu früh "completed")."""
    teams = match["teams"]
    if len(teams) < 2:
        return None
    wins = []
    for t in teams:
        result = t.get("result") or {}
        w = result.get("gameWins")
        if not isinstance(w, int):
            return None
        wins.append((t.get("id"), w, result.get("outcome")))
    count = match["strategy"].get("count") or 3
    majority = count // 2 + 1
    best = max(wins, key=lambda x: x[1])
    if best[1] < majority:
        return None
    return best[0]


def match_games(match):
    """Spiel-IDs eines Matches. Stehen sie schon im Spielplan, sparen wir uns
    die Extra-Abfrage."""
    games = [g for g in match["games"] if g.get("id")]
    if not games:
        detail = api("getEventDetails", id=match["id"])
        event = (detail or {}).get("data", {}).get("event") or {}
        games = [g for g in ((event.get("match") or {}).get("games") or []) if g.get("id")]
    # Nur wirklich gespielte Spiele - abgesagte stehen als "unneeded" drin.
    return [g for g in games if g.get("state") in (None, "completed")]


# ---------------------------------------------------------------- Punkte

def score_line(k, d, a, cs):
    pts = (k * SCORING["kill"] + a * SCORING["assist"]
           + d * SCORING["death"] + cs * SCORING["cs"])
    if k >= SCORING["bigGameAt"] or a >= SCORING["bigGameAt"]:
        pts += SCORING["bigGame"]
    if d == 0:
        pts += SCORING["deathless"]
    return round(pts, 2)


def read_game(game_id, roster_players, roster_teams):
    """Eine Spielzeile je Teilnehmer aus dem Livestats-Fenster.

    Gibt None zurück, wenn der Feed für das Spiel (noch) nichts hat - dann
    bleibt das Spiel unverarbeitet und der nächste Lauf versucht es erneut.
    """
    data = livestats_window(game_id)
    if not data:
        return None
    frames = data.get("frames") or []
    if not frames:
        return None
    frame = frames[-1]

    meta = data.get("gameMetadata") or {}
    lines = []
    for side in ("blue", "red"):
        side_meta = meta.get(f"{side}TeamMetadata") or {}
        team_id = side_meta.get("esportsTeamId")
        participants = (frame.get(f"{side}Team") or {}).get("participants") or []
        by_pid = {p.get("participantId"): p for p in participants}

        for pm in side_meta.get("participantMetadata") or []:
            stats = by_pid.get(pm.get("participantId"))
            if not stats:
                continue
            player_id = pm.get("esportsPlayerId")
            if not player_id:
                continue
            k = int(stats.get("kills") or 0)
            d = int(stats.get("deaths") or 0)
            a = int(stats.get("assists") or 0)
            cs = int(stats.get("creepScore") or 0)

            # Spieler, die nicht im Kader von getTeams stehen (Ersatz, frisch
            # hochgezogen), bekommen ihren Eintrag aus den Livestats.
            if player_id not in roster_players:
                role = (pm.get("role") or "").lower()
                team = roster_teams.get(team_id)
                if role not in ROLES or not team:
                    continue
                roster_players[player_id] = {
                    "name": pm.get("summonerName") or "?",
                    "full": "",
                    "role": role,
                    "teamId": team_id,
                    "team": team["name"],
                    "code": team["code"],
                    "league": team["league"],
                    "image": "",
                    "teamImage": team["image"],
                }

            lines.append({
                "p": player_id,
                "tid": team_id,
                "c": pm.get("championId") or "",
                "k": k, "d": d, "a": a, "cs": cs,
                "pts": score_line(k, d, a, cs),
            })
    return lines or None


# ---------------------------------------------------------------- Speicher

def empty_data():
    return {
        "scoringVersion": SCORING_VERSION,
        "players": {},
        "rounds": {},
        "prices": {},
        "priceBase": {},
        "lines": [],
        "processed": [],
        "doneMatches": [],
        "failedMatches": {},
    }


def load_data():
    if REBUILD:
        print("FANTASY_REBUILD gesetzt - baue von vorn auf")
        return empty_data()
    if not os.path.exists(OUTPUT_PATH):
        return empty_data()
    try:
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"  ! {OUTPUT_PATH} nicht lesbar ({e}) - baue von vorn auf")
        return empty_data()
    if data.get("scoringVersion") != SCORING_VERSION:
        print("Punktesystem hat sich geändert - rechne die Saison neu durch")
        return empty_data()
    base = empty_data()
    base.update({k: data.get(k, base[k]) for k in base})
    for key in ("players", "rounds", "prices", "priceBase", "failedMatches"):
        if not isinstance(base[key], dict):
            base[key] = {}
    for key in ("lines", "processed", "doneMatches"):
        if not isinstance(base[key], list):
            base[key] = []
    return base


def add_to_round(data, round_key, start, end, player_id, pts, games=1):
    r = data["rounds"].setdefault(round_key, {"start": iso(start), "end": iso(end), "p": {}})
    r.setdefault("p", {})
    entry = r["p"].get(player_id) or [0, 0]
    entry = [round(entry[0] + pts, 2), entry[1] + games]
    r["p"][player_id] = entry


# ---------------------------------------------------------------- Preise

def compute_prices(data, round_key):
    """Preise einer Runde, eingefroren sobald sie einmal berechnet sind.

    Grundlage sind nur Runden *vor* der aktuellen - sonst würde sich der Preis
    mitten in der Woche ändern und ein längst gesetzter Kader plötzlich über
    dem Budget liegen. Innerhalb einer Rolle wird nach Rang verteilt: es gibt
    also auf jeder Position teure und billige Optionen, statt fünf teurer
    Midlaner und lauter billiger Supports.
    """
    past = sorted((k for k in data["rounds"] if k < round_key), reverse=True)[:PRICE_WINDOW_ROUNDS]
    # Einmal berechnete Preise bleiben stehen. Ausnahme: beim ersten Lauf gibt
    # es noch keine Historie und alle stehen auf dem Einheitspreis - der darf
    # sich nachziehen, sobald Spiele verarbeitet sind, sonst hinge die ganze
    # erste Woche an einem Zufallszeitpunkt.
    if round_key in data["prices"] and (data["priceBase"].get(round_key) or 0) > 0:
        return data["prices"][round_key]
    tally = {}
    for key in past:
        for pid, (pts, games) in (data["rounds"][key].get("p") or {}).items():
            t = tally.setdefault(pid, [0.0, 0])
            t[0] += pts
            t[1] += games

    by_role = {}
    for pid, (pts, games) in tally.items():
        player = data["players"].get(pid)
        if not player or games < PRICE_MIN_GAMES:
            continue
        by_role.setdefault(player["role"], []).append((pid, pts / games))

    prices = {}
    for role, entries in by_role.items():
        entries.sort(key=lambda x: x[1])
        n = len(entries)
        for i, (pid, _avg) in enumerate(entries):
            rank = i / (n - 1) if n > 1 else 0.5
            raw = PRICE_MIN + (PRICE_MAX - PRICE_MIN) * rank
            prices[pid] = round(raw * 2) / 2

    for pid in data["players"]:
        prices.setdefault(pid, PRICE_DEFAULT)

    data["prices"][round_key] = prices
    data["priceBase"][round_key] = len(past)
    return prices


# ---------------------------------------------------------------- Aufräumen

def prune(data, now):
    line_cutoff = iso(now - timedelta(days=LINE_RETENTION_DAYS))
    data["lines"] = [l for l in data["lines"] if l.get("t", "") >= line_cutoff]

    round_cutoff = iso(now - timedelta(days=ROUND_RETENTION_DAYS))
    for key in [k for k, r in data["rounds"].items() if (r.get("end") or "") < round_cutoff]:
        del data["rounds"][key]

    keep = set(data["rounds"]) | {round_bounds(now)[0]}
    for key in [k for k in data["prices"] if k not in keep]:
        del data["prices"][key]

    if len(data["processed"]) > PROCESSED_RETENTION:
        data["processed"] = data["processed"][-PROCESSED_RETENTION:]
    if len(data["doneMatches"]) > PROCESSED_RETENTION:
        data["doneMatches"] = data["doneMatches"][-PROCESSED_RETENTION:]


def season_totals(data, now):
    """Saisonsummen je Spieler aus den Rundensummen - immer neu gerechnet,
    damit sie nicht durch doppelt gezählte Spiele auseinanderlaufen können."""
    year = str(now.year)
    for p in data["players"].values():
        p["pts"], p["games"], p["avg"] = 0.0, 0, 0.0
    for r in data["rounds"].values():
        if not (r.get("start") or "").startswith(year):
            continue
        for pid, (pts, games) in (r.get("p") or {}).items():
            player = data["players"].get(pid)
            if not player:
                continue
            player["pts"] = round(player["pts"] + pts, 2)
            player["games"] += games
    for p in data["players"].values():
        if p["games"]:
            p["avg"] = round(p["pts"] / p["games"], 2)


# ---------------------------------------------------------------- Hauptlauf

def main():
    now = datetime.now(timezone.utc)
    data = load_data()

    print("Kader laden…")
    roster_players, roster_teams = load_roster()
    for pid, info in roster_players.items():
        entry = data["players"].setdefault(pid, {})
        entry.update(info)
        entry["active"] = True
    for pid, entry in data["players"].items():
        if pid not in roster_players:
            entry["active"] = False
    print(f"  {len(roster_players)} aktive Spieler in {len(roster_teams)} Teams")

    print("Abgeschlossene Matches suchen…")
    matches = collect_matches()
    print(f"  {len(matches)} Matches dieser Saison")

    done_matches = set(data["doneMatches"])
    new_games = 0
    skipped_score = 0
    retry_later = 0
    given_up = 0

    for match in matches:
        if new_games >= MAX_NEW_GAMES:
            break
        if match["id"] in done_matches:
            continue

        winner = series_winner(match)
        if winner is None:
            skipped_score += 1
            continue

        games = match_games(match)
        if not games:
            continue
        # Ein Match wird komplett verarbeitet oder gar nicht. Der Serien-Bonus
        # hängt am letzten Spiel eines Spielers in diesem Match - käme das
        # zweite Spiel erst im nächsten Lauf dazu, gäbe es den Bonus doppelt.
        if new_games + len(games) > MAX_NEW_GAMES and new_games > 0:
            break
        # Reihenfolge sichern, damit "letztes Spiel" auch das letzte ist.
        games.sort(key=lambda g: g.get("number") or 0)

        round_key, r_start, r_end = round_bounds(match["start"])
        started = iso(match["start"])

        match_lines = []
        complete = True
        for game in games:
            lines = read_game(game["id"], roster_players, roster_teams)
            if lines is None:
                complete = False
                break
            for line in lines:
                record = {
                    "g": game["id"], "m": match["id"], "r": round_key,
                    "t": started, "l": match["league"],
                    "p": line["p"], "c": line["c"],
                    "k": line["k"], "d": line["d"], "a": line["a"], "cs": line["cs"],
                    "pts": line["pts"],
                }
                if line["tid"] == winner:
                    record["w"] = True
                match_lines.append(record)

        if not complete or not match_lines:
            # Der Feed hat für dieses Match (noch) nicht alles. Ein paar Läufe
            # später erneut versuchen, dann aufgeben - sonst fragt jeder Lauf
            # bis in alle Ewigkeit dieselben toten Spiele ab.
            tries = (data["failedMatches"].get(match["id"]) or 0) + 1
            if tries >= 3:
                data["failedMatches"].pop(match["id"], None)
                data["doneMatches"].append(match["id"])
                done_matches.add(match["id"])
                given_up += 1
            else:
                data["failedMatches"][match["id"]] = tries
                retry_later += 1
            continue

        data["failedMatches"].pop(match["id"], None)

        # Serien-Bonus: einmal je Spieler des Siegerteams, angehängt an sein
        # letztes Spiel in diesem Match.
        last_of_player = {}
        for record in match_lines:
            if record.get("w"):
                last_of_player[record["p"]] = record
        for record in last_of_player.values():
            record["wb"] = True
            record["pts"] = round(record["pts"] + SCORING["seriesWin"], 2)

        for record in match_lines:
            data["lines"].append(record)
            add_to_round(data, round_key, r_start, r_end, record["p"], record["pts"])
            # Spieler, die erst über die Livestats aufgetaucht sind (Ersatz),
            # gehören ebenfalls in die Spielerliste.
            if record["p"] not in data["players"] and record["p"] in roster_players:
                data["players"][record["p"]] = dict(roster_players[record["p"]], active=True)

        for game in games:
            data["processed"].append(game["id"])
        data["doneMatches"].append(match["id"])
        done_matches.add(match["id"])
        new_games += len(games)

    print(f"  {new_games} neue Spiele verarbeitet")
    if skipped_score:
        print(f"  {skipped_score} Matches ohne plausiblen Endstand übersprungen")
    if retry_later:
        print(f"  {retry_later} Matches ohne vollständige Livestats - nächster Lauf versucht es erneut")
    if given_up:
        print(f"  {given_up} Matches endgültig ohne Livestats abgehakt")

    prune(data, now)
    season_totals(data, now)

    current_key, c_start, c_end = round_bounds(now)
    compute_prices(data, current_key)
    # Sortiert schreiben, damit der Diff im Repo lesbar bleibt.
    data["lines"].sort(key=lambda l: (l.get("t", ""), l.get("g", ""), l.get("p", "")))

    out = {
        "generated": iso(now),
        "scoringVersion": SCORING_VERSION,
        "leagues": FANTASY_LEAGUES,
        "roles": ROLES,
        "budget": BUDGET,
        "priceDefault": PRICE_DEFAULT,
        "priceRange": [PRICE_MIN, PRICE_MAX],
        "scoring": SCORING,
        "currentRound": {"key": current_key, "start": iso(c_start), "end": iso(c_end)},
        "players": data["players"],
        "rounds": data["rounds"],
        "prices": data["prices"],
        "priceBase": data["priceBase"],
        "lines": data["lines"],
        "processed": data["processed"],
        "doneMatches": data["doneMatches"],
        "failedMatches": data["failedMatches"],
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    size = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"{len(data['players'])} Spieler, {len(data['rounds'])} Runden, "
          f"{len(data['lines'])} Spielzeilen -> {OUTPUT_PATH} ({size:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
