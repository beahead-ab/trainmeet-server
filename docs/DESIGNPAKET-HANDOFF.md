# Överlämning — ombyggnaden mot designpaketet

Läs den här filen och [`DESIGNPAKET-DOD.md`](DESIGNPAKET-DOD.md) **innan** du
fortsätter. Checklistan är sanningen om vad som är gjort; den här filen är
sanningen om hur du tar vid.

Sessionen bytte här av ett enda skäl: nästa block är för stort för att riskera
att krav, beslut eller testbevis tappas. Det är ingen paus i projektet.

---

## Läge

| | |
|---|---|
| Gren | `claude/kor-bygg-struktur` |
| Senaste commit | `e3fece6` |
| Baserad på | `main` @ `ad75fec` |
| Arbetsträd | rent |
| Pushat | ja — `origin/claude/kor-bygg-struktur` = `e3fece6` |
| PR | **ingen öppnad** med flit |
| Version | `1.2.0` (roboten höjer vid merge; commit bär `[minor]`) |

### Commits på grenen

```
e3fece6  Make the source choice do the locking, not just say it
c4fbfba  Write down every binding requirement and where it stands
de4ccaa  Two modes instead of twelve menu points [minor]
```

---

## Vad som är byggt

**Block 1 — skelettet** (`de4ccaa`)

- KÖR/BYGG-lägen. `mode` är roten: enda variabeln som byter hela skelettet.
- Fem körflikar, fem byggsteg. Tolv menypunkter avvecklade.
- Mörkt topplock 50px i byggläget, applock 56px, flikar 30px — uppmätta.
- Designpaketets tokens ersätter de blå, mappade på befintliga semantiska namn.
- KÖR › Trafik byggd (ur ordning — den bär det dagliga värdet).
- v1-simuleringen borttagen, inklusive dess JS.

**Block 2 — källvalet** (`e3fece6`)

- BYGG steg 1 med tre källkort.
- Valet **är** serverns driftläge, inte en etikett.
- Ett CSP-fel som redan låg på `main` lagat.

### Ändrade filer

| Fil | Vad |
|---|---|
| `web/index.html` | nytt applock, sidopanel, trafikvy, TKL-vy, steg 1; simulatorvyn borttagen |
| `web/app.css` | paketets tokens; skelett-, trafik-, TKL- och steg 1-stilar |
| `web/app.js` | lägesmaskin, trafikrenderare, källval, TKL-iframe; v1-simulatorn borttagen |
| `http_server.py` | ikontillgångar serveras |
| `web/ikon/` | paketets ikon, 3 SVG + 8 PNG |
| `docs/ADMIN-UI-CONTRACT.md`, `docs/GRAPHIC_IDENTITY.md` | uppdaterade efter konflikt |
| `tests/test_kor_bygg_structure.py` | 21 tester, nytt |
| `tests/test_http_server.py` | skaltestet mot ny struktur |

---

## Beslut som måste bevaras

Bryt inte de här utan att först läsa varför.

### 1. Källvalet är driftläget

```
TrainMeet Cloud  ↔  cloud-linked   redigering låst
Lokalt utkast    ↔  offline-meet   redigering öppen
Importerad fil                     en åtgärd, inte ett läge
```

`locked = source === "cloud"` **beräknas aldrig i webbläsaren**. Den läses ur
`/v1/operating-mode`, så UI och server inte kan tycka olika om vem som får
redigera. Servern vägrar redan varje skrivväg i `cloud-linked` (`409
cloud_linked`) — det är inte UI:ts jobb att grinda, bara att visa.

Bygg inte en egen `source`-variabel bredvid. Den skulle bli en andra sanning.

### 2. Endast verkliga API:er

Skärmbildernas "Cloud rev 12 · 499 rörelser · 3 ändrade" är prototypdata. Ett
test faller om de strängarna dyker upp i källan. Stegräckans underrubriker
kommer ur `/v1/runtime` och `/v1/devices`, och samma svar föder både listan
och räckan så de inte kan visa olika tal.

