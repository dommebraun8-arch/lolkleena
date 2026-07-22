# LoL Esports -> iPhone Kalender Sync

Synct automatisch den Profi-Spielplan (LEC, LCS, LCK, LPL, MSI, Worlds) in
deinen iPhone-Kalender, per Abo-URL. Läuft komplett auf GitHub, kostet nichts.

## 1. Repo einrichten

1. Neues **privates oder öffentliches** GitHub-Repo anlegen, z.B. `lol-calendar-sync`.
2. Diese drei Dateien reinlegen:
   - `lol_calendar_sync.py` -> ins Repo-Root
   - `sync.yml` -> nach `.github/workflows/sync.yml`
   - `README.md` -> ins Repo-Root (optional)
3. Einmal pushen.

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<dein-user>/lol-calendar-sync.git
git push -u origin main
```

## 2. Workflow einmal manuell anstoßen

Im Repo unter **Actions -> Sync LoL Schedule -> Run workflow** einmal von Hand
starten, damit `docs/lol-schedule.ics` erzeugt wird (sonst existiert der
Ordner `docs/` noch nicht für GitHub Pages).

## 3. GitHub Pages aktivieren

**Settings -> Pages**:
- Source: `Deploy from a branch`
- Branch: `main`, Ordner: `/docs`
- Speichern.

Nach ein-zwei Minuten ist die Datei erreichbar unter:

```
https://<dein-user>.github.io/lol-calendar-sync/lol-schedule.ics
```

> Wichtig: Wenn dein Repo **privat** ist, funktioniert GitHub Pages nur mit
> GitHub Pro/Team (private Pages). Für ein rein privates Setup entweder Repo
> public machen (die Kalenderdaten sind eh öffentlich - nur Spielpläne) oder
> GitHub Pro nutzen.

## 4. Auf dem iPhone abonnieren

1. **Einstellungen -> Kalender -> Accounts -> Account hinzufügen -> Andere**
2. **Kalenderabo hinzufügen**
3. Als Server-URL eingeben (mit `webcal://` statt `https://`):

```
webcal://<dein-user>.github.io/lol-calendar-sync/lol-schedule.ics
```

4. Speichern. iOS holt sich das Update automatisch in einem gewissen Intervall
   (steuerbar unter **Einstellungen -> Kalender -> Accounts -> [dein Abo] ->
   Aktualisieren -> z.B. alle 15 Minuten/stündlich**).

## Anpassen, welche Ligen drin sind

In `lol_calendar_sync.py` die Liste `WANTED_LEAGUES` editieren, z.B. nur:

```python
WANTED_LEAGUES = ["LEC"]
```

Mögliche Namen (kommen direkt von der Riot API): `LEC`, `LCS`, `LCK`, `LPL`,
`MSI`, `Worlds`, `EMEA Masters`, u.a. Bei Bedarf `get_leagues()` einmal lokal
ausgeben lassen um die genauen Namen zu sehen.

## Ergebnisse

Die gleiche API liefert für vergangene Spiele auch das Ergebnis mit. Das
Script wertet das automatisch aus:

- **Bevorstehendes Match:** `LEC: T1 vs G2`
- **Laufendes Match:** `LEC: T1 vs G2 (LIVE)`
- **Abgeschlossenes Match:** `LEC: T1 3:1 G2 (T1 gewinnt)`

Kein Extra-Setup nötig - der bestehende Sync-Rhythmus (alle 3 Stunden) reicht,
damit Ergebnisse zeitnah im Kalendertitel auftauchen. Wenn du es schneller
willst (z.B. Ergebnis quasi live), stell den Cron in `sync.yml` z.B. auf
`*/15 * * * *` (alle 15 Minuten).

## Hinweise

- Genutzt wird dieselbe (inoffizielle, aber stabile und öffentlich sichtbare)
  API, die lolesports.com selbst im Browser aufruft. Kein Login/Key nötig,
  Riot kann das aber theoretisch ändern - falls der Sync mal leer bleibt,
  zuerst in den Actions-Logs schauen.
- Cron läuft alle 3 Stunden - reicht locker, Spielpläne ändern sich nicht
  minütlich. Anpassbar in `sync.yml`.

## Tippspiel absichern (Cloudflare Worker)

GitHub Pages kann keine echte Passwortsperre (rein statisches Hosting), daher
läuft die Zugriffskontrolle über einen kostenlosen Cloudflare Worker
(`worker/gate-worker.js`), der:

- die komplette Seite hinter HTTP Basic Auth legt (nur Logins `domi` / `lisa`),
- alle Anfragen sonst unverändert an GitHub Pages durchreicht,
- den Tippspiel-Speicher (`/api/tipp`) über Cloudflare KV bereitstellt -
  ersetzt jsonblob.com, das keine Zugriffskontrolle kennt,
- über `/api/whoami` automatisch erkennt, wer eingeloggt ist (kein
  Namensfeld im Frontend mehr nötig).

### Einmaliges Setup

1. Kostenlosen Account auf [cloudflare.com](https://cloudflare.com) anlegen.
2. `npm install -g wrangler` und `wrangler login`.
3. KV-Namespace anlegen:
   ```bash
   wrangler kv namespace create TIPP_KV
   ```
   Die ausgegebene `id` in `wrangler.toml` bei `TIPP_KV` eintragen.
4. Passwörter als Secrets setzen (werden nicht im Code gespeichert):
   ```bash
   wrangler secret put DOMI_PASSWORD
   wrangler secret put LISA_PASSWORD
   ```
5. Deployen:
   ```bash
   wrangler deploy
   ```
   Danach ist der Worker unter `https://lolkleena-gate.<dein-subdomain>.workers.dev`
   erreichbar.

### Nutzung

- Ab jetzt **diese Worker-URL** statt der GitHub-Pages-URL besuchen/abonnieren
  (Browser fragt beim ersten Aufruf nach Nutzername/Passwort ab).
- Für den Kalender-Abo-Link (`webcal://…`) fragt iOS beim ersten Sync
  ebenfalls nach den Zugangsdaten, oder sie können direkt in die URL
  eingebettet werden: `webcal://domi:PASSWORT@lolkleena-gate.<sub>.workers.dev/lol-schedule.ics`.
- Die alte jsonblob-ID braucht niemand mehr - der Worker übernimmt den
  Speicher automatisch.
