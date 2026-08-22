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
| Senaste commit | se `git log` — grenen har fortsatt förbi `198b73f` |
| Baserad på | `main` @ `ad75fec` |
| Arbetsträd | rent |
| Pushat | ja — `origin/claude/kor-bygg-struktur` |
| PR | **ingen öppnad** med flit |
| Version | `1.2.0` (roboten höjer vid merge; commit bär `[minor]`) |

### Commits på grenen

```
198b73f  Hand over honestly: correct the checklist and write down how to take over
e3fece6  Make the source choice do the locking, not just say it
c4fbfba  Write down every binding requirement and where it stands
de4ccaa  Two modes instead of twelve menu points [minor]
```

plus blocket nedan för BYGG 5.

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

**Block 3 — BYGG steg 5 Server**

- Paketets fyra block: identitet, extern admininloggning, programuppdatering,
  nollställning. Tre menypunkter till avvecklade.
- Rent flyttarbete. De sju uppdateringsstegen kommer fortfarande ur
  `update_contract.py` och är inte omskrivna — de fick bara paketets radlayout
  med tillståndsordet i högerkanten.
- Den gamla "DRIFT / Aktiv träff"-listen borttagen. Ingen kod skrev den längre
  och den sa emot varje byggstegs egen rubrik. Med den försvann `adminSections`,
  `state.selectedAdminSection` och ett anrop till `selectAdminSection`, som inte
  längre fanns — en latent krasch.
- 17 nya tester, 76 kontroller i webbläsaren. Se checklistans **Webbläsarbevis**.

**Block 4 — kvalitetsrunda på steg 5**

- Testskillnaden mellan sessionerna utredd och nedskriven ovan. Inget saknas.
- Avvikelse 4 lagad, **scopat till steg 5**: paketets 8px-form, kant `#e0dcd1`,
  34px höjd, vit sekundär och `#c96442` primär. Den globala `button`-regeln är
  orörd, och att KÖR är oförändrat är bevisat genom att jämföra mot samma sida
  serverad från `198b73f` - inte mot en förväntan.
- Den destruktiva knappen behåller sin röda färg. Se beslut 8.
- Fem nya tester för knappscopet, alla mutationsprovade.

### Ändrade filer

| Fil | Vad |
|---|---|
| `web/index.html` | nytt applock, sidopanel, trafikvy, TKL-vy, steg 1 och steg 5; simulatorvyn och den gamla adminlisten borttagna |
| `web/app.css` | paketets tokens; skelett-, trafik-, TKL-, steg 1- och steg 5-stilar |
| `web/app.js` | lägesmaskin, trafikrenderare, källval, TKL-iframe, steg 5:s rendering; v1-simulatorn och `adminSections` borttagna |
| `http_server.py` | ikontillgångar serveras |
| `web/ikon/` | paketets ikon, 3 SVG + 8 PNG |
| `docs/ADMIN-UI-CONTRACT.md`, `docs/GRAPHIC_IDENTITY.md` | uppdaterade efter konflikt |
| `tests/test_kor_bygg_structure.py` | 43 tester |
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

### 5. Sju uppdateringssteg, ett kontrakt

Stegen och etiketterna kommer ur `update_contract.py`, som är kopierad ordagrant
till `trainmeet-cloud`. Webbläsaren ritar dem och översätter tillståndet till
ett ord (`Klar` / `Pågår` / `Väntar` / `Fel`) — den hittar inte på ett steg, en
etikett eller ett femte tillstånd. Ett test faller om en etikett dyker upp som
sträng i `app.js`.

### 6. Nollställningen är två olika saker

`access_mode: local` ⇒ fabriksåterställning, administratören raderas.
`access_mode: external` ⇒ bara träffdata. Sammanfattningen på det hopfällda
`<details>` är det enda som syns innan man öppnar, så den måste säga vilken av
dem det blir. Skriv inte paketets fasta "Nollställ träffdata" där.

### 7. Knapparnas form är scopad med flit

`.server-step-card button` bär paketets form. Den globala `button`-regeln är
kvar som den var, och det är inte slarv: den slår igenom på varje KÖR-vy och
på byggsteg 1, som redan står 👁 i checklistan. Byter man den globalt måste
allt det verifieras om i webbläsaren, och det är ett eget block.

Lägger du till ett kort i steg 5: **ge det klassen `server-step-card`**, annars
får dess knappar pillerformen. Ett test räknar att exakt fyra kort bär den.

### 8. Den destruktiva knappen är röd med flit

Paketets adminpalett innehåller ingen röd, eftersom nollställningen ligger
hopfälld i varenda skärmbild och knappen därför aldrig syns. Att ge den
accentfärgen skulle göra *Installera och starta om* och *Fabriksåterställ
servern* till samma knapp. Formen följer paketet; färgen gör det inte. Ett test
faller om den byts till accentfärgen.

### 9. Paketet styr utseendet

Vid konflikt med `ADMIN-UI-CONTRACT.md` eller `GRAPHIC_IDENTITY.md` vinner
paketet, och dokumentet uppdateras. Det har redan hänt en gång (blå → orange,
16px → 12/8px, 48px → 56px) och står i checklistans **Avvikelser**.

---

## Test

```bash
cd trainmeet-server
pip install paho-mqtt          # annars faller 4 MQTT-moduler på importfel
apt-get install -y mosquitto   # annars hoppas 2 tester över, se nedan
PYTHONPATH=src:tests python3 -m unittest discover -s tests -q               # 315, OK
PYTHONPATH=src:tests python3 -m unittest tests.test_kor_bygg_structure -q   # 43
PYTHONPATH=src:tests python3 -m unittest tests.test_operating_modes -q      # 13
```

