# Definition of Done — designpaketet

Varje bindande krav i `design_handoff_trainmeet_server` mappat till status.
Uppdateras efter varje arbetsblock, så att ingenting tappas mellan omgångar.

| Status | Betyder |
|---|---|
| ⬜ | ej påbörjat |
| 🔨 | implementerat, inte testat |
| ✅ | implementerat och testat |
| 👁 | dessutom visuellt verifierat i webbläsare mot paketet |
| ⛔ | verkligt blockerat — orsak angiven |

**Läge 2026-08-22:** ⬜ 59 · 🔨 26 · ✅ 19 · 👁 34 · ⛔ 2

Uppdatera den här raden när du uppdaterar tabellerna, så att en snabb blick
räcker för att se om arbetet rör sig.

Paketet styr utseende, struktur och användarflöde. Befintliga API-, säkerhets-
och datakontrakt bevaras. Konflikter dokumenteras i **Avvikelser** sist.

---

## DEL 2 — Strukturen

| # | Krav | Status | Var |
|---|---|---|---|
| 2.1 | KÖR är default; inget där ändrar konfigurationen | 👁 | `app.js` `setMode` |
| 2.2 | KÖR har fem flikar: Översikt, Trafik, Skärmar, TKL, TMBox v2 | 👁 | `index.html` `#run-tabs` |
| 2.3 | BYGG nås via *Bygg om träffen* uppe till höger | 👁 | `#enter-build` |
| 2.4 | BYGG har fem numrerade steg | 👁 | `#build-sidebar` |
| 2.5 | Mörkt topplock i byggläget med förklarande mening | 👁 | `#build-chrome` |
| 2.6 | Osparad-ändringar-panel med *Granska och aktivera* / *Spara utkast* | 🔨 | `#unsaved-panel` |
| 2.7 | Aktiva träffen kör oförändrad tills en revision aktiveras | ⬜ | |

### Skärmkartan — gammalt till nytt

| Dagens menypunkt | Ny plats | Status |
|---|---|---|
| Översikt | KÖR › Översikt (omgjord) | ⬜ |
| Aktiv träff | KÖR › Trafik | 👁 |
| Cloud och synk | BYGG › 1, källa = Cloud | 👁 |
| Lokala ändringar | BYGG › 1, källa = Lokalt utkast | 👁 |
| Manuell import | BYGG › 1, källa = Importerad fil | ⬜ kortet scrollar bara till den gamla panelen |
| TMBoxar | BYGG › 4 | 🔨 |
| TMBox-simulering | **Borttagen** | ✅ |
| TMBox v2 | KÖR › TMBox v2 (oförändrad) | 👁 |
| TKL-terminal | KÖR › TKL (inbäddad) | 🔨 |
| Skärmar och klocka | KÖR › Skärmar | 🔨 |
| Användare och åtkomst | BYGG › 5 | 👁 |
| Programuppdatering | BYGG › 5 | 👁 |
| Server och nollställning | BYGG › 5 | 👁 |

---

## DEL 3 — Skärm för skärm

### 3.1 Applocket

| # | Krav | Status |
|---|---|---|
| 3.1.1 | Mörk list 50px, `#1f1e1d`, padding 0 22px, bara i byggläget | 👁 |
| 3.1.2 | Vit rad 56px, botten-border 1px `#e8e5dc` | 👁 |
| 3.1.3 | Logotypmärke 28×28 + träffnamn 16px/600 | ✅ |
| 3.1.4 | Flikrad i mitten, bara i körläget | 👁 |
| 3.1.5 | Träffklocka monospace 15px/600 `#4b7a4f` | 🔨 |
| 3.1.6 | "● N boxar online" 12.5px `#706c61` | 🔨 |
| 3.1.7 | Flik: min-height 30px, padding 0 11px, radie 8px | 👁 |
| 3.1.8 | Aktiv flik `#1f1e1d`/vit, inaktiv transparent `#706c61` | 👁 |

### 3.2 KÖR › Översikt

