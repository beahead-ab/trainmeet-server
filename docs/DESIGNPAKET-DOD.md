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

**Läge 2026-08-22:** ⬜ 49 · 🔨 12 · ✅ 19 · 👁 41 · ⛔ 0

Uppdatera den här raden när du uppdaterar tabellerna, så att en snabb blick
räcker för att se om arbetet rör sig. Räkna den, gissa den inte — den var
handhållen fram till 2026-08-22 och hade hunnit driva isär från tabellerna:

```bash
python3 - <<'EOF'
import pathlib
c = {m: 0 for m in "⬜🔨✅👁⛔"}
for line in pathlib.Path("docs/DESIGNPAKET-DOD.md").read_text().splitlines():
    if line.startswith("|"):
        for cell in [x.strip() for x in line.strip("|").split("|")][2:]:
            if cell in c:
                c[cell] += 1
print(" · ".join(f"{k} {v}" for k, v in c.items()))
EOF
```

Paketet styr utseende, struktur och användarflöde. Befintliga API-, säkerhets-
och datakontrakt bevaras. Konflikter dokumenteras i **Avvikelser** sist.

---

## Produktbeslut — bindande, över paketet

Paketet beskriver hur servern ska *se ut*. De här besluten beskriver vad den
**är**, och de går före paketet om de någonsin krockar. De är inte förslag och
de ska inte omtolkas av en senare session.

| # | Beslut | Status | Bevis |
|---|---|---|---|
| P1 | PDF, JPG och PNG tolkas **endast** i TrainMeet Cloud | ✅ | `test_product_boundaries.NoDocumentInterpretationTests` — faller om något tolkningsbibliotek importeras i servern |
| P2 | Cloud publicerar grundkonfigurationen | ✅ | `RuntimePublication.parse` är enda vägen in; `test_the_manual_import_accepts_a_json_operating_package` |
| P3 | Synk går **endast** Cloud → Server | ✅ | `test_every_request_to_cloud_is_a_read` — varje `Request` mot Cloud är en GET utan `data` |
| P4 | Ändringar på servern skickas **aldrig** upp till Cloud | ✅ | samma test, plus `test_area_gate.test_nothing_from_the_server_is_sent_to_cloud_by_editing` som kontrollerar det där arbetet faktiskt produceras |
| P5 | BYGG steg 2 skrivskyddat i Cloud-läge, redigerbart lokalt | 👁 | `test_build_topology` (33 tester), `test_area_gate` (21). Båda lägena verifierade i webbläsare vid 1280 och 924 px |
| P6 | BYGG steg 3 **alltid** lokalt redigerbart, även på Cloud-revision | ✅ | `test_area_gate.AreaGateTests` — tid ändrad och rörelse struken i Cloud-läge; `test_operating_modes.test_cloud_linked_still_lets_the_timetable_be_corrected` |
| P7 | Lokala tidtabellsändringar blir lokala runtime-revisioner | ✅ | `test_a_corrected_timetable_becomes_a_local_revision`, `test_the_local_revision_is_built_on_the_cloud_publication` |
| P8 | En ny Cloud-revision aktiveras **aldrig** tyst över lokala ändringar | 👁 | `test_pending_revisions` (17 tester); hela slingan körd i webbläsare mot en riktig väntande revision |
| P9 | Serverns manuella filimport är ett exporterat JSON-driftpaket | ✅ | `test_the_manual_import_accepts_a_json_operating_package` |

### ✅ T3 — grinden är områdesvis

Den gamla `_require_editing_open()` stängde *varje* skrivväg i `cloud-linked`,
tidtabellen inkluderad. Det gjorde två olika saker omöjliga att skilja åt: att
rätta en avgångstid vägrades av samma skäl som att rita om banan.

Grinden heter nu `_require_topology_unchanged()` och gäller bara
`TOPOLOGY_SECTIONS = ("stations", "connections", "panels")`. Tidtabellen är
öppen även i Cloud-läge. Den globala grinden är **borttagen**, inte kringgången,
och `test_product_boundaries.test_the_global_editing_gate_is_gone` faller om den
smyger tillbaka.

Tre följdändringar som hörde ihop med den:

1. **Sådd tillåts i Cloud-läge.** En kopia är ingen ändring — den aktiva
   träffen står orörd. Att vägra den lämnade tidtabellen utan något att
   redigeras *i*, vilket är hur en smal regel blir en bred.
