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
7cec157  Give the Server step the package's button shape, and explain the test count
abc6eb2  Gather the three system menu points into one Server step
198b73f  Hand over honestly: correct the checklist and write down how to take over
e3fece6  Make the source choice do the locking, not just say it
c4fbfba  Write down every binding requirement and where it stands
de4ccaa  Two modes instead of twelve menu points [minor]
```

plus blocket nedan för BYGG 2 i låst läge.

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

### 2. Bara Cloud tolkar underlag, och synken går åt ett håll

Det normala produktflödet, och den enda riktning data rör sig i:

```
PDF / JPG / PNG
  → tolkning och granskning i TrainMeet Cloud
  → publicerad revision (grundkonfigurationen)
  → Servern hämtar den
```

Fyra gränser som **inte** får suddas ut:

1. **PDF, JPG och PNG tolkas endast i TrainMeet Cloud.** Ingen PDF-parser,
   ingen bildtolkning, ingen extraktions- eller granskningskö byggs i servern.
   `pdftoppm`, modellanrop och granskningsvyer hör hemma i `trainmeet-cloud`
   och ska stanna där.
2. **Cloud publicerar grundkonfigurationen.** Stationer, sträckor och den
   ursprungliga tidtabellen kommer därifrån.
3. **Synk går endast Cloud → Server.** Ändringar som görs på servern skickas
   *aldrig* upp till Cloud. Servern är inte en redaktör åt Cloud; den är
   drift, och driftens rättelser stannar i driften.
4. **Serverns manuella filimport är ett exporterat JSON-driftpaket.** Aldrig
   en PDF. Det är reservvägen när Cloud inte går att nå alls.

Om en framtida uppgift verkar kräva PDF-hantering eller uppladdning till Cloud
i servern: det är fel repo, respektive fel riktning.

### 3. Låst är inte samma sak för banan och för tidtabellen

Det här är den gräns som är lättast att dra fel, eftersom "Cloud-läge" låter
som ett läge där allt är låst. Det är det inte.

| Steg | Cloud-läge | Varför |
|---|---|---|
| **2 · Bana** — stationer, sträckor, paneler | **Skrivskyddat** | Banan är tolkad ur underlaget och granskad i Cloud. Den ritas en gång och ligger still. |
| **3 · Tidtabell** | **Alltid redigerbar** | Under träffen *är* servern driften. Tåg blir sena och rörelser ställs in, och då finns ingen väg via Cloud. |

I lokalt läge är båda redigerbara.

Grinden heter `_require_topology_unchanged()` och gäller
`TOPOLOGY_SECTIONS = ("stations", "connections", "panels")` — inget annat. Den
gamla globala `_require_editing_open()` är **borttagen**, och ett test faller
om den kommer tillbaka.

Tre saker som hänger ihop med den och som är lätta att bryta:

1. **Sådd är tillåten i Cloud-läge.** En kopia är ingen ändring. Att vägra den
   lämnar tidtabellen utan något att redigeras *i*.
2. **Aktivering kontrollerar det utkast som aktiveras**, inte det som råkade
   sparas sist. Spara och aktivera är två anrop.
3. **Radordning är ingen ändring.** Jämförelsen sorterar på id.

Två följdregler om revisionerna:

1. **Lokala tidtabellsändringar blir lokala runtime-revisioner**
   (`<bas>+local-rN`). De är versioner, inte överskrivningar.
2. **En ny Cloud-revision aktiveras aldrig tyst.** Se nästa beslut.

### 3b. Pollern hämtar, men beslutar aldrig

`auto_sync_cloud_runtime()` körde var 15:e sekund med `install()`, vars
`activate` är `True` som default, och pollerloopen anropade sedan
`server.request_restart()`. En operatör som rättat tre avgångstider kl 13
förlorade dem när Cloud publicerade kl 14 — och träffen startade om medan det
hände.

Nu: hämta, lagra, markera som väntande, sluta. Ingen aktivering, ingen omstart,
och i `offline-meet` ingen hämtning alls — där är servern medvetet redaktör,
och att köa revisioner ingen bett om vore att lägga ett "väntar"-märke över
arbete som lämnat Cloud med flit.

`/v1/runtime/pending/activate` kräver att revisionens `publication_id` skickas
tillbaka och stämmer. Utan det kan en flik som stått öppen sedan i morse
aktivera något som kommit sedan dess — samma tysta överskrivning, fast genom
UI:t.

Bygger du något som hämtar från Cloud: **använd `stage_pending`, inte
`install`.**

### 4. Endast verkliga API:er

Skärmbildernas "Cloud rev 12 · 499 rörelser · 3 ändrade" är prototypdata. Ett
test faller om de strängarna dyker upp i källan. Stegräckans underrubriker
kommer ur `/v1/runtime` och `/v1/devices`, och samma svar föder både listan
och räckan så de inte kan visa olika tal.

### 5. Inga inline-stilar i HTML-strängar

Serverns CSP är `style-src 'self'`. Ett `style="width:N%"` i en `innerHTML`-
sträng avvisas tyst. Bygg med DOM-anrop och sätt värdet via CSSOM
(`element.style.width`), som inte omfattas. Två tester håller det shut.

### 6. Aktiva träffen rörs inte

Byggläget ändrar ingenting förrän en revision uttryckligen granskas och
aktiveras. Aktiveringsvägen finns redan i API:t sedan 1.2.0
(`+local-rN` via `SQLiteRuntimeStore.install`), men **är inte kopplad till
UI:t**. Koppla den — hitta inte på en ny.

### 7. Sju uppdateringssteg, ett kontrakt

Stegen och etiketterna kommer ur `update_contract.py`, som är kopierad ordagrant
till `trainmeet-cloud`. Webbläsaren ritar dem och översätter tillståndet till
ett ord (`Klar` / `Pågår` / `Väntar` / `Fel`) — den hittar inte på ett steg, en
etikett eller ett femte tillstånd. Ett test faller om en etikett dyker upp som
sträng i `app.js`.

### 8. Nollställningen är två olika saker

`access_mode: local` ⇒ fabriksåterställning, administratören raderas.
`access_mode: external` ⇒ bara träffdata. Sammanfattningen på det hopfällda
`<details>` är det enda som syns innan man öppnar, så den måste säga vilken av
dem det blir. Skriv inte paketets fasta "Nollställ träffdata" där.

### 9. Knapparnas form är scopad med flit

`.server-step-card button` bär paketets form. Den globala `button`-regeln är
kvar som den var, och det är inte slarv: den slår igenom på varje KÖR-vy och
på byggsteg 1, som redan står 👁 i checklistan. Byter man den globalt måste
allt det verifieras om i webbläsaren, och det är ett eget block.

Lägger du till ett kort i steg 5: **ge det klassen `server-step-card`**, annars
får dess knappar pillerformen. Ett test räknar att exakt fyra kort bär den.

### 10. Den destruktiva knappen är röd med flit

Paketets adminpalett innehåller ingen röd, eftersom nollställningen ligger
hopfälld i varenda skärmbild och knappen därför aldrig syns. Att ge den
accentfärgen skulle göra *Installera och starta om* och *Fabriksåterställ
servern* till samma knapp. Formen följer paketet; färgen gör det inte. Ett test
faller om den byts till accentfärgen.

### 11. Paketet styr utseendet

Vid konflikt med `ADMIN-UI-CONTRACT.md` eller `GRAPHIC_IDENTITY.md` vinner
paketet, och dokumentet uppdateras. Det har redan hänt en gång (blå → orange,
16px → 12/8px, 48px → 56px) och står i checklistans **Avvikelser**.

---

## Test

```bash
cd trainmeet-server
pip install paho-mqtt          # annars faller 4 MQTT-moduler på importfel
apt-get install -y mosquitto   # annars hoppas 2 tester över, se nedan
PYTHONPATH=src:tests python3 -m unittest discover -s tests -q               # 394, OK
PYTHONPATH=src:tests python3 -m unittest tests.test_kor_bygg_structure -q   # 43
PYTHONPATH=src:tests python3 -m unittest tests.test_operating_modes -q      # 13
```

Senaste körning: **394 gröna, inga överhoppade.**

| Modul | Antal |
|---|---:|
| basen `7cec157` | 315 |
| `test_build_topology` | 33 |
| `test_pending_revisions` (T4) | 17 |
| `test_area_gate` (T3 + genvägen) | 21 |
| `test_product_boundaries` | 6 |
| netto omskrivna i befintliga moduler | +2 |

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

### Mät layouten, titta inte på den

Ett fel nådde produktion som varken markup- eller JavaScript-tester kunde se:
hela KÖR ritades i vänstra fjärdedelen av ett brett fönster. Skalet ärvde en
tvåkolumnsgrid från de tolv menypunkterna, byggsidofältet var `display: none`
men **spåret fanns kvar**, och arbetsytan hamnade i det.

En skärmbild avslöjar det bara om man råkar titta på en tillräckligt bred
skärm. Måtten avslöjar det alltid:

```js
const el = document.querySelector(".server-workspace");
const shell = document.querySelector(".server-admin-shell");
console.log(el.getBoundingClientRect().width, shell.getBoundingClientRect().width);
// Arbetsytan ska fylla skalet minus dess padding. Är den en bråkdel ligger
// den i fel grid-spår.
```

Granskningsmönstret som hittade resten: gå igenom alla fem körflikar och alla
fem byggsteg på **924, 1100, 1440 och 1850 px**, och rapportera varje element
vars högerkant ligger utanför en förälder som inte scrollar, varje kryssruta
bredare än 30 px, och varje sida vars `scrollWidth` överstiger fönstret.

Skriptet ligger inte i repot, eftersom CI inte har någon webbläsare. Testerna
i `tests/test_shell_layout.py` vaktar därför *reglerna* som gör måtten rätt,
inte måtten själva — och det står i testets egen docstring.

**Ett känt falskt positivt:** `#app-chrome.topbar` sticker ut ur sin förälder
med flit (`margin: 0 calc(50% - 50vw)`) för att spänna hela bredden.