| # | Krav | Status |
|---|---|---|
| 3.2.1 | "Banan just nu"-kort med banschema (`renderTopology`) | ⬜ |
| 3.2.2 | Rubrikrad med "N stationer · N sträckor · N tåg ute" | ⬜ |
| 3.2.3 | Textknapp *Öppna på storbild ⛶* | ⬜ |
| 3.2.4 | "Nästa rörelser" — tid monospace, händelse, status | ⬜ |
| 3.2.5 | Aktuell rad `background: #faf9f5` | ⬜ |
| 3.2.6 | "Enheter" — stationskod, panel, boxkod, online | ⬜ |
| 3.2.7 | Sektion 2–3 i `repeat(auto-fit, minmax(290px, 1fr))` | ⬜ |

### 3.3 KÖR › Trafik

| # | Krav | Status |
|---|---|---|
| 3.3.1 | Stationsfilter, default "Hela banan" | 👁 |
| 3.3.2 | Kryssruta *Bara avvikelser* | 👁 |
| 3.3.3 | "På linjen just nu" — kort per tåg mellan stationer | 👁 |
| 3.3.4 | Tågnummer monospace stort, sträcka, progressbar med lok | 🔨 (lokmarkör saknas) |
| 3.3.5 | Avvikelse som chip: "I tid" grönt / "+N min" rött | 👁 |
| 3.3.6 | Rutnät `repeat(auto-fit, minmax(300px, 1fr))` | 👁 |
| 3.3.7 | "Inne på stationerna" — spår, tåg, tider, status ur motorn | 🔨 (spår saknas) |
| 3.3.8 | Statusar REDO/VÄNTAR SVAR/INNE/AVGÅTT/EJ KLART | ⬜ |
| 3.3.9 | Det som kräver en människa pulserar, inget annat | 👁 |
| 3.3.10 | Tidslinje med nu-linje som följer träffklockan | 🔨 (nu-linje saknas) |
| 3.3.11 | Avklarat tonas ner, nästa två med grön prick | 👁 |

### 3.4 KÖR › Skärmar

| # | Krav | Status |
|---|---|---|
| 3.4.1 | Lista över publika vyer med *Öppna på storbild* | 🔨 |
| 3.4.2 | Skärmens adress | 🔨 |
| 3.4.3 | QR-kod-platshållare | ⬜ |

### 3.5 KÖR › TKL-terminal

| # | Krav | Status |
|---|---|---|
| 3.5.1 | Terminalen inbäddad i iframe | 🔨 |
| 3.5.2 | Stationsväljare ovanför | ✅ (fylls ur /v1/devices) |
| 3.5.3 | Knapp *Öppna i egen flik* | 🔨 |

### 3.6 KÖR › TMBox v2

| # | Krav | Status |
|---|---|---|
| 3.6.1 | Oförändrad mot dagens vy | 👁 |

### 3.7 BYGG › 1 Träffen

| # | Krav | Status |
|---|---|---|
| 3.7.1 | Tre källkort med radioknapp i auto-fit-rutnät | 👁 |
| 3.7.2 | Valt kort: kant `#c96442`, botten `#fdf6f3`, ifylld bock | 👁 uppmätt |
| 3.7.3 | Ovalt kort: kant `#e8e5dc`, botten `#fff`, tom cirkel | 👁 |
| 3.7.4 | **Valet styr faktisk låsning av steg 2 och 3** | 🔨 mekanismen verifierad, men steg 2–3 finns inte att låsa ännu |
| 3.7.5 | Cloud: revisionsrad med chip *Aktiv* | ⬜ |
| 3.7.6 | Cloud: fyrstegs statuslista | ⬜ |
| 3.7.7 | Lokalt: grunddataformulär i `minmax(150px, 1fr)` | ⬜ |
| 3.7.8 | Lokalt: informationsruta om enkelriktat flöde | ⬜ |
| 3.7.9 | Importerad fil: filväljare + valideringsresultat | ⬜ |

### 3.8 BYGG › 2 Stationer och sträckor