2. **Aktivering kontrollerar det utkast som faktiskt aktiveras**, inte det som
   råkade sparas sist. Spara och aktivera är två anrop.
3. **Radordning är ingen ändring.** Jämförelsen sorterar på id, för en grind som
   vägrar en sparning över radordning är en grind folk går runt.

Jämförelsen är muterad från fyra håll: grinden som slutar jämföra, `trains`
inflyttad bland de låsta sektionerna, aktiveringens kontroll borttagen, och
sorteringen borttagen. Alla fyra fångas.

### ✅ T4 — ingen tyst aktivering

`auto_sync_cloud_runtime()` anropade `runtime_store.install(download.package)`
med `activate` som default `True`, och pollerloopen anropade sedan
`server.request_restart()`. En träff kunde alltså starta om under händerna på
tågklareraren för att Cloud råkat publicera.

Nu gäller:

| | |
|---|---|
| Pollern hämtar | ja, var 15:e sekund som förut |
| Pollern aktiverar | **aldrig** |
| Pollern startar om | **aldrig** |
| Pollern kör i `offline-meet` | **nej** — inte "hämta men aktivera inte", utan hämta inte alls |
| Aktivering | kräver att revisionens id skickas tillbaka |

Den hämtade revisionen läggs som **väntande** (`pending_publication_id` i
`runtime_settings`, publiceringen lagrad med `active = 0`). Nyckeln behövs
utöver `active = 0`: en lokal revision som byggts men inte aktiverats ligger
också inaktiv, och de två får inte förväxlas.

`/v1/runtime/pending` svarar med vad som väntar **och en diff**: omdöpta
stationer vid namn, ändrade tåg vid nummer, tillagda och borttagna rörelser
räknade. Att räkna rader räcker inte — "3 stationer ändras" säger ingenting om
huruvida ändringen spelar roll.

`/v1/runtime/pending/activate` kräver att `publication_id` skickas med och
stämmer. Utan det skulle en flik som stått öppen sedan i morse kunna aktivera
något som kommit sedan dess — samma tysta överskrivning, men genom UI:t.

Muterat från fem håll: pollern aktiverar igen, offline-grinden borta, id-kravet
borta, `install()` aktiverar trots `activate=False`, och självläkningen borta.
Alla fem fångas.

**Kvar:** en väntande revision syns i BYGG steg 1. Den syns *inte* i KÖR-vyerna,
där en tågklarerare tillbringar dagen. Det är rimligt att den borde göra det,
men det är en egen designfråga och paketet beskriver ingen sådan markör.

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
| TMBox v2 | KÖR › TMBox v2 (testklient + tre dokumentationsvyer) | ✅ |
| TKL-terminal | KÖR › TKL (inbäddad) | 🔨 |
| Skärmar och klocka | KÖR › Skärmar | 🔨 |
| Användare och åtkomst | Inställningar | 👁 |
| Programuppdatering | Inställningar | 👁 |
| Server och nollställning | Inställningar | 👁 |

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
| 3.6.1 | Testklienten oförändrad mot dagens vy | 👁 |
| 3.6.2 | Fyra undervyer: Testklient, Flöden, Skärmkatalog, Referens | ✅ uppmätt |
| 3.6.3 | Vald undervy: underkant `#c96442`, övriga `--ink-muted` | ✅ |
| 3.6.4 | Skärmkatalogen ritar guldfilens rutor, inte avritade | ✅ testat |
| 3.6.5 | Flödeskartan härleder varje steg ur tangentspåren | ✅ testat |
| 3.6.6 | Geometrivalet slår igenom i alla undervyer | ✅ uppmätt |

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
| 3.8.1 | Tre numrerade sektioner i **ett** kort | 👁 |
| 3.8.2 | Stationer: ordningsnummer i cirkel, signatur monospace 76px | 👁 |
| 3.8.3 | Härledd länkbeskrivning per station | 👁 |
| 3.8.4 | Sträckor: från, till, spårtyp, trafikeringsregel | 👁 |
| 3.8.5 | Paneler: station, namn, fyra slot-chips A–D | 👁 |
| 3.8.6 | **Genväg: *Bygg från stationsordningen*, idempotent** | 👁 |
| 3.8.7 | Låst av Cloud: `#f7f5f0`/`#8a857a`, märke "🔒 Låst av Cloud" | 👁 |
| 3.8.8 | Lokal: vita fält, märke "✎ Redigerbar" `#a44f33` | 👁 |

