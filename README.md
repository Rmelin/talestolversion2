# Talerstol v2

Styring af skærmen i en talerstol. En Raspberry Pi er både server og afviklingsmaskine:
den kører backend i Docker, viser den aktive scene på HDMI-skærmen og tilbyder et
kontrolpanel over det lokale netværk. Systemet virker helt uden internet.

**Vigtigste egenskab:** Forsvinder kontrolmaskinen (heartbeat udebliver i 120 s), skifter
Raspberry Pi'en selv til en foruddefineret fallback-scene – typisk et logo.

| Adresse | Formål | Login |
|---|---|---|
| `http://raspberrypi.local:8000/admin` | Kontrolpanel | Ja |
| `http://raspberrypi.local:8000/admin/settings` | Indstillinger, systemstatus, log | Ja |
| `http://raspberrypi.local:8000/display` | Talerstolens display (kiosk) | Nej\* |
| `http://raspberrypi.local:8000/monitor` | Read-only overvågning | Nej\* |
| `http://raspberrypi.local:8000/api/docs` | Interaktiv API-dokumentation | – |

\* Kan kræve login ved at sætte `PUBLIC_DISPLAY=false` / `PUBLIC_MONITOR=false`.

Port og hostname: porten sættes med `PORT` i `.env`; hostname sættes på Pi'en
(`sudo raspi-config` → System Options → Hostname) og nås via mDNS som `<navn>.local`.

---

## Teknologi

Samme opbygning som KRM-eva: Python 3.12, FastAPI, Jinja2, vanilla JavaScript, SQLite
(her via SQLModel), WebSockets, Uvicorn med én worker, Docker Compose. Ingen frontend-
frameworks, ingen CDN'er eller eksterne fonte. Billeder valideres med Pillow.

```
app/
  control.py     Tilstandsmaskine for kontrolsession/heartbeat (ren logik, monotont ur)
  runtime.py     Live- og preview-scene, publicering, fallback – én lås, samlede snapshots
  library.py     Scener, billeder, hurtigtekster, indstillinger, hændelseslog (database)
  main.py        HTTP/WebSocket-endpoints, watchdog-opgave
  auth.py        Login, CSRF-header, kontrolsessions-header
  realtime.py    WebSocket-udsendelse (kø pr. klient)
  static/js/
    scene-render.js     Fælles scenerenderer (display, preview, live, thumbnails, monitor)
    display.js          Display: forudindlæser billeder og skifter hele scenen på én gang
    admin.js            Kontrolpanel
    heartbeat-worker.js Heartbeat i Web Worker (neddrosles ikke i baggrundsfaner)
tests/           Automatiske tests (tilstandsmaskine, accepttest, API, sikkerhed)
deploy/kiosk/    Chromium-kiosk med systemd
```

---

## Installation på Raspberry Pi

Kræver Raspberry Pi OS 64-bit (Bookworm eller nyere) eller Ubuntu Server 64-bit.

1. **Installér Docker** (kræver internet én gang):
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER   # log ud og ind igen
   ```
2. **Hent projektet** til `~/talerstol` og opret konfiguration:
   ```bash
   cd ~/talerstol
   cp .env.example .env
   nano .env        # sæt mindst ADMIN_PASSWORD
   ```
3. **Start backend:**
   ```bash
   docker compose up -d --build
   ```
   Containeren genstarter automatisk ved fejl og ved boot (`restart: unless-stopped`) og har
   et Docker-healthcheck på `/api/health`. Status: `docker compose ps`, log: `docker compose logs -f`.
4. **Opsæt kiosk-display** (se nedenfor).
5. Åbn `http://<hostname>.local:8000/admin` fra kontrolmaskinen og log ind.

Ved første start oprettes tre scener: **Fallback – logo** (valgt som fallback), **Formand** og
**Dirigent**. Upload jeres logo og sæt det på fallback-scenen.

### Persistente data

Alt gemmes i Docker-volumen `talerstol-data` (`/data` i containeren):
`talerstol.db` (scener, indstillinger, hurtigtekster, log), `uploads/` (billeder og thumbnails)
og `secret_key`. Data overlever genstart af container og Pi samt `docker compose up --build`.

Backup:
```bash
docker run --rm -v talerstol_talerstol-data:/data -v "$PWD":/backup alpine tar czf /backup/talerstol-backup.tgz -C /data .
```

### Kiosk med Chromium og systemd (anbefalet)

Displayets browser kører direkte på Pi'en uden for Docker.

1. Slå skrivebords-autologin til og skærmslukning fra:
   `sudo raspi-config` → System Options → Boot / Auto Login → **Desktop Autologin**;
   Display Options → **Screen Blanking: Off**.