| # | Krav | Status |
|---|---|---|
| 3.8.1 | Tre numrerade sektioner i **ett** kort | ⬜ |
| 3.8.2 | Stationer: ordningsnummer i cirkel, signatur monospace 76px | ⬜ |
| 3.8.3 | Härledd länkbeskrivning per station | ⬜ |
| 3.8.4 | Sträckor: från, till, spårtyp, trafikeringsregel | ⬜ |
| 3.8.5 | Paneler: station, namn, fyra slot-chips A–D | ⬜ |
| 3.8.6 | **Genväg: *Bygg från stationsordningen*, idempotent** | ⬜ |
| 3.8.7 | Låst av Cloud: `#f7f5f0`/`#8a857a`, märke "🔒 Låst av Cloud" | ⬜ |
| 3.8.8 | Lokal: vita fält, märke "✎ Redigerbar" `#a44f33` | ⬜ |

### 3.9 BYGG › 3 Tidtabell

| # | Krav | Status |
|---|---|---|
| 3.9.1 | Segmenterad kontroll: Tid / Station / Tåg | ⬜ |
| 3.9.2 | **Grupperingen ändrar vyn, inte datan** | ⬜ |
| 3.9.3 | Stationsfilter och sökruta | ⬜ |
| 3.9.4 | Ändrade rader: orange kantmarkering + chip *Ändrad* | ⬜ |
| 3.9.5 | **Massredigering av markerade rader** | ⬜ |
| 3.9.6 | Kolumner enligt paketet | ⬜ |

### 3.10 BYGG › 4 TMBoxar

| # | Krav | Status |
|---|---|---|
| 3.10.1 | Kopplingsformulär: kod, station, panel, *Koppla* | ⬜ |
| 3.10.2 | Lista med boxkod monospace, station, panel, status | ⬜ |
| 3.10.3 | Förklarande underrubrik | ⬜ |

### 3.11 BYGG › 5 Server

| # | Krav | Status |
|---|---|---|
| 3.11.1 | Identitet: tre statusrutor + servernamn | 👁 |
| 3.11.2 | Extern admininloggning i tre kolumner + chip | 👁 |
| 3.11.3 | Programuppdatering: versionsrad + **sju steg** | 👁 |
| 3.11.4 | Nollställ i hopfällt `<details>`, kräver NOLLSTÄLL | 👁 |

Alla fyra är uppmätta i Chromium vid 924px mot `bygg-09-server.png` och
`bygg-10-server-uppdatering.png`. Bevisen står under **Webbläsarbevis** nedan.
Kvarvarande avvikelse mot bilderna är knappformen, se **Avvikelser 5**.

---

## DEL 4 — Hallskärmarna

| # | Krav | Status |
|---|---|---|
| 4.1 | Träffklocka, STOPPAD-platta halvgenomskinlig i samma SVG | ⬜ |
| 4.2 | Banöversikt, dubbelspår ljusare, belagt `#ff4d4f` | ⬜ |
| 4.3 | Avgångstavla: AVG · DRIFTPLATS · SPÅR · MOT · TÅG | ⬜ |
| 4.4 | Nu-tavlan: PÅ LINJEN / VÄNTAR, färgat kantband | ⬜ |
| 4.5 | Tågdiagram med tågnummer längs linjerna | ⬜ |
| 4.6 | Gemensam topprad och fot | ⬜ |
| 4.7 | **Degraderat: tona ner bilden** | ⬜ |
| 4.8 | **Degraderat: ta bort tågen helt** | ⬜ |
| 4.9 | **Degraderat: gul list med senaste kontakt** | ⬜ |
| 4.10 | **Trösklarna konfigurerbara** (30 s / 2 min) | ⬜ |
| 4.11 | Verifierade i 1920×1080 | ⬜ |

---

## DEL 5 — Interaktion och tillstånd