**Läget i steg 2:** båda lägena är byggda, testade och visuellt verifierade vid
1280 och 924 px. Låst läge ritar fälten som `div` med paketets ruta, inte som
avstängda `input`: ett låst fält är ingen kontroll, och en skärmläsare ska inte
kalla det "redigerbart textfält". Öppet läge ritar riktiga fält och sparar vid
`change`, inte vid varje tangenttryckning — det senare hade skrivit "L", "Le",
"Lek" som tre revisioner.

Genvägen **lägger till det som saknas och skriver aldrig om det som finns**. En
sträcka någon satt till dubbelspår förblir dubbelspår, en panel någon döpt om
behåller sitt namn, och en plats någon riktat behåller sin riktning. Att köra
den en andra gång gör därför ingenting. Det är den egenskapen som gör knappen
trygg att trycka på när man inte minns om man redan tryckt — vilket under en
träff är för det mesta. Sju tester och tre mutationer håller den.

Sådd-kopplingen är på plats: ett tomt utkast visar *Hämta från aktiva träffen*
i stället för genvägen, och genvägen visas först när det finns stationer att
härleda ur.

En A–D-plats pekar på en **sträcka**, inte på en granne. Grannen är sträckans
andra ände sedd från panelens station.
`test_a_panel_slot_carries_the_connection_id_not_a_station` pinnar det, för
uppslaget ser rätt ut även när det är fel — det renderar bara ett id i stället
för ett namn.

**Kvar i steget:** `⋯`-menyn per rad (paketet har den; i låst läge finns inget
bakom den, och i öppet läge saknas *Lägg till station* och *Ta bort*), och
omordning av stationer.

### 3.9 BYGG › 3 Tidtabell

> **P6 gäller här:** tidtabellen är redigerbar även när grundrevisionen kommer
> från Cloud, till skillnad från steg 2. Grinden är öppnad (se ✅ T3) och
> API-vägen fungerar — `save_local_configuration` tar emot tidtabellsändringar
> i Cloud-läge och `activate_local_configuration` gör dem till en lokal
> revision. Det som saknas är vyn. Bygg inte det här steget som skrivskyddat.

| # | Krav | Status |
|---|---|---|
| 3.9.1 | Segmenterad kontroll: Tid / Station / Tåg | ✅ |
| 3.9.2 | **Grupperingen ändrar vyn, inte datan** | ✅ testat |
| 3.9.3 | Stationsfilter och sökruta | ✅ |
| 3.9.4 | Ändrade rader: orange kantmarkering + chip *Ändrad* | ✅ |
| 3.9.5 | **Massredigering av markerade rader** | ✅ spår, dagar, tidsförskjutning |
| 3.9.6 | Kolumner enligt paketet | ✅ tåg, dagar, station, spår, ankomst, avgång, från, till, anteckning |
| 3.9.7 | Redigering öppen även när grunden kommer från Cloud | ✅ sådd ur aktiv publikation |