### 3. Inga inline-stilar i HTML-strängar

Serverns CSP är `style-src 'self'`. Ett `style="width:N%"` i en `innerHTML`-
sträng avvisas tyst. Bygg med DOM-anrop och sätt värdet via CSSOM
(`element.style.width`), som inte omfattas. Två tester håller det shut.

### 4. Aktiva träffen rörs inte

Byggläget ändrar ingenting förrän en revision uttryckligen granskas och
aktiveras. Aktiveringsvägen finns redan i API:t sedan 1.2.0
(`+local-rN` via `SQLiteRuntimeStore.install`), men **är inte kopplad till
UI:t**. Koppla den — hitta inte på en ny.

### 5. Paketet styr utseendet

Vid konflikt med `ADMIN-UI-CONTRACT.md` eller `GRAPHIC_IDENTITY.md` vinner
paketet, och dokumentet uppdateras. Det har redan hänt en gång (blå → orange,
16px → 12/8px, 48px → 56px) och står i checklistans **Avvikelser**.

---

## Test

```bash
cd trainmeet-server
PYTHONPATH=src:tests python3 -m unittest discover -s tests -q      # 293, OK
PYTHONPATH=src:tests python3 -m unittest tests.test_kor_bygg_structure -q   # 21
PYTHONPATH=src:tests python3 -m unittest tests.test_operating_modes -q      # 13
```

Senaste körning: **293 gröna**.

### Känd flakighet

`test_mqtt_integration.test_physical_box_needs_only_its_printed_code_and_device_id`
faller ibland på `assignment_event.wait(30)` när maskinen är belastad. Kunde
inte reproduceras under 32 parallella loopar. **Kör om innan du felsöker** —
den har fallit och passerat på samma kod flera gånger den här sessionen.

### Verifiering i webbläsare

Servern måste vara färdiginstallerad, annars ligger `#app-view` på
`display: none` och Playwright hittar element som inte går att klicka:

```bash
PYTHONPATH=src python3 -m tmbox_gateway.local_server \
  --http-port 8951 --mqtt-port 18881 --state-dir /tmp/tm &

curl -s -c /tmp/cj -X POST -H 'Content-Type: application/json' \
  -d '{"username":"casper","password":"ett-langt-losenord"}' \
  http://127.0.0.1:8951/v1/setup/admin
curl -s -b /tmp/cj -X POST -H 'Content-Type: application/json' \
  -d '{"username":"casper","password":"ett-langt-losenord"}' \
  http://127.0.0.1:8951/v1/auth/login
curl -s -b /tmp/cj -X POST -H 'Content-Type: application/json' \
  -d '{"server_name":"Testservern"}' http://127.0.0.1:8951/v1/setup/server
# paket: json.dumps({"package": runtime_package_v3()}) från tests/runtime_fixture.py
curl -s -b /tmp/cj -X POST -H 'Content-Type: application/json' \
  -d @/tmp/pkg.json http://127.0.0.1:8951/v1/runtime/install
curl -s -b /tmp/cj -X POST -H 'Content-Type: application/json' \
  -d '{}' http://127.0.0.1:8951/v1/setup/complete
```

Playwright finns: `chromium.launch({ executablePath: "/opt/pw-browsers/chromium" })`.
Kör aldrig `playwright install`.

---

## Nästa arbetsblock: BYGG steg 5 — Server

Paketets DEL 3.11 och skärmbilderna `bygg-09-server.png`,
`bygg-10-server-uppdatering.png`. Rent flyttarbete som avvecklar tre
menypunkter till, utan att någon funktionalitet ändras.

Fyra block i ett steg:

1. **Identitet** — tre statusrutor (Server / Aktiv träff / Cloud), sedan
   redigerbart servernamn med *Spara servernamn*.