| # | Krav | Status |
|---|---|---|
| 5.1 | `mode`, `korTab`, `byggStep` | ✅ |
| 5.2 | `source` styr låsning | ✅ |
| 5.3 | `ttMode`, `ttStation`, `ttSelected` | ⬜ |
| 5.4 | `trafikStation` | ✅ |
| 5.5 | `locked = source === "cloud"` | 🔨 härledd ur serversvaret; ingen konsument ännu |
| 5.6 | Osparade ändringar räknas ur diffen mot aktiv revision | ⬜ |
| 5.7 | Aktivering skapar `rev N+local-rN` | ⬜ (finns i API, ej i UI) |
| 5.8 | *Granska och aktivera* visar diff och kräver bekräftelse | ⬜ |
| 5.9 | *Lämna byggläget* — osparat ligger kvar som utkast | ⬜ |
| 5.10 | Byte lokal → Cloud varnar om kastade revisioner | 🔨 kodvägen finns, **aldrig körd** — inga lokala revisioner i testsessionen |

---

## DEL 6 — Designtokens

| # | Krav | Status |
|---|---|---|
| 6.1 | Admin-paletten exakt | ✅ |
| 6.2 | Skärm-paletten (mörkt) | ⬜ |
| 6.3 | Monospace för tider, tågnummer, koder, IP | ✅ |
| 6.4 | Admin-skalor 11/12/12.5–13/13.5/14.5–15/16px | 🔨 steg 5 följer dem; `.revision-badge` står kvar på 9px i övriga vyer |
| 6.5 | Skärm-skalor 24/26/28–30/34/42–50px | ⬜ |
| 6.6 | Radier 8/10/12/999px | 🔨 **rättad status.** Kort och fält stämmer, men `button` har `border-radius: 999px` i hela adminen. Paketet säger 8px för knappar och 999px bara för chip. Se **Avvikelser 5** |
| 6.7 | Fälthöjd 34/36px, knapphöjd 30/32–36px | 🔨 höjderna stämmer i steg 5; knappens *yta* gör det inte, se **Avvikelser 5** |
| 6.8 | Gap 2/8–12/16/24px | 🔨 |
| 6.9 | Sidopanel 236px, sticky top 106px | ✅ |

---

## Kvalitetskrav

| # | Krav | Status |
|---|---|---|
| K1 | Endast verkliga API:er, ingen prototypdata | ✅ hittills |
| K2 | Tester körda före och efter varje block | ✅ |
| K3 | Test: KÖR/BYGG | ✅ |
| K4 | Test: källåsning | 🔨 statiska tester av kopplingen; inget test kör själva låsningen i UI |
| K5 | Test: lokal revision och aktivering | ✅ (`test_local_revisions`) |
| K6 | Test: TKL-iframe | ⬜ |
| K6b | Test: BYGG 5 Server | ✅ 17 tester i `test_kor_bygg_structure.BuildStepFiveServerTests`, mutationsprovade |
| K7 | Test: tidtabellsgruppering | ⬜ |
| K8 | Test: degraderade skärmar | ⬜ |
| K9 | Visuella regressionstester | ⬜ |
| K10 | `prefers-reduced-motion` | 👁 mätt i Chromium med `reducedMotion: "reduce"`: uppdateringsstegets markör får `animation-name: none` |
| K11 | Tangentbord, fokus, etiketter, kontrast | 🔨 **steg 5 är gjort och mätt** (tabbordning, 2px fokusring, `<summary>` öppnas med Enter, alla fem fält har etikett). Övriga vyer är inte genomgångna, och kontrast är inte mätt någonstans |
| K12 | Konsol fri från JS-, nät- och CSP-fel | 👁 steg 5 och alla fem körflikar körda: inga JS-, CSP- eller nätfel. Ett kvarstående 404 finns, se **Avvikelser 6** |
| K13 | Inga hemligheter eller exempeldata incheckade | ✅ |
| K14 | Dokumentation och versionsanteckningar | 🔨 |
| K15 | Minst minor-version | 🔨 (`[minor]` i commit) |
| K16 | Bildjämförelser vid 924px och desktop | 🔨 **steg 5 jämfört** vid 924, 1440 och 390px mot paketets två bilder. Övriga skärmar är fortfarande ojämförda |

---

## Definition of Done