> Steget hade varken panel eller sektion och lämnade en tom yta — rapporterat
> från drift som att sidan tog lång tid att ladda. Ingenting var långsamt; det
> fanns inget att rita.
>
> Vyn läser `/v1/build/timetable`, som svarar med samma form vare sig raderna
> kommer ur en Cloud-publicering eller ett lokalt utkast. Sparningen läser
> utkastet först och sår det ur den aktiva publikationen när det är tomt —
> annars vägras en tidtabell vars rader pekar på stationer utkastet inte har,
> vilket är just fallet när träffen kommer från Cloud.
>
> Uppmätt med 499 rörelser och elva stationer: steget öppnar på 119–130 ms,
> omgruppering och sökning under 175 ms, och tabellen skrollar i sin egen ruta
> så att sidan aldrig blir bredare än skärmen.

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
Knappformen är sedan kvalitetsrundan också paketets, se **Avvikelser 4**.

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
| 6.6 | Radier 8/10/12/999px | 🔨 **steg 5 följer paketet** (knappar och fält 8px, chip 999px, kort 12px), uppmätt i webbläsaren. Globalt är `button` fortfarande 999px och `input` 14px, vilket gäller KÖR och byggsteg 1–4. Se **Avvikelser 4** |
| 6.7 | Fälthöjd 34/36px, knapphöjd 30/32–36px | 👁 i steg 5: alla sju knappar uppmätta till 34–36px, kant `#e0dcd1`. Utanför steg 5 gäller den globala regeln |
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
| K6b | Test: BYGG 5 Server | ✅ 22 tester i `test_kor_bygg_structure.BuildStepFiveServerTests`, alla mutationsprovade |
| K7 | Test: tidtabellsgruppering | ⬜ |
| K8 | Test: degraderade skärmar | ⬜ |
| K9 | Visuella regressionstester | ⬜ |
| K10 | `prefers-reduced-motion` | 👁 mätt i Chromium med `reducedMotion: "reduce"`: uppdateringsstegets markör får `animation-name: none`, och steg 5:s knappar `transition-property: none` — annars tonade fokusringen in över 200ms |
| K11 | Tangentbord, fokus, etiketter, kontrast | 🔨 **steg 5 är gjort och mätt**: tabbordning, `<summary>` öppnas med Enter, alla fem fält har etikett, och tre knappar nådda med Tab visar 2px `#c96442`-ring (`:focus-visible` kräver tangentbord — `element.focus()` ger ingen ring och duger inte som bevis). Övriga vyer är inte genomgångna, och kontrast är inte mätt någonstans |
| K12 | Konsol fri från JS-, nät- och CSP-fel | 👁 steg 5 och alla fem körflikar körda: inga JS-, CSP- eller nätfel. Ett kvarstående 404 finns, se **Avvikelser 6** |
| K13 | Inga hemligheter eller exempeldata incheckade | ✅ |
| K14 | Dokumentation och versionsanteckningar | 🔨 |
| K15 | Minst minor-version | 🔨 (`[minor]` i commit) |
| K16 | Bildjämförelser vid 924px och desktop | 🔨 **steg 5 jämfört** vid 924, 1440 och 390px mot paketets två bilder, inklusive pixelmätning av knappar och fält i `bygg-09`/`bygg-10`. Övriga skärmar är fortfarande ojämförda |

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
`TrainMeetHTTPApplication`, riktiga CSP-headers, riktiga API:er). Två skript i
sessionens arbetskatalog: **80 kontroller** för struktur och flöde, **73** för
knapparna. Alla passerar.

Servern byggs som `tests/test_http_server.py` gör — en `TrainMeetHTTPApplication`
i en tråd — i stället för via `local_server`, som vill ha en MQTT-broker som
adminvyn ändå inte rör. Kör sedan installationsflödet i
`DESIGNPAKET-HANDOFF.md`, annars ligger `#app-view` på `display: none`.

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

### Knapparna (kvalitetsrundan)

73 kontroller, alla gröna. Alla sju knappar i steget mätta i varje tillstånd.

| Vad | Bevis |
|---|---|
| Form | Alla sju: radie `8px`, kant `1px`, höjd 34px (nollställningsknappen 36px) |
| Sekundär | `#ffffff` yta, `#e0dcd1` kant, `#1f1e1d` text — *Spara servernamn*, *Sök efter uppdatering*, *Försök igen*, *Starta om servern* |
| Primär | `#c96442` yta, vit text, vikt 600 — *Spara inloggning*, *Installera och starta om* |
| Destruktiv | Upplåst `rgb(192,57,43)`, skild från primärens `rgb(201,100,66)` |
| Hover | Sekundär byter till `#faf9f5`; primär till `#a44f33`; destruktiv `brightness(.94)` |
| Active | Primär `#a44f33` + `brightness(.94)`, mätt med musknappen nere |
| Fokus | *Nådd med Tab*, inte `element.focus()`: `solid 2px rgb(201,100,66)` på primär, sekundär och destruktiv |
| Disabled | `#8a857a` text på `#f7f5f0`, `cursor: not-allowed`, ingen opacity kvar |
| Fälten i samma kort | `#admin-server-name`, `#admin-username`, `#factory-reset-confirmation` alla `8px` |
| Formen håller i bredd | `8px` vid 924, 1440 och 390px, och under `prefers-reduced-motion` |
| Etiketten | "Skriv NOLLSTÄLL …" står på **en** rad — global `label { display: grid }` delade den i tre |
| Scopet | Klassen bort ⇒ `999px`/`#f7efe9`; klassen tillbaka ⇒ `8px`/`#ffffff` |
| KÖR orörd | Elva grupper knappar jämförda mot samma sida serverad ur `198b73f`: fem körflikar, applocket, byggsteg 1–4, sidopanelen. Alla identiska |

