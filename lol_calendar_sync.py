"""
LoL Esports -> iCal Sync
------------------------
Holt den Spielplan von LEC/LCS/LCK/LPL/MSI/Worlds über die öffentliche
Riot Esports API (dieselbe, die lolesports.com im Frontend nutzt) und
schreibt eine .ics Datei nach docs/lol-schedule.ics.

Diese Datei wird von der GitHub Action regelmäßig neu erzeugt und
per GitHub Pages ausgeliefert -> iPhone abonniert die URL als Kalender.
"""

import os
import uuid
from datetime import datetime, timezone

import requests

# Öffentlicher API-Key, den auch die lolesports.com Website im Browser nutzt.
API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
BASE = "https://esports-api.lolesports.com/persisted/gw"
HEADERS = {"x-api-key": API_KEY}

# Hier anpassen, welche Ligen du im Kalender haben willst.
# Mögliche Namen u.a.: "LEC", "LCS", "LCK", "LPL", "MSI", "Worlds", "EMEA Masters"
WANTED_LEAGUES = ["LEC", "LCS", "LCK", "LPL", "MSI", "Worlds"]

OUTPUT_PATH = "docs/lol-schedule.ics"


def get_leagues():
    r = requests.get(f"{BASE}/getLeagues", headers=HEADERS, params={"hl": "en-GB"}, timeout=20)
    r.raise_for_status()
    return r.json()["data"]["leagues"]


def get_schedule(league_id):
    events = []
    params = {"hl": "en-GB", "leagueId": league_id}
    # Die API liefert Seiten mit "older" Token - wir holen uns erstmal die aktuelle Seite,
    # das reicht für "kommende + letzte" Spiele um den heutigen Tag herum.
    r = requests.get(f"{BASE}/getSchedule", headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()["data"]["schedule"]
    events.extend(data.get("events", []))
    return events


def fmt_dt(iso_str):
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def escape(text):
    return (
        text.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def build_ics(events):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LoL Esports Sync//DE",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:LoL Esports Spielplan",
        "REFRESH-INTERVAL;VALUE=DURATION:PT3H",
        "X-PUBLISHED-TTL:PT3H",
    ]

    seen_ids = set()

    for ev in events:
        match = ev.get("match")
        start = ev.get("startTime")
        if not match or not start:
            continue

        teams = match.get("teams", [])
        if len(teams) < 2:
            continue

        uid = match.get("id") or str(uuid.uuid4())
        if uid in seen_ids:
            continue
        seen_ids.add(uid)

        t1 = teams[0].get("name", "TBD")
        t2 = teams[1].get("name", "TBD")
        league_name = ev.get("league", {}).get("name", "")
        state = ev.get("state", "")  # "unstarted" | "inProgress" | "completed"

        result1 = teams[0].get("result") or {}
        result2 = teams[1].get("result") or {}

        if state == "completed" and result1 and result2:
            score1 = result1.get("gameWins", 0)
            score2 = result2.get("gameWins", 0)
            winner = t1 if result1.get("outcome") == "win" else t2
            matchup = f"{t1} {score1}:{score2} {t2}"
            summary = escape(f"{league_name}: {matchup} ({winner} gewinnt)")
            description = escape(
                f"{league_name} - Ergebnis: {matchup}, Sieger: {winner}"
            )
        elif state == "inProgress":
            summary = escape(f"{league_name}: {t1} vs {t2} (LIVE)")
            description = escape(f"{league_name} Match läuft gerade - {ev.get('blockName', '')}".strip())
        else:
            summary = escape(f"{league_name}: {t1} vs {t2}")
            description = escape(f"{league_name} Match - {ev.get('blockName', '')}".strip())

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@lolesports-sync",
            f"DTSTAMP:{now}",
            f"DTSTART:{fmt_dt(start)}",
            "DURATION:PT1H30M",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def main():
    leagues = get_leagues()
    target_ids = [l["id"] for l in leagues if l["name"] in WANTED_LEAGUES]

    if not target_ids:
        print("WARNUNG: Keine passenden Ligen gefunden - Namen in WANTED_LEAGUES prüfen.")

    all_events = []
    for lid in target_ids:
        try:
            all_events += get_schedule(lid)
        except Exception as e:
            print(f"Fehler beim Laden von League {lid}: {e}")

    ics_content = build_ics(all_events)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(ics_content)

    print(f"{len(all_events)} Events verarbeitet, geschrieben nach {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