| # | Krav | Status |
|---|---|---|
| D1 | Varje målskärm har en fungerande motsvarighet | ⬜ |
| D2 | Checklistan visar varje krav | ✅ (den här filen) |
| D3 | Bildjämförelser mot målbilden | ⬜ |
| D4 | Alla gamla funktioner flyttade enligt kartan | 🔨 nio av tolv menypunkter flyttade; kvar: Översikt, Manuell import, samt TMBoxar/Skärmar som är flyttade men inte omformade |
| D5 | Alla nya flöden använder verkliga API | ✅ hittills |
| D6 | Tester och produktionsbyggen gröna | ✅ |
| D7 | PR granskad och mergad | ⬜ |
| D8 | Säkerhetskopia och rollback dokumenterad | ✅ (finns sedan 1.2.0) |
| D9 | Driftsatt på server.trainmeet.app | ⛔ **utanför min åtkomst** |
| D10 | Driftkontroll i produktion | ⛔ **utanför min åtkomst** |

---

## Vad som uttryckligen **inte** är verifierat

Sanningshalten i checklistan är viktigare än hur långt den ser ut att ha
kommit. Följande är byggt men obevisat, och nästa session ska inte lita på
det:

| Sak | Läge |
|---|---|
| Kassationssammanfattningen vid återgång till Cloud | Koden finns i `confirmDiscardAndSwitch`. Den kördes **aldrig** — testsessionen hade inga lokala revisioner att kasta. Servervägen är däremot testad (`test_operating_modes.py`, 13 tester). |
| "Importerad fil" som källa | Kortet scrollar bara till den gamla importpanelen. Inget eget flöde, ingen validering, inget resultat. |
| Källvalets beständighet över omladdning | Aldrig provat. Läget läses ur `/v1/operating-mode` vid varje besök i steg 1, så det *bör* hålla, men "bör" är inte verifierat. |
| Låsningen som faktisk effekt | `data-source-locked` sätts och CSS:en finns, men steg 2 och 3 existerar inte ännu, så ingenting låses i praktiken. |
| Osparade ändringar | Panelen finns i markup med statisk text. Ingen diff räknas, knapparna gör ingenting. |
| Hela uppdateringsförloppet | De sju stegen är ritade i alla fyra tillstånd, men bara med serverns svar som indata. En **riktig** uppdatering är inte körd härifrån: den kräver en installerad server med `update_backend`, och utvecklingskatalogen har ingen. `build`-id:t är därför tomt lokalt och versionsraden faller tillbaka på enbart `1.2.0`. |
| Nollställningen som faktisk åtgärd | Spärren är provad på riktigt i webbläsaren - fel ord låser, NOLLSTÄLL låser upp - men knappen är **aldrig tryckt**. Servervägen är testad sedan tidigare (`/v1/server/factory-reset` i `test_http_server.py`). |
| Extern admininloggning som inloggning | Formuläret sparar mot `/v1/admin/access`, men ingen inloggning utifrån är gjord: allt är kört från 127.0.0.1, där servern rapporterar `access_mode: local`. Chippets externa variant och `#reset-mode-summary`:s "Nollställ träffdata" är alltså sedda i koden, inte på skärmen. |

## Webbläsarbevis — BYGG 5 Server

Kört i Chromium 1194 via Playwright mot en färdiginstallerad server (riktig
`TrainMeetHTTPApplication`, riktiga CSP-headers, riktiga API:er). Skriptet
ligger i sessionens arbetskatalog och gör 76 kontroller; alla passerar.

Så här startas servern utan Mosquitto, som utvecklingsmiljön saknar: bygg en
`TrainMeetHTTPApplication` direkt, som `tests/test_http_server.py` gör, och kör
sedan installationsflödet i `DESIGNPAKET-HANDOFF.md`.

