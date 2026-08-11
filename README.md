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

### Nach einem Update: Worker neu deployen

Der Worker beantwortet seit dem Worlds-Tippspiel zusätzlich
`POST /api/tipp/pick`. Dort mergt er einen einzelnen Tipp serverseitig und
schreibt dabei nur die Felder des eingeloggten Nutzers - vorher lud jeder
Browser den kompletten Speicher hoch und konnte damit die Tipps des anderen
überschreiben, wenn beide gleichzeitig offen waren.

```bash
wrangler deploy
```

Vergisst man das, bleibt die Seite benutzbar: sie merkt am 404 des Endpunkts,
dass noch die alte Worker-Version läuft, und fällt automatisch auf den früheren
Weg (`PUT /api/tipp`) zurück.

### Nutzung

- Ab jetzt **diese Worker-URL** statt der GitHub-Pages-URL besuchen/abonnieren
  (Browser fragt beim ersten Aufruf nach Nutzername/Passwort ab).
- Für den Kalender-Abo-Link (`webcal://…`) fragt iOS beim ersten Sync
  ebenfalls nach den Zugangsdaten, oder sie können direkt in die URL
  eingebettet werden: `webcal://domi:PASSWORT@lolkleena-gate.<sub>.workers.dev/lol-schedule.ics`.
- Die alte jsonblob-ID braucht niemand mehr - der Worker übernimmt den
  Speicher automatisch.

## Worlds-Tippspiel

Eigener Tab **Worlds**, getrennt vom wöchentlichen Liga-Tippspiel. Ein Turnier
hat keine sinnvollen Kalenderwochen, deshalb sind die Runden hier die
Turnierphasen: Play-In, Swiss-Stage, Viertelfinale, Halbfinale, Finale.

### Wetten und Punkte

| Wette | Punkte | Gesperrt ab |
| --- | --- | --- |
| Match-Tipp (exaktes Ergebnis) | 2 | Anpfiff des Matches |
| Match-Tipp (nur Sieger richtig) | 1 | Anpfiff des Matches |
| Champion (wer gewinnt Worlds) | 10 | erstem Spiel des Turniers |
| Swiss: geht 3:0 durch (2 Teams) | 3 je Treffer | erstem Swiss-Spiel |
| Swiss: fliegt 0:3 raus (2 Teams) | 3 je Treffer | erstem Swiss-Spiel |
| Swiss: kommt ins Viertelfinale (8 Teams) | 1 je Treffer | erstem Swiss-Spiel |
| Bracket: Viertelfinale | 1 je Treffer | erstem Viertelfinale |
| Bracket: Halbfinale | 2 je Treffer | erstem Viertelfinale |
| Bracket: Finale | 3 | erstem Viertelfinale |

Das Bracket wird kaskadierend getippt: die Halbfinal-Auswahl besteht aus den
eigenen Viertelfinal-Siegern, die Final-Auswahl aus den eigenen Halbfinal-
Siegern. Tippt man ein Viertelfinale um, verfallen automatisch die Folgetipps,
die dieses Team gebraucht hätten.

Wie im Liga-Tippspiel sieht man fremde Tipps erst, wenn man selbst getippt hat
(oder die Sperre durch ist).

### Woher die Daten kommen

- Spielplan und Ergebnisse: `getSchedule` (laufend/kommend) zusammengeführt mit
  `getCompletedEvents` (das ganze Turnier, auch ältere Runden).
- Turnierphase: aus `blockName` des Spiels ("Swiss Stage Round 2",
  "Quarterfinals", …), für die KO-Runden bevorzugt aus der Bracket-Struktur von
  `getStandings`.
- KO-Baum inkl. Zuordnung, welcher Sieger in welches Halbfinale rückt: aus
  `previousMatchIds` von `getStandings`. Liefert die API dazu noch nichts, wird
  der Baum aus der Spielplan-Reihenfolge abgeleitet und die Seite weist darauf
  hin.