### Tester

```
PYTHONPATH=src:tests python3 -m unittest discover -s tests -q
```

| Miljö | `198b73f` | HEAD |
|---|---|---|
| med `mosquitto` | `Ran 293 ... OK` | **`Ran 315 ... OK`** |
| utan `mosquitto` | `Ran 291 ... OK (skipped=1)` | `Ran 313 ... OK (skipped=1)` |

Tvåan i differensen är `MQTTIntegrationTests`, vars `setUpClass` hoppar över
**klassen** när brokern saknas — då räknas dess två metoder inte i `testsRun`.
Det förklarar hela skillnaden mellan sessionernas rapporter; se
överlämningens *Varför siffran hoppade mellan sessionerna*.

Att inget test tappats är kontrollerat, inte antaget. En inventering över
`TestLoader().discover()` ger **293 test-id på `198b73f` och 315 på HEAD, med
0 laddningsfel i båda, 0 borttagna och 22 tillagda** — alla 22 i
`BuildStepFiveServerTests`.

Alla 22 är mutationsprovade. Varje mutation fäller minst ett test:

| Mutation | Fäller |
|---|---|
| Ändrad statusruteetikett | `test_identity_is_three_status_boxes_and_the_server_name` |
| Uppdateringsstegen hårdkodade i `app.js` | `test_the_seven_update_steps_come_from_the_server` |
| `<details open>` på nollställningen | `test_the_reset_is_collapsed_and_carries_the_packages_edges` |
| Knappregeln avscopad till global `button` | `test_the_button_shape_is_scoped_to_the_step_not_global` + `..._carry_the_packages_shape` |
| `999px` radie i steg 5 | `test_the_step_five_buttons_carry_the_packages_shape` |
| Destruktiv knapp i accentfärg | `test_the_destructive_button_stays_distinct_from_the_primary` |
| `opacity: .5` tillbaka på disabled | `test_a_disabled_button_reads_as_disabled` |
| `server-step-card` borttagen från ett kort | `test_the_button_shape_is_scoped_to_the_step_not_global` |
| Reduced-motion-regeln borttagen | `test_reduced_motion_stops_the_button_transition` |

Två miljöberoenden måste vara på plats: `pip install paho-mqtt` (annars faller
fyra MQTT-moduler på importfel) och `apt-get install mosquitto` (annars kör
integrationstesterna inte alls).

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

### 4. Knapparnas form och färg — **lagad i steg 5, scopad dit**

Paketets DEL 6 säger `Radie: 8px fält och knappar` och `Kantlinje #e0dcd1 |
fält, knappar`. Pixelmätning i paketets bilder bekräftar det:

| Uppmätt i paketet | Värde |
|---|---|
| Primärknappens yta (`bygg-10`) | `rgb(193–208, 93–103, 66)` ⇒ `#c96442` |
| Primärknappens höjd | överkant y=329, underkant y=363 ⇒ **34px** |
| Primärknappens hörn | vänsterkanten går från x=480 vid y=329 till x=468 vid y=336 ⇒ **~8px radie** |
| Sekundärknappens yta (`bygg-09`, *Spara servernamn*) | `#ffffff`, kant ~`#dcdcdc` ⇒ `#e0dcd1` |
| Fältets hörn (`bygg-09`, *Servernamn*) | överkant y=344, vänsterkanten stabil vid y=351 ⇒ **~8px radie** |

Repot hade `button { border-radius: 999px }` med accenttonad sekundärknapp och
`input { border-radius: 14px }` — former paketet inte visar någonstans.

**Lagat, men scopat till steg 5** via `.server-step-card`, som de fyra korten
bär. Den globala `button`- och `input`-regeln är oförändrad, eftersom den slår
igenom på varje KÖR-vy och på byggsteg 1, som redan står 👁 i den här listan.
Att byta den globalt kräver omverifiering av allt det, och är därför ett eget
block (se överlämningens ordningslista, punkt 6).

Att scopet håller är bevisat på två sätt, inte antaget:

1. **Mot verkligheten, inte mot en förväntan.** Commit `198b73f` serveras
   samtidigt på en andra port ur en `git worktree`. Varje synlig knapp i de fem
   körflikarna, i applocket, i byggstegen 1–4 och i sidopanelen läses av på båda
   sidor och jämförs som strängar. Alla elva grupperna är identiska.