| Vad | Bevis |
|---|---|
| Fyra block, paketets ordning | DOM-ordningen lästs ur den renderade sidan: `identity, access, software, system` |
| Bara steg 5:s paneler syns | Cloud- och enhetskorten dolda; steg 1-panelen dold |
| Statusrutor ur verklig data | `SERVER: Grimslöv driftserver`, `AKTIV TRÄFF: Sommarträffen`, `CLOUD: Inte kopplad` — ur `/v1/info` |
| Servernamnet sparas på riktigt | `POST /v1/setup/server` → "Servernamnet är nu Grimslöv driftserver 2." och statusrutan följde med |
| Tre kolumner | `grid-template-columns` gav 3 spår vid 924px |
| Chippet | "Lokal åtkomst" — ur `/v1/auth/status`, inte en fast sträng |
| Inloggningsfältet tomt | `#login-username` = `""` efter full sidladdning |
| Sju steg | Etiketterna och ordningen matchar `update_contract.STAGES` exakt |
| Tillstånden | `Klar / Klar / Klar / Pågår / Väntar / Väntar / Väntar`; pågående markör `rgb(201, 100, 66)` = `#c96442`, rad `#fdf6f3` |
| Versionsraden | `1.2.0` lokalt (build-id tomt utanför en installation); med build-id: `1.2.0 · build 4bd9c9a`, och stegräckan visar samma sträng |
| Nollställningen | Hopfälld vid inladdning; kant `rgb(230, 207, 199)` = `#e6cfc7`, botten `rgb(253, 246, 243)` = `#fdf6f3` |
| NOLLSTÄLL-spärren | Knappen låst från start, låst efter fel ord, upplåst efter NOLLSTÄLL |
| Tangentbord | Tab: användarnamn → nytt lösenord → upprepa. Enter på `<summary>` fäller ut |
| Fokus | 2px synlig fokusring på servernamnsfältet |
| Etiketter | Alla fem fält har `<label for>` eller omslutande `<label>` |
| Mått vid 924px | Topplock 50px, sidopanel 236px, uppdateringssteg 36px |
| Ingen vågrät scroll | 924px, 1440px och 390px |
| `prefers-reduced-motion` | `animation-name: none` på pågående stegs markör |
| Konsol och CSP | Inga JS-, CSP- eller nätfel i steg 5. Ett 404, se **Avvikelser 6** |
| Inget annat gick sönder | Alla fem byggsteg och alla fem körflikar öppnade; inga adminpaneler läcker in i körläget |

### Tester

```
PYTHONPATH=src:tests python3 -m unittest discover -s tests -q
```

| | Före blocket | Efter |
|---|---|---|
| Antal | 291 | 308 |
| Utfall | OK (1 överhoppad) | OK (1 överhoppad) |

De 17 nya ligger i `tests/test_kor_bygg_structure.BuildStepFiveServerTests`.
De är mutationsprovade: en ändrad statusruteetikett, hårdkodade
uppdateringssteg i `app.js` och ett `<details open>` fäller var sitt test.

`paho-mqtt` måste installeras (`pip install paho-mqtt`), annars hoppas fyra
MQTT-moduler över med importfel i stället för att köras.

---

## Avvikelser och konflikter

### 1. Accentfärg, radie och topprad — paketet mot repots egna dokument

`docs/ADMIN-UI-CONTRACT.md` och `docs/GRAPHIC_IDENTITY.md` angav blå
`hsl(220 70% 45%)` som primärfärg, med den uttryckliga meningen *"amber/orange
används inte som knappfärg"*, 16px radie och en 48px topprad med blur.

Paketet anger `#c96442` som primärknapp, 12/8px radier och en 56px solid rad.

**Löst enligt uppdragets regel 1**: paketet styr utseendet. Båda dokumenten är
uppdaterade så att de inte längre säger emot produkten.

### 2. Typsnitt