Kör aldrig `playwright install` — `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
npm install playwright` räcker, binären finns redan.

---

## Vad som är byggt — block 4: BYGG steg 2 i låst läge

`/v1/build/topology` svarar med stationer, sträckor och paneler i **samma form**
vare sig innehållet kommer från en Cloud-publicering eller ett lokalt utkast.
Skillnaden är ett `locked` i svaret, inte två kodvägar och inte en gissning i
webbläsaren.

Tre saker som inte är uppenbara och som kostade tid att hitta:

1. **En A–D-plats pekar på en sträcka, inte på en granne.** Grannens namn är
   sträckans andra ände sett från panelens station. Ett uppslag som behandlar
   platsen som ett stations-id renderar utan att krascha — det visar bara ett
   id där ett namn ska stå.
2. **Stationsordningen ligger i `diagram_order`, inte i listordningen.**
   Publiceringen får sorteras; utkastet får inte, för där *är* listan ordningen.
3. **`.build-panel-heading` delas med steg 1 och 5**, vars rubriker har `h2`
   och `p` som direkta barn. Att göra den till flex för att få märket till
   höger ställer dem bredvid varandra i de andra stegen. Därför
   `.with-badge`, inte en omodifierad regel.

Kvar i steget: redigeringsläget (fälten är ännu inte inmatningsbara),
genvägen *Bygg från stationsordningen*, och sådd-kopplingen —
`/v1/local-configuration/seed` finns men anropas inte av någon vy, så den
lokala vägen har ännu inget att redigera.