Senaste körning: **315 gröna, inga överhoppade.**

### Varför siffran hoppade mellan sessionerna

Två sessioner rapporterade olika tal för **samma commit** `198b73f`: 293 gröna
respektive 291 gröna med en överhoppad. Utrett, och orsaken är miljön - inget
test har försvunnit, bytt namn eller slutat upptäckas.

`tests/test_mqtt_integration.MQTTIntegrationTests.setUpClass` gör

```python
executable = shutil.which("mosquitto")
if executable is None:
    raise unittest.SkipTest("mosquitto is not installed")
```

En `SkipTest` i `setUpClass` hoppar över **klassen**, inte metoderna. unittest
räknar då en (1) överhoppning och lägger **inte** klassens två testmetoder till
`testsRun`. Därav skillnaden på exakt två:

| Miljö | Utfall på `198b73f` |
|---|---|
| med `mosquitto` | `Ran 293 tests ... OK` |
| utan `mosquitto` | `Ran 291 tests ... OK (skipped=1)` |

Båda är verifierade genom att köra sviten i en `git worktree` på `198b73f`, en
gång med och en gång utan `mosquitto` på `PATH`. Att den föregående sessionen
såg 293 stämmer alltså - den hade brokern installerad, vilket också är varför
den kunde se `test_physical_box_needs_only_its_printed_code_and_device_id`
flaxa: det testet kan bara flaxa om det faktiskt kör.

Att inget test tappats är kontrollerat separat, inte antaget. En inventering
räknar upp varje test-id `unittest.TestLoader().discover()` hittar, utan att
köra dem, och de två listorna jämförs:

```
198b73f: 293 test-id, 0 laddningsfel
HEAD:    315 test-id, 0 laddningsfel
borttagna: 0     tillagda: 22
```

De 22 tillagda ligger alla i `BuildStepFiveServerTests`.

**Installera mosquitto innan du kör sviten.** Annars ser du 313 + 1 överhoppad
i stället för 315, och integrationstesterna mot en riktig broker kör inte alls.

### Känd flakighet

Gäller bara när `mosquitto` finns - utan den kör testet inte.

`test_mqtt_integration.test_physical_box_needs_only_its_printed_code_and_device_id`
faller ibland på `assignment_event.wait(30)` när maskinen är belastad. Kunde
inte reproduceras under 32 parallella loopar. **Kör om innan du felsöker** —
den har fallit och passerat på samma kod flera gånger den här sessionen.

### Verifiering i webbläsare

Servern måste vara färdiginstallerad, annars ligger `#app-view` på
`display: none` och Playwright hittar element som inte går att klicka.

`local_server` vill ha en MQTT-broker och vägrar starta utan `mosquitto` på
`PATH`. Bygg i stället en `TrainMeetHTTPApplication` direkt,
precis som `tests/test_http_server.py` gör, och kör den i en tråd. Det ger
samma markup, samma CSP-headers och samma API — bara utan MQTT, som adminvyn
ändå inte rör. Sätt `allow_restart=True` och `allow_software_update=True`, annars
svarar uppdateringsvägen att miljön inte stöds.

Chromium ligger på `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`.
`paho-mqtt` måste installeras innan testsviten körs, annars faller fyra
MQTT-moduler på importfel.

```bash
# starta servern enligt ovan på port 8951, sedan:

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

Kör aldrig `playwright install` — `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
npm install playwright` räcker, binären finns redan.

---

## Nästa arbetsblock: BYGG 2 Stationer och sträckor

Paketets DEL 3.8 och skärmbilderna `bygg-04-stationer-och-strackor.png` och
`bygg-05-strackor-och-paneler.png`. Tre numrerade sektioner i **ett** kort,
plus genvägen *Bygg från stationsordningen*, som måste vara idempotent.

Det är också första steget där källåsningen får något att låsa: `data-source-locked`
sätts redan, men steg 2 och 3 fanns inte att låsa förrän nu. Räkna med att
3.7.4 och 5.5 kan gå från 🔨 till verifierade när steget finns.

Ge kortet klassen `server-step-card` om du vill ha paketets knappform i det —
tills den globala regeln byts är formen scopad per steg.

### Därefter, i ordning

| # | Block | Anteckning |
|---|---|---|
| 1 | BYGG 2 Stationer och sträckor | ← nästa; inkl. **`Bygg från stationsordningen`**, idempotent |
| 2 | BYGG 3 Tidtabell | tre grupperingar, massredigering; datamodell från `trainmeet-cloud` `src/model.ts` |
| 3 | BYGG 4 TMBoxar | kopplingsformulär + lista |
| 4 | KÖR Översikt | banschema med `renderTopology`, nästa rörelser, enheter |
| 5 | KÖR Skärmar | lista, adress, QR-platshållare |
| 6 | Knappformen globalt | lyft `.server-step-card`-reglerna till `button`; kräver omverifiering av allt som står 👁 |
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
| `skarmbilder/bygg-04`, `05`, `06`, `07`, `08` | nästa block |
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

- **Omfattningen.** Ungefär en fjärdedel är gjord. Resten är mer arbete än så
  igen, och hallskärmarna är i praktiken fem nya sidor.
- **`selectRunTab` och `selectBuildStep`** styr synligheten för allt. Lägger
  du till en panel utan att koppla in den i `RUN_PANELS` eller
  `STEP_SECTIONS` blir den antingen alltid synlig eller aldrig.
- **Bara steg 5 är bildjämfört.** Övriga sidor är punktvis uppmätta men aldrig
  ställda mot sin skärmbild.
- **D9 och D10 kan inte utföras** från utvecklingsmiljön: proxyn blockerar
  `trainmeet.app` och det finns ingen SSH till Pi:n.