Paketet säger "Inga typsnittsfiler behövs — allt är systemstackar".
`GRAPHIC_IDENTITY.md` kräver Inter. Servern paketerar sedan 1.2.0 Inter lokalt
(issue #32) för att slippa CSP-fel mot Google Fonts.

**Ingen verklig konflikt**: paketet säger att filer inte *behövs*, inte att de
är förbjudna. Lokalt paketerad Inter uppfyller båda, och kravet "inga externa
typsnitt eller CSP-fel" i uppdraget.

### 3. CSP-fel som redan låg på main

`renderStationCounts` byggde HTML med `style="width:N%"` genom `innerHTML`.
Det är ett inline-attribut och serverns egen CSP (`style-src 'self'`) avvisar
det — så staplarna i översikten har **aldrig** fått sin bredd, och felet låg
tyst i konsolen.

Inte infört av det här arbetet, men uppdraget kräver en ren konsol, så det är
lagat: elementen byggs med DOM-anrop och bredden sätts via CSSOM, som inte
omfattas av `style-src`. Ett test håller fast det.

### 5. Knapparnas form och färg — hittat, **inte lagat**

Paketets DEL 6 säger `Radie: 8px fält och knappar` och `Kantlinje #e0dcd1 |
fält, knappar`. Pixlarna i `bygg-09-server.png` bekräftar det: *Spara
servernamn* är vit `#ffffff` med en kant runt `#dcdcdc`, i en rundad rektangel.

Repot har `button { border-radius: 999px }` och `button.secondary { color:
var(--trainmeet); background: var(--trainmeet-soft) }` — piller i accentton.

Det är en **verklig avvikelse mot paketet**, och den är inte lagad här. Skälet
är avgränsning, inte oenighet: `button` är global och slår igenom på varje vy,
även de KÖR-vyer som redan står som 👁 i den här listan. Att ändra den inne i
ett BYGG 5-block skulle antingen ge två knappformer i samma produkt, eller
tvinga fram en ny visuell genomgång av allt som redan är verifierat.

Rekommenderas som **eget block före nästa skärmarbete**, eftersom varje block
som byggs innan dess ärver fel knappform:

```css
button { border: 1px solid var(--line-field); border-radius: var(--radius-sm); }
button.secondary { color: var(--ink-strong); background: var(--surface-raised); }
```

Efter den ändringen måste KÖR-vyerna och steg 1 verifieras om.

### 6. 404 på `/terminal/config` — förbefintligt, inte det här blockets

Körfliken TKL bäddar in `/tkl/`, och paketets TKL-bygge frågar efter
`/terminal/config`, som den här servern inte serverar. Det ger ett 404 i
konsolen så snart TKL-fliken öppnas.

Kontrollerat att det inte kommer härifrån: felet reproduceras oförändrat med
det här blockets ändringar `git stash`:ade. Hör till 3.5.1, som står 🔨.

### 7. Nollställningens rubrik säger inte alltid "Nollställ träffdata"

Paketets bild visar sammanfattningen *Nollställ träffdata*. Servern har två
olika nollställningar, och vilken det blir avgörs av `access_mode`:

| Åtkomst | Vad knappen gör | Sammanfattningen säger |
|---|---|---|
| lokal | fabriksåterställning — även administratören raderas | Fabriksåterställ servern |
| extern | bara träffdata; administratören behålls | Nollställ träffdata |

Paketets text är alltså den externa varianten. Att skriva den även lokalt vore
att dölja att administratörskontot försvinner. Verklig data vinner över
bildens etikett; layouten, kanten och bottnen följer paketet exakt.

### 8. Två borttagna texter i steg 5

Två strängar följde inte med flytten, båda utan funktion:

- `Serveridentitet` / *"Namnet visas lokalt och när servern kopplas till
  Cloud."* — en underrubrik i servernamnsformuläret. Paketets identitetsblock
  har ingen; rutan `SERVER` ovanför säger samma sak.
- Chippet `Servern kör` i "Server och nollställning". Statisk markup som ingen
  kod någonsin skrev om — det stod "Servern kör" även om den inte gjorde det.

Kvarvarande information ur den gamla `#access-mode`-texten
("Extern inloggning klar" / "Lösenord saknas") är **inte** borttagen: den står
nu på egen rad som `#access-password-state`, eftersom chippet enligt paketet
bär åtkomstläget.

### 9. D9 och D10 — driftsättning

Proxyn i utvecklingsmiljön blockerar `trainmeet.app`, och det finns ingen
SSH-åtkomst till Raspberry Pi:n. Driftsättning och driftkontroll kan inte
utföras härifrån. Exakta kommandon och kontrollista lämnas i slutrapporten.