2. Portrætskærm: drej skærmen i *Screen Configuration* (Wayland/labwc) eller sæt
   `TALERSTOL_ROTATE=90` i servicefilen (bruger `wlr-randr`).
3. Installér servicen:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp ~/talerstol/deploy/kiosk/talerstol-kiosk.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now talerstol-kiosk
   ```

Scriptet venter på at backend svarer på `/api/health`, starter Chromium i `--kiosk` uden
menuer/dialoger og genstartes af systemd, hvis browseren lukker. Displayet genopretter selv
WebSocket-forbindelsen efter afbrydelser og beholder det senest viste billede imens.

---

## Brug

### Kontrolpanel

* **Overtag kontrol** – starter en kontrolsession og heartbeat. Holder en anden maskine
  kontrollen, spørges der om bekræftelse, før den overtages.
* **Sceneliste** – klik på en scene for at indlæse den i editoren og i **Preview**. Live
  påvirkes ikke. Scener kan oprettes, redigeres, omdøbes, duplikeres, sorteres (▲▼) og slettes.
* **Editor** – ændringer vises i *Kladde* og markeres "Ikke gemt", indtil der trykkes **Gem
  scene**. Live påvirkes aldrig af redigering.
* **Hurtigtekster** – 8 felter til forberedte navne/titler. Ændringer markeres med gult, indtil
  der trykkes **Gem**. **Klargør** lægger teksten på den valgte scene (baggrund) i preview.
* **Billeder** – upload (PNG, JPEG, WebP, GIF); de fem seneste vises, *Alle billeder …* viser
  resten. Brug et billede som baggrund eller logo uden at uploade igen.
* **VIS** – aktiverer præcis det, der står i preview, samlet. Har preview ændret sig siden du
  så det (fx fordi scenen er gemt fra en anden maskine), afvises VIS, så intet uventet vises.
* **Vis fallback nu** – viser fallback-scenen med det samme.
* **Frigiv kontrol** – afslutter kontrolsessionen; den aktuelle scene bliver stående og
  timeout er sat på pause. Browseren kan derefter lukkes.

### To driftstilstande

| | Aktiv kontrol | Frigivet kontrol |
|---|---|---|
| Heartbeat | Hvert 10. s fra kontrolpanelet | Ingen |
| Efter 30 s uden heartbeat | Advarsel | – |
| Efter 120 s uden heartbeat | Automatisk fallback | Scenen bliver stående (også i timevis) |
| Publicere ny scene | Ja | Kræver at kontrollen overtages igen |

Når forbindelsen kommer tilbage efter en fallback, **forbliver fallback-scenen aktiv**, indtil
teknikeren selv vælger en scene og trykker VIS. Kontrolpanelet viser årsagen.

Efter genstart af Pi eller container starter systemet altid i **fallback uden kontrolsession**
– også selvom kontrollen var frigivet før genstarten.

---

## Tilstandsmaskine

Tilstandsskift styres udelukkende af backend. En selvstændig asyncio-opgave (watchdog)
tjekker hvert sekund – uafhængigt af om nogen browser er forbundet. Tid måles med
`time.monotonic()`, så ændringer i systemuret ikke påvirker overvågningen.

```
                  acquire / takeover (alle tilstande)
        ┌──────────────────────────────────────────────┐
        ▼                                              │
  CONTROL_ACTIVE ──(intet heartbeat ≥ advarsel)──► CONTROL_WARNING
        ▲  │                                           │
        │  │ release           heartbeat ◄─────────────┤
        │  ▼                                           │ intet heartbeat ≥ timeout
  CONTROL_RELEASED                                     ▼ (og auto-fallback til)
                                                 FALLBACK_ACTIVE  ◄── opstart