2. **Extern admininloggning** — användarnamn, nytt lösenord, upprepa, i ett
   tre-kolumners rutnät. Chip *Lokal åtkomst* och förklaringen om att admin
   öppnas utan inloggning på serverdatorn.
   **Inloggningsfältet ska fortsätta vara tomt** — rör inte den delen.
3. **Programuppdatering** — versionsrad (`1.2.0 · build …`) och den
   sjustegslista som redan finns och fungerar. Flytta, bygg inte om.
4. **Nollställ träffdata** — hopfällt `<details>`, kant `#e6cfc7`, botten
   `#fdf6f3`, kräver att man skriver NOLLSTÄLL.

Panelerna finns redan som `data-admin-section="access" | "software" | "system"`
och visas redan under steget. Arbetet är att ge dem paketets layout.

### Därefter, i ordning

| # | Block | Anteckning |
|---|---|---|
| 1 | BYGG 5 Server | ← nästa |
| 2 | BYGG 2 Stationer och sträckor | inkl. **`Bygg från stationsordningen`**, idempotent |
| 3 | BYGG 3 Tidtabell | tre grupperingar, massredigering; datamodell från `trainmeet-cloud` `src/model.ts` |
| 4 | BYGG 4 TMBoxar | kopplingsformulär + lista |
| 5 | KÖR Översikt | banschema med `renderTopology`, nästa rörelser, enheter |
| 6 | KÖR Skärmar | lista, adress, QR-platshållare |
| 7 | Hallskärm: Avgångstavla | paketet säger den först |
| 8 | Hallskärmarna 2–5 | klocka, banöversikt, nu-tavlan, tågdiagram |
| 9 | Degraderat läge | tona ner, ta bort tågen, gul list; trösklar konfigurerbara |
| 10 | Visuella regressionstester + bildjämförelser | 924px och desktop; hallskärmar 1920×1080 |

Sedan: fyll i checklistan, öppna PR, och lämna slutrapporten.

---

## Vad som måste bifogas igen i en ny session

Zipfilen är **inte** incheckad, med flit — det är prototypmaterial. Bifoga
den igen:

```
TrainMeet Server.zip
```

Det som faktiskt behövs ur den:

| Fil | Varför |
|---|---|
| `README.md` | bindande specifikation, 760 rader |
| `skarmbilder/bygg-09-server.png`, `bygg-10-server-uppdatering.png` | nästa block |
| `skarmbilder/bygg-04`, `05`, `06`, `07`, `08` | blocken därefter |
| `skarmbilder/kor-01`, `04` | KÖR-vyerna |
| `skarmbilder/skarm-01`…`06` | hallskärmarna och degraderat läge |
| `TrainMeet Skärmar.dc.html` | hallskärmarnas exakta markup |
| `TrainMeet Server - ny struktur.dc.html` | KÖR-vyernas exakta markup |

Ikonerna behöver **inte** bifogas igen — de ligger i repot under
`src/tmbox_gateway/web/ikon/`.

Designfilerna har bara `isKor*`-grenar. BYGG-stegens exakta markup finns
alltså **inte** i HTML:en — bara som prosa i README och som skärmbilder. Räkna
med att läsa bilderna.

---

## Risker

- **Omfattningen.** Ungefär en femtedel är gjord. Resten är minst lika mycket
  arbete igen, och hallskärmarna är i praktiken fem nya sidor.
- **`selectRunTab` och `selectBuildStep`** styr synligheten för allt. Lägger
  du till en panel utan att koppla in den i `RUN_PANELS` eller
  `STEP_SECTIONS` blir den antingen alltid synlig eller aldrig.
- **Inga bildjämförelser är gjorda ännu.** Måtten är punktvis uppmätta i
  webbläsaren, men ingen sida har jämförts mot sin skärmbild.
- **D9 och D10 kan inte utföras** från utvecklingsmiljön: proxyn blockerar
  `trainmeet.app` och det finns ingen SSH till Pi:n.