---

## Vad som är byggt — block 5: T4, T3 och steg 2 färdigt

**T4 — ingen tyst Cloud-aktivering.** Pollern stagear i stället för att
aktivera; `/v1/runtime/pending` visar vad som väntar och en diff av vad ett ja
skulle ersätta; `/v1/runtime/pending/activate` kräver revisionens id. Hela
slingan körd i webbläsare mot en riktig väntande revision: kortet visade
"Omdöpta: Lekeberg → Lekeberg norra" och "Ändrade tider: tåg 101", aktiveringen
gick igenom och kortet försvann.

**T3 — områdesvis grind.** Banan låst i Cloud-läge, tidtabellen alltid öppen.
Den globala grinden borttagen.

**Steg 2 färdigt.** Redigerbart läge med riktiga fält, genvägen *Bygg från
stationsordningen* kopplad till en idempotent serverhärledning, och
sådd-kopplingen på plats.

Tre fällor som kostade tid:

1. **Servern avvisar tom kropp på POST.** En åtgärd utan argument måste skicka
   `{}` — annars `invalid_body`, och knappen ser trasig ut utan att något syns
   i loggen.
2. **`width: 100%` på ett flexbarn framtvingar den radbrytning det skulle
   förhindra.** Ett `<input>` bär en inbyggd minimibredd på ~20 tecken som ett
   `<div>` inte gör; rätt botemedel är `min-width: 0` och `flex-basis: 0`.
3. **Kvittensen skrevs och raderades i samma andetag** — omritningen nollar
   meddelanderaden, så "Sparat" måste sättas *efter* den.

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