```

* Heartbeat er knyttet til et tilfældigt sessions-id. En overtagelse ugyldiggør det gamle id,
  så en glemt fane på en anden maskine aldrig kan holde systemet "i live" eller publicere.
* Kun den aktive session må klargøre, publicere og frigive. *Vis fallback nu* er tilladt for
  alle indloggede, fordi det altid er en sikker handling.
* Heartbeat sendes fra en Web Worker og tæller kun, når serveren har svaret – en åben fane
  uden forbindelse giver altså ingen livstegn.

---

## API

Interaktiv dokumentation: `/api/docs` (OpenAPI: `/api/openapi.json`).

Alle skrivekald kræver login (cookie) og headeren `X-Talerstol-Client: 1` (CSRF-beskyttelse).
Kald, der kræver kontrol, sender desuden `X-Control-Session: <session_id>`.

| Metode | Sti | Beskrivelse |
|---|---|---|
| GET | `/api/health` | Healthcheck |
| GET | `/api/state` | Samlet tilstand: `live`, `preview`, `control`, `settings` |
| GET | `/api/system/status` | Systemstatus (forbindelser, disk, database, oppetid) |
| GET | `/api/events` | Hændelseslog |
| GET | `/api/control/status` | Kontroltilstand |
| POST | `/api/control/acquire` | Overtag kontrol (409 `control_busy` hvis en anden har den) → `session_id` |
| POST | `/api/control/takeover` | Overtag fra en anden maskine → `session_id` |
| POST | `/api/control/release` | Frigiv kontrol (kræver session) |
| POST | `/api/control/heartbeat` | Heartbeat (kræver session); alias `/api/heartbeat` |
| POST | `/api/fallback/activate` | Vis fallback nu |
| GET/POST | `/api/scenes` | List / opret scener |
| GET/PUT/DELETE | `/api/scenes/{id}` | Hent / redigér / slet scene |
| POST | `/api/scenes/{id}/duplicate` | Duplikér |
| PUT | `/api/scenes/order` | Sortér (`{"ids": [...]}`) |
| POST | `/api/scenes/{id}/preview` | Klargør (valgfrit `primary_text`, `secondary_text`, `quick_text_id`) – kræver session |
| POST | `/api/scenes/{id}/activate` | Vis scene direkte – kræver session |
| POST | `/api/preview/activate` | **VIS**: aktivér preview (`{"preview_rev": n}`) – kræver session |
| GET/POST | `/api/uploads` | List / upload billede (multipart `file`) |
| DELETE | `/api/uploads/{id}` | Slet billede (409 hvis det bruges) |
| GET/PUT | `/api/quicktexts` | Hurtigtekster |
| GET/PUT | `/api/settings` | Indstillinger |

Fejl returneres som `{"detail": "...", "code": "..."}` – fx `session_invalid`,
`control_busy`, `preview_changed`, `missing_file`, `is_fallback`, `bad_image`, `too_large`.

### WebSocket

Alle kanaler er envejs (server → klient). Ændringer sker kun via REST.

| Kanal | Adgang | Beskeder |
|---|---|---|
| `/ws/display` | offentlig\* | `{"type":"live", boot_id, rev, canvas, live}` ved forbindelse og hvert sceneskift; `ping` hvert 10. s |
| `/ws/admin` | login | `{"type":"state", ...}` (som `/api/state`), `{"type":"control", control, displays}` hvert 2. s, `{"type":"library", what}` når scener/billeder/hurtigtekster ændres |
| `/ws/monitor` | offentlig\* | som `/ws/admin`, minus `library` |

`live` indeholder hele det frosne sceneindhold inkl. billed-URL'er, så displayet kan
forudindlæse billeder og skifte alt på én gang.

---

## Sikkerhed

* Login til kontrolpanelet (sessionscookie, `SameSite=Lax`); adgangskode fra `.env`.
* Alle ændrende endpoints kræver login og CSRF-header; kontrolhandlinger kræver desuden et
  gyldigt sessions-id.
* Uploads: kun PNG/JPEG/WebP/GIF (verificeret med Pillow, SVG afvises), størrelsesgrænse
  (`MAX_UPLOAD_MB`), pixelgrænse, og filer gemmes under tilfældige navne – brugerens filnavn
  bruges aldrig som sti.
* Systemet er beregnet til et lukket LAN. Brug ikke standardadgangskoden.

---

## Udvikling og test

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
DATA_DIR=data ADMIN_PASSWORD=test .venv/bin/uvicorn --factory app.main:build --reload --port 8000
```

Testene bruger et falsk monotont ur og dækker bl.a.: manglende heartbeat (advarsel → fallback),
genoprettet forbindelse (fallback bliver stående), frigivelse i timevis, overtagelse (gammel
session kan ikke publicere/forhindre fallback), genstart (data bevares, start i fallback),
watchdog uden browser, uploadvalidering og adgangskontrol.

## Accepttest – manuel tjekliste

1. Genstart Pi'en → displayet viser fallback-logoet.
2. Åbn `/admin` fra en Mac, log ind, tryk **Overtag kontrol**.
3. Klik en scene, vælg evt. en hurtigtekst → preview opdateres, live er uændret.
4. Tryk **VIS** → billede og tekst skifter samlet.
5. Luk browseren → efter 30 s viser `/monitor` advarsel; efter 120 s vises fallback.
6. Åbn kontrolpanelet igen → årsagen vises; fallback bliver stående til der trykkes VIS.
7. Overtag kontrol, publicér, tryk **Frigiv kontrol**, luk browseren → scenen bliver stående (≥ 5 min).
8. Åbn kontrolpanelet på en anden maskine → **Overtag kontrol** med bekræftelse; den første kan ikke længere publicere.
9. Genstart Pi'en → fallback vises; scener, billeder og indstillinger er bevaret.
10. Træk netværkskablet til internettet → alt virker fortsat.