- Swiss-Bilanzen (3:0, 0:3, weiter) werden aus den Spielergebnissen selbst
  gerechnet, nicht aus der Tabelle - die Ergebnisse liegen ohnehin vor.

Fehlt eines dieser Felder, bricht nichts: die betroffene Wette meldet sich mit
einem Hinweis ab, der Rest bleibt bedienbar.

Über die Auswahl unter der Kopfzeile lässt sich auch ein vergangenes Worlds
ansehen; Tipps werden pro Jahrgang gespeichert.

## Design

`.claude/skills/apple-design/SKILL.md` hält fest, nach welchen Regeln die
Oberfläche gebaut ist. Umgesetzt ist:

- **Seitenleiste statt Tab-Reihe.** Die Navigation steht links, bleibt beim
  Scrollen stehen und wächst auf breiten Schirmen mit (66px am Telefon, 92px am
  Rechner). Der Inhalt liegt in Boxen ungleicher Breite, teils eingerückt,
  statt in gleich breiten Streifen untereinander. Oben steht eine Hero-Kachel
  mit dem Spiel, das als Nächstes ansteht.
- **Lieblingsteam als feste Spalte.** Rechts neben dem Inhalt steht die
  Teamauswahl. Einmal gewählt, merkt der Browser sich das Team
  (`localStorage`, Schlüssel `lolkleena.favTeam`) und die Spalte zeigt von da
  an dauerhaft dessen Spiele - live, kommend und die letzten Ergebnisse.
  Über "Ändern" kommt die Auswahl zurück. Ist `localStorage` gesperrt (privates
  Fenster), verhält sich die Spalte wie vorher und fragt jedes Mal neu.
- **Kalender im eigenen Fenster.** Das Abo braucht man einmal, danach nie
  wieder - deshalb liegt es hinter dem Knopf *Kalender* statt dauerhaft im
  Start-Tab. Dort steht auch ein Countdown bis zum ersten Worlds-Spiel; in der
  Kopfzeile bleibt eine Kurzfassung sichtbar. Der Termin kommt aus dem ersten
  angesetzten Anpfiff, ersatzweise aus dem Turnierdatum der API.
- **Material statt Rechteck (§12).** Flächen haben Tiefe: abgestufte
  Schichten (`--mat-1/2/3`), Schatten, die mit der Flächengröße zunehmen, und
  ein hellerer Rand an der Oberkante, als fiele Licht von oben darauf. Die
  Tab-Leiste ist durchscheinend, der Inhalt läuft sichtbar darunter durch, und
  statt einer harten Trennlinie steht dort ein weicher Verlauf.
- **Bewegung aus Federn (§4).** Die `--ease-*`-Kurven sind keine geratenen
  Béziers, sondern aus Apples Dämpfung/Response ausgerechnet und als CSS
  `linear()` abgetastet: 1.0 (kein Überschwingen) für den Normalfall, 0.8 für
  Flächen, die auftauchen.
- **Sofortige Rückmeldung (§1, §10).** Die Seite läuft am Handy, dort gibt es
  kein Hover - die Rückmeldung hängt deshalb am Fingerdruck. Ein Tipp erscheint
  sofort statt erst nach der Serverantwort und wird bei fehlgeschlagenem
  Speichern sichtbar zurückgenommen.
- **Räumliche Konsistenz (§7).** Modals wachsen aus dem angetippten Element
  heraus und verschwinden auf demselben Weg wieder.
- **Typografie (§15).** Laufweite und Zeilenhöhe hängen an der Schriftgröße:
  große Schrift enger und dichter, Kleinschrift offener. Abstände in `rem`,
  damit eine größere Systemschrift das Layout mitnimmt.
- **Systemeinstellungen (§14).** `prefers-reduced-motion`,
  `prefers-reduced-transparency` und `prefers-contrast` sind bedient, Hover-
  Effekte nur unter `@media (hover: hover)`, Tastaturfokus sichtbar, Escape
  schließt offene Panels.