2. **Direkt orsakstest.** Tas klassen bort från uppdateringskortet faller
   knappen tillbaka till `999px` och accenttonen; sätts den tillbaka blir det
   `8px` och vitt igen. Formen kan alltså inte komma någon annanstans ifrån.

En fälla att känna till: knappar har global `transition: all 200ms ease`. Varje
mätning direkt efter en tillståndsändring läser mitt i övergången — en
disabled-knapp som just låstes upp mätte `rgb(196,71,58)` i stället för
`#c0392b`, och en radie mitt i en 8px→999px-övergång mätte fortfarande 8px.
Verifieringen väntar 400ms efter varje tillståndsbyte, och stänger av
övergången i orsakstestet.

### 5. Nollställningens knapp är röd, och paketet har ingen röd

Paketets adminpalett innehåller ingen röd alls. Det beror rimligen på att
nollställningen ligger hopfälld i varenda skärmbild, så knappen aldrig syns
och aldrig behövde en färg.

Att ge den accentfärgen skulle göra *Installera och starta om* och
*Fabriksåterställ servern* till samma knapp. Den ena tar en uppdatering, den
andra raderar administratören och all träffdata. **Formen följer paketet**
— 8px, 1px kant, 36px — men färgen är kvar på `#c0392b`. Ett test faller om
den byts till accentfärgen.

Samma resonemang som Avvikelse 7: en verklig säkerhetsskillnad väger tyngre än
en etikett i en bild.

### 7. Inställningar är ett eget läge — paketet har fyra byggsteg nu

Paketets DEL 3.11 lägger serveradministrationen som **BYGG steg 5**. Den ligger
numera i ett tredje läge i stället, och BYGG har fyra steg.

Skälet kommer från drift, inte från smak. För att uppdatera programvaran fick
en operatör trycka **Bygg om träffen** — ett flöde som handlar om träffens
innehåll och som varnar att ändringar inte slår igenom förrän man aktiverar.
Att administrera servern hörde inte hemma bakom det. Kugghjulet på översikten
hette dessutom "Öppna administration" men landade i steg 1, som svarar på var
träffen kommer ifrån.

Uppdelningen är nu:

| Läge | Vad |
|---|---|
| **KÖR** | Det som händer under träffen |
| **BYGG** | Träffens innehåll: källa, bana, tidtabell, TMBoxar |
| **Inställningar** | Servern själv: identitet, åtkomst, programvara, Cloud-koppling, nollställning |

Cloud-kortet flyttade med, eftersom det bär kopplingen och parkopplingen av
lådor — båda serveradministration. Källvalet står kvar i BYGG steg 1: det
handlar om var träffen kommer ifrån, inte om kopplingen.

Innehållet är oförändrat. Samma sektioner, samma DOM, samma API-anrop och
samma sju uppdateringssteg ur `update_contract.py`. Bara flyttade.

### 6. 404 på `/terminal/config` — **avsiktligt, ska inte lagas**

Körfliken TKL bäddar in `/tkl/`, och terminalen frågar efter
`/terminal/config` så snart fliken öppnas. Servern svarar 404, och det syns i
konsolen.

Det såg ut som en olöst avvikelse och bokfördes som en sådan. Det är fel.
Anropet är terminalens sätt att ta reda på var den kör, och 404 är det
förväntade svaret. `trainmeet-tkl/src/api.ts:223`:

```js
export async function loadTerminalConfig(): Promise<TerminalConfig> {
  try {
    return await readJSON<TerminalConfig>("/terminal/config");
  } catch {
    // When hosted by TrainMeet Server the browser keeps its own terminal profile.
```

Kör terminalen fristående med sin egen backend finns endpointen och svarar med
en serversidig profil. Är den gäst hos TrainMeet Server finns den inte, och
terminalen använder en webbläsarlokal profil i stället.

**Lägg alltså inte till rutten här.** Gör man det får terminalen för sig att
den är fristående, och den slutar läsa den profil den faktiskt har.

Vill man bli av med bruset är rätt ställe `trainmeet-tkl`: låta terminalen
fråga något Server *svarar* på i stället för att använda ett 404 som besked.
Det är en ändring i ett annat repo och en ombyggd bundle, och den gör inget
bättre för användaren — bara konsolen tystare.

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
