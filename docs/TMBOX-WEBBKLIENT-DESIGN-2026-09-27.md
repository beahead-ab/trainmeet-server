# TMBox-webbklienten i Serverns UI-kit – kravspec för Codex

Datum: 2026-09-27. Gäller repot `trainmeet-server`. Underlag: designskiss på
Cowork-canvasen ”TMBox i Server-designen” (fyra artboards: `/tmbox/` mobil
väntar på station, `/tmbox/` mobil tilldelad, `/tmbox/` dator, `/tmbox-lab/`),
samt genomlysning av koden på grenen `claude/klockskarm-och-puts` (68d405d).

## Status: implementerad 2026-09-27 (Claude, gren `claude/tmbox-webbklient-design`)

Byggd direkt i stället för via Codex. Avvikelser från texten nedan, alla för att
boxen ska bli exakt oförändrad:

- **Boxkortets inre mått behålls** (`padding: 23px 18px`, 18/12 px under 1100 px,
  24 px under 900 px, max 430 px på mobil, samma sidmarginaler som förut) i stället
  för 12–16 px i avsnitt 4.3. Boxen skalar med kortets bredd (`cqi`); andra mått
  hade gjort den större eller mindre.
- **Generella button-regler** avgränsas med `:where(.tmbox-case) button` i stället
  för `.tmbox-case button`. `:where()` ger samma specificitet som tidigare, så
  `.key` vinner exakt som förut.
- **Det tillagda regelsteget** låser både typsnitt, storlek, radhöjd och textfärg:
  `.tmbox-case { font: 400 16px/normal ui-sans-serif, …; color: #242c2b; }` –
  det boxen tidigare ärvde från sidan.
- Provbänkens långa regeltext finns kvar, hopfälld under **Alla regler i detalj**.

Verifierat: hela Python-sviten, sex Node-enhetstester och fyra webbläsartester
gröna. Boxen renderad med gammal och ny stilmall i 250, 300, 358 och 442 px bredd
(också med fokusmarkering): pixelidentisk utanför de fyra rundade hörnen, där
sidans bakgrund syns.

## 0. Sammanfattning

Sidorna `/tmbox/` (virtuell TMBox) och `/tmbox-lab/` (provbänk) har i dag ett
eget utseende – beige bakgrund, kopparfärgade rubriker, systemtypsnitt – som
inte hör ihop med resten av TrainMeet Server, som sedan 1.13.0 använder
UI-kitet i `web/server-design.css`. Deltagaren scannar QR-koden på TV:n, landar
i deltagarvyn (ny design), trycker **Starta virtuell TMBox** och hamnar på en
sida som ser ut som en annan produkt. Den sömmen ska bort.

**Själva boxen – det rosa skalet, den blå LCD-displayen och knappsatsen – ska
inte ändras med en pixel.** Den ser ut exakt som den fysiska TMBoxen och är
referensen för hur boxen ser ut i verkligheten. Allt runt omkring – sidhuvud,
kort, texter, knappar, typsnitt, färger, avstånd – byter till Serverns UI-kit.

## 1. Förutsättningar och arbetssätt

- Grenen `claude/klockskarm-och-puts` ska vara sammanslagen till `main` innan
  arbetet börjar. Den ändrar `terminal16_web/live.html`, `web/server-ui.js`,
  `web/app.js` och `web/index.html`, alltså samma filer som detta arbete rör.
- Ny gren från `main`: `codex/tmbox-webbklient-design`.
- Inga ändringar i trafiklogik, protokoll, MQTT, `terminal16*.py`, firmware
  eller Cloud. Detta är enbart HTML/CSS och en liten mängd JS i
  `src/tmbox_gateway/terminal16_web/`.
- Ingen ny extern resurs: serverns CSP är `style-src 'self'` och servern körs
  utan internet på träffen. Typsnitt finns redan lokalt under `/assets/fonts/`.
- Push men ingen driftsättning. Claude verifierar resultatet på grenen
  (se avsnitt 8) innan merge.

## 2. Berörda filer

| Fil | Ändring |
|---|---|
| `src/tmbox_gateway/terminal16_web/style.css` | Delas i två tydligt markerade block: **FRUSET** (boxen, byte för byte oförändrat) och **SIDA** (allt annat, skrivs om mot UI-kitets tokens). |
| `src/tmbox_gateway/terminal16_web/live.html` | Ny sidstruktur för `/tmbox/` enligt avsnitt 4. |
| `src/tmbox_gateway/terminal16_web/index.html` | Ny sidstruktur för `/tmbox-lab/` enligt avsnitt 5. |
| `src/tmbox_gateway/terminal16_web/terminal.js` | Minsta möjliga ändring: de element `update()`/`render()` skriver till finns kvar med samma id/klass. Tre tillägg, alla i live-läget: träffnamn i sidhuvudet (4.2), lägesrad och enhetskod i kortet Din TMBox (4.4), texten `offline` (4.4). Ingen ändring i `EntryBuffer`, `press()`, `apply()` eller pollningen. |
| `tests/test_terminal16_glyphs.py` | Befintligt test `test_original_client_palette_is_preserved` ska fortsätta passera. Nytt test enligt avsnitt 7. |
| `tests/js/public-workspaces-live.test.cjs` | Länknamnet på rad 78 uppdateras (avsnitt 4.2). |
| `tests/test_terminal16_pilot.py` | Kontrollen `ENBART TESTDATA` (rad ~888) ska fortsätta passera. |
| `docs/server-terminal16.md`, `CHANGELOG.md` | Kort notis under ”Nästa version”. |

Inget annat i `web/` ändras. `server-design.css` ändras inte – den läses som den är.

## 3. Boxen är fryst – exakt vad som gäller

Följande regler i `terminal16_web/style.css` får inte ändras, varken värden,
selektorer eller ordning. De flyttas oförändrade in i ett block som börjar med
kommentaren `/* ===== FRUSET: TMBox-skalet. Ändra inte. ===== */` och slutar
med `/* ===== slut FRUSET ===== */`:

```
:root-variablerna --case, --lcd-blue, --lcd-text, --key-panel, --key
.tmbox-case
.lcd-frame
.lcd
.lcd-row
.lcd-cell
.keypad
button (font: inherit; cursor; border; transition)
button:focus-visible
.key
.key.function
.key:active:not(:disabled)
.key:disabled
.key:focus-visible
@media (max-width: 1100px) – raderna för .tmbox-case, .lcd-frame, .keypad
@media (max-width: 900px)  – raderna för .tmbox-case, .keypad
@media (max-width: 360px)  – raden för .keypad
```

Exakt två anpassningar är tillåtna i det frysta blocket. Båda finns för att
boxen **inte** ska förändras när sidan runt omkring byter utseende:

1. Knapparna ärver i dag sitt typsnitt från `:root`. När `:root` byter till
   Inter skulle siffrorna och bokstäverna på knapparna byta utseende. Därför
   läggs en egen regel sist i blocket som låser dagens typsnitt:

   ```css
   .tmbox-case { font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
   ```

2. De generella reglerna `button { … }` och `button:focus-visible { … }` gäller
   i dag alla knappar på sidan. De flyttas in i blocket med **oförändrade
   deklarationer** men avgränsade selektorer, `.tmbox-case button` och
   `.tmbox-case button:focus-visible`, så att de fortsätter styra boxens
   knappar men slutar läcka ut på UI-kitets knappar.

Allt annat i blocket är byte-identiskt med dagens fil. Guldfilen i avsnitt 7
speglar blocket efter dessa två anpassningar.

DOM-strukturen som `terminal.js` bygger i `makeBox()` ändras inte:

```
article.box > .box-heading (h2 + .box-code) · .box-status · .box-queue ·
.tmbox-case (.lcd-frame > .lcd > .lcd-row > .lcd-cell ×16, .keypad > .key ×16) ·
.key-hints · .box-message · .box-timetable
```

Knapparnas ordning `123A 456B 789C *0#D`, `disabled`-logiken,
`aria-label`, tangentbordsstyrningen och `input_guard_ms`-spärren ändras inte.

## 4. `/tmbox/` – den virtuella boxen (deltagarens sida)

### 4.1 Layout

Mobil (≤ 900 px) i denna ordning, en kolumn, sidmarginal 16 px, 12 px mellan
korten:

1. Sidhuvud (avsnitt 4.2).
2. **Boxkortet** (avsnitt 4.3) – först, eftersom det är det deltagaren ska använda.
3. Kortet **Din TMBox** (avsnitt 4.4).
4. Hopfällt kort **Så fungerar knapparna** (avsnitt 4.5), stängt som förval.
5. Sidfot (avsnitt 4.6).

Dator (> 900 px): innehållet centreras med `max-width: 1000px`, två kolumner
`480px minmax(0,1fr)` med 14 px mellanrum. Vänster: boxkortet. Höger: Din TMBox
och därunder Så fungerar knapparna, utfällt.

Bakgrund `var(--bg)` (#fbfaf9). Typsnitt `var(--font)` (Inter, lokalt).
Grundstorlek 13 px enligt UI-kitet.

### 4.2 Sidhuvud

Samma mönster som deltagarvyns `.pv-top` i `web/index.html`:

- TrainMeet-ikonen (`/assets/ikon/trainmeet-ikon.svg`, 26 px).
- Träffens namn i fetstil 16 px, följt av `tm-badge` ”EU”. Namnet hämtas från
  `GET /v1/workspaces` (publik, används redan av deltagarvyn):
  `selected_meet.name`. Saknas det visas ”TrainMeet Server”. Hämtas en gång vid
  sidladdning; misslyckas anropet visas fallbacken utan felmeddelande.
- Spacer.
- Anslutningsprick 8 px: grön (`var(--ok-text)`) när `#connection` är i läget
  ”Ansluten till servern”, gul (`var(--warn-dot)`) när klienten tappat kontakt.
  Pricken speglar samma tillstånd som `#connection`-texten i dag.
- Länk `Träffens sida` som `tm-btn tm-btn--sm`, `href="/"`.
  Uppdatera `tests/js/public-workspaces-live.test.cjs` rad 78 till
  `{name: 'Träffens sida'}`.

`h1` och `#subtitle` som `terminal.js` skriver till behålls i DOM men visas
inte i sidhuvudet: `h1` blir visuellt dolt (`.visually-hidden`) och `#subtitle`
(värdadressen) flyttas till sidfoten (4.6). `#session-info` visas inte alls
längre – dess innehåll ersätts av kortet Din TMBox (4.4). Elementet behålls
dolt så att `update()` inte behöver ändras.

Länken ”Öppna isolerad provbänk →” tas bort från `/tmbox/`. Deltagaren ska inte
dit; administratören når provbänken från Hjälp (`/hjalp`), där länken redan finns.

**DOM-kontrakt mot `terminal.js`.** `update()` och `resetButtons()` skriver
ovillkorligt till `h1`, `#subtitle`, `#connection`, `#session-info`,
`#reset-all`, `#reset-clearance`, `#reset-direct`, `#reset-status`,
`#language-samples`, `#mode`, `#event-count` och `#events`. Alla dessa ska
finnas kvar i `live.html`; de som inte visas ligger kvar i dagens dolda
`<div hidden>`. `#boxes` och `#start-client` behåller sina id. Boxens kod är
`identity.device_code` och har formen `WEB-XXXXXXXX` för en webbläsarbox
(testet väntar på `/^WEB/`), inte `TMBOX-…` som en fysisk box.

### 4.3 Boxkortet

`article.box` blir ett UI-kit-kort: `background: var(--card)`,
`border: 1px solid var(--border)`, `border-radius: var(--radius)` (8 px),
`padding: 12px 14px 14px` (mobil) respektive `14px 16px 16px` (dator), ingen
skugga. Bredd: fyller kolumnen, max 480 px, centrerat på mobil.

Innehåll uppifrån och ned. Alla höjder är **reserverade** som i dag, så att
knappsatsen aldrig flyttar sig när text tillkommer eller försvinner:

| Element | Utseende |
|---|---|
| `.box-heading` | `h2` 16 px (dator 18 px) fet, `var(--text)`. `.box-code` som `pv-value`-chip: JetBrains Mono 14 px 600, bakgrund `var(--row-alt)`, kant `var(--border-soft)`, radie 6 px. Radhöjd ca 30 px. |
| `.box-status` | 12.5 px, `var(--text-3)`, `line-height: 1.5`, `min-height: 3em`, `overflow: auto`, `overflow-wrap: anywhere`. |
| `.box-queue` | Utan väntande förfrågningar: 12.5 px `var(--muted)`, `padding: 7px 10px`, genomskinlig kant, `min-height: 2.4em`. Med `.pending`: samma yta som `tm-state` (gul: bakgrund `var(--warn-bg)`, kant `var(--warn-border)`, text `var(--warn-text)`, 600) med en `tm-dot` framför texten. Dagens orange `#fff0d3/#d3a35f` utgår. |
| `.tmbox-case` | **Oförändrad.** |
| `.key-hints` | Chips i stället för lös text: varje `span` blir `display:inline-flex; gap:6px; font-size:12px; color:var(--text-2); background:var(--card); border:1px solid var(--border); border-radius:6px; padding:3px 8px`. Tangenten (`b`) i JetBrains Mono 700 `var(--text)`. Behållaren `display:flex; flex-wrap:wrap; gap:6px; align-content:flex-start; min-height:5.5em; overflow:auto; margin-top:10px`. |
| `.box-message` | 12 px, `var(--muted)`, `min-height: 3em`, `overflow: auto`. `.error` → `var(--danger-text)`. |
| `.box-timetable` | Visas bara i provbänken (live-klienten får ingen tidtabell). Tabell enligt `tm-table`: rubrikrad 11.5 px 600 `var(--text-3)` på `var(--row-alt)`, celler 12.5 px, tågnummer i Inter 700 med tabellsiffror (#122), tid `var(--muted)`. `caption` 12 px 700 vänsterställd. Avgränsas uppåt med `border-top: 1px solid var(--border-soft)`. |

`.box:focus-within` byter till UI-kitets fokus: `border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-soft)`.

### 4.4 Kortet ”Din TMBox”

`section.tm-card` med `tm-card__head` ”Din TMBox” och grå bitext ”på den här
servern”. Kroppen (`padding: 12px 14px`, `gap: 10px`):

- Lägesrad `tm-state`: **Väntar på station** (gul, `tm-dot`) när boxen saknar
  station; **Tilldelad station: {stationsnamn}** (`tm-state--ok`, grön) när den
  är tilldelad. Läget läses ur samma frame som boxkortet: saknas `frame.station`
  är boxen otilldelad.
- Etikett ENHETSKOD (11 px 700, versaler, `letter-spacing: .06em`,
  `var(--muted)`) och koden som `pv-value pv-value--big` (26 px JetBrains Mono)
  när boxen väntar på station; som vanlig `pv-value` (14 px) på samma rad som
  etiketten när den är tilldelad. Koden är `identity.device_code`.
- Text när boxen väntar: ”Visa koden för trafikledningen, som tilldelar din
  station under Inställningar → TMBoxar. Boxen börjar arbeta direkt när den är
  tilldelad – du behöver inte ladda om sidan.”
  Text när boxen är tilldelad (dator): ”Boxen arbetar mot träffens riktiga
  trafik. Trafikledningen kan flytta den till en annan station eller ta bort
  den under Inställningar → TMBoxar. Språket byter du med * på boxen.”
  På mobil i tilldelat läge räcker raden nedan.
- Sista raden 12 px `var(--muted)`: `#connection`-elementet, flyttat hit, följt
  av ” · uppdateras varje sekund”. Vid kontaktförlust visar `terminal.js` i dag
  `text.offline || "Testservern är inte ansluten."` – på den riktiga klienten
  är det fel ord. Lägg till `offline: "Servern är inte ansluten."` i det
  textobjekt `pollLive()` skickar till `update()`; testet
  `public-workspaces-live` letar bara efter ”inte ansluten” och påverkas inte.

Knappen `#start-client` (”Starta ny TMBox”) behålls med samma id och samma
dolda/visade-logik men flyttar till sidfoten som en textlänk-knapp
(`pv-link`-stil: ingen ram, `var(--primary)`, 12.5 px 600).

### 4.5 Kortet ”Så fungerar knapparna”

Ersätter dagens fritextstycke längst ned. Rubrik ”Så fungerar knapparna”,
bitext ”servern bestämmer texter och funktioner”. Sex rader i ett
tvåkolumnsrutnät `44px minmax(0,1fr)`, 12.5 px, tangenten i JetBrains Mono:

| Tangent | Text |
|---|---|
| 0–9 | Skriv tågnumret direkt. Siffrorna stannar i boxen tills du trycker #. |
| # | Bekräftar. Utför det visade tågets åtgärd: sök, begär klartecken, ge klart, rapportera avgång eller ankomst. |
| * | Tillbaka eller avbryt. Under inmatning töms numret. I översikten öppnas språkvalet. |
| A | Förfrågningskön – alltid, oavsett var du är. |
| B | Aktiva tåg från översikten. Under inmatning suddar B sista siffran. |
| C / D | Bläddrar: mellan aktiva tåg, i kön eller i tidtabellen. |

Mobil: `details.tm-card` med `summary` som kortrubrik, stängt som förval.
Dator: alltid utfällt (vanligt kort).

### 4.6 Sidfot

12 px `var(--muted)`, `justify-content: space-between`, `padding: 14px 16px 20px`:
vänster ”TrainMeet Server {version} · {#subtitle = värdadress}”, höger
`#start-client`. Versionen hämtas ur `/v1/workspaces` om fältet finns, annars
utelämnas den (”TrainMeet Server · server.local:8080”).

## 5. `/tmbox-lab/` – provbänken (administratörens sida)

Samma UI-kit, samma boxkort som i 4.3 (inklusive `.box-timetable`). Skillnader:

- Sidhuvud: ikon, **TMBox-provbänk** i fetstil, `tm-tag tm-tag--warn` med
  texten `ENBART TESTDATA` (exakt den strängen – testet i
  `test_terminal16_pilot.py` letar efter den), grå bitext ”Isolerad från träffens
  trafik och databas”, spacer, ”Testservern ansluten” + prick, länk
  `Till driften` (`tm-btn tm-btn--sm`, `href="/drift"`).
- Verktygsrad direkt under sidhuvudet: ett `tm-card` i radform
  (`flex-direction: row; align-items: center; gap: 8px; padding: 10px 12px;
  flex-wrap: wrap`) med knapparna `#reset-all` ”Nollställ alla enheter”,
  `#placement-open` ”Testa vänster/höger”, en lodrät avdelare, `#reset-clearance`
  ”Nytt test · med klartecken”, `#reset-direct` ”Nytt test · direkttrafik”,
  spacer, och till höger `#mode` + `#event-count` som 12 px `var(--muted)`.
  Alla knappar som `tm-btn`. `#reset-status`/`#placement-status` visas under
  raden när de har text (12 px, grön `var(--ok-text)`, `.error` →
  `var(--danger-text)`); tomma tar de ingen plats.
- Tre boxkort i rutnät `repeat(3, minmax(0,1fr))`, 14 px mellanrum, sidmarginal
  24 px. Under 1100 px: två kolumner; under 900 px: en kolumn med kortet
  centrerat (max 480 px).
- Under boxarna två kort sida vid sida (`minmax(0,1.6fr) minmax(0,1fr)`):
  **Prova en hel tågrörelse** med dagens fem steg som `pv-step`-rader
  (numrerad cirkel 24 px `var(--primary-soft)`/`var(--primary-text)`, text
  12.5 px, tangenter och stationsnamn i JetBrains Mono), och **Teckentest**
  med dagens förklaring och `#language-samples` i ett `details`. Dagens långa
  brödtext om A/B/C/D/# ersätts av kortet ”Så fungerar knapparna” (4.5) som
  läggs under ”Prova en hel tågrörelse”.
- `details.audit` (”Vad tog servern emot?”) blir ett `tm-card` med `summary`
  som kortrubrik; listan i JetBrains Mono 12 px.
- Placeringsdialogen `#placement-dialog` byter till UI-kitets modalutseende:
  vit yta, `border-radius: 10px`, kant `#d9d9d9`, `box-shadow: var(--shadow-menu)`,
  rubrik 16 px 700, kryss som `tm-icon-btn`, fält som `tm-field`/`tm-input`,
  sidfot med `tm-btn` för Återställ standard och Avbryt samt `tm-btn tm-btn--primary`
  för Spara. Ingen ändring i dialogens id, formulärfält, CAS-logik eller
  tangentbordshantering.
- Språkprovens LCD-rutor (`.language-sample .lcd-frame`) använder det frysta
  blocket och ändras inte; själva kortet runt dem blir `tm-card`.

Fristående körning (`python -m tmbox_gateway.terminal16_public`) ska fortsätta
fungera. Där finns inte `/assets/server-design.css`; sidan ska då falla tillbaka
på systemtypsnitt utan att layouten går sönder. Det räcker att `style.css` själv
bär de tokens den behöver (avsnitt 6).

## 6. CSS-strategi

- `live.html` och `index.html` länkar **först** `/assets/server-design.css`
  (som via `@import` ger `/assets/fonts/fonts.css` med Inter och JetBrains Mono,
  båda redan lokalt paketerade), **därefter** `./style.css` respektive
  `/tmbox/style.css`.
- `style.css`, blocket SIDA: definiera de tokens sidan använder på `:root`
  med samma värden som `server-design.css` (`--bg`, `--card`, `--border`,
  `--border-soft`, `--row-alt`, `--text`, `--text-2`, `--text-3`, `--muted`,
  `--primary`, `--primary-soft`, `--primary-text`, `--warn-bg`, `--warn-border`,
  `--warn-dot`, `--warn-text`, `--ok-bg`, `--ok-text`, `--danger-text`,
  `--radius`, `--radius-sm`, `--font`, `--font-mono`), så att sidan ser rätt ut
  även när kitet inte laddats (fristående provbänk). Kitets egen `:root` vinner
  när det finns eftersom det laddas först och har samma värden – ingen konflikt.
- De gamla tokens `--ink`, `--muted` (gammalt värde), `--copper`, `--line`,
  `--paper`, `--card` (gammalt värde) tas bort ur `style.css` tillsammans med
  alla regler som bara fanns för det gamla utseendet (`.eyebrow`, `.pill`,
  `.intro`, `.legend`, `.test-tools`, `.test-actions`, `.secondary`,
  `.reset-controls`, `.footnote`, `.page-header`, gamla `h1/h2/h3/p`).
  Variablerna `--case`, `--lcd-blue`, `--lcd-text`, `--key-panel`, `--key`
  behålls med samma namn och värden (testet i `test_terminal16_glyphs.py`).
- Använd kitets klasser (`tm-card`, `tm-card__head`, `tm-btn`, `tm-btn--sm`,
  `tm-btn--primary`, `tm-badge`, `tm-tag`, `tm-state`, `tm-dot`, `pv-value`,
  `pv-link`) direkt i markupen i stället för att kopiera reglerna. Det som
  saknas i kitet (chip för tangenthintar, `.krow`-raderna i 4.5,
  `.box`-kortets inre mått) skrivs i SIDA-blocket.
- Inga `!important`, inga inline-stilar, ingen `<style>` i HTML (CSP).

## 7. Skydd av det frysta blocket – nytt test

Lägg till `tests/tmbox_case_golden.css` med det frysta blockets exakta innehåll
(inklusive tillägget för `.tmbox-case { font-family … }`) och ett test i
`tests/test_terminal16_glyphs.py`:

```python
def test_tmbox_case_block_is_frozen(self):
    root = Path(__file__).resolve().parents[1]
    css = (root / "src/tmbox_gateway/terminal16_web/style.css").read_text()
    start = css.index("/* ===== FRUSET: TMBox-skalet. Ändra inte. ===== */")
    end = css.index("/* ===== slut FRUSET ===== */")
    golden = (root / "tests/tmbox_case_golden.css").read_text()
    self.assertEqual(css[start:end].strip(), golden.strip())
```

Guldfilen skapas genom att kopiera de befintliga reglerna i avsnitt 3 ur
dagens `style.css` (commit 68d405d) – inte genom att skriva dem på nytt.

## 8. Verifiering (Codex kör; Claude kontrollerar efteråt)

1. `python -m unittest` – hela sviten grön. Node-testerna under `tests/js/`
   gröna, inklusive `public-workspaces-live.test.cjs` efter länkändringen.
2. `git diff 68d405d -- src/tmbox_gateway/terminal16_web/style.css` visar att
   varje rad i avsnitt 3 finns kvar oförändrad, med undantag för de två
   anpassningarna (flytt inom filen är okej). Det nya testet i avsnitt 7 är grönt.
3. Skärmdumpar i verklig webbläsare mot en tillfällig EU-server:
   `/tmbox/` vid 390 px (väntar på station respektive tilldelad) och 1280 px;
   `/tmbox-lab/` vid 1280 px och 390 px. Boxarna ska vara identiska med
   skärmdumpar av samma sidor tagna före ändringen (samma bredd på kortet ger
   samma boxstorlek, eftersom boxen skalar med `cqi`).
4. Flödet deltagarvy → **Starta virtuell TMBox** → boxkod syns → admin tilldelar
   station i Inställningar → boxen visar stationens översikt utan omladdning.
5. Tangentbordet: siffror, `#`, `*`, A–D fungerar med fysiskt tangentbord som
   i dag (kortet har `tabindex=0`).
6. Kontaktförlust: dra ur nätet – pricken i sidhuvudet blir gul, raden i
   Din TMBox visar ”inte ansluten”, knapparna spärras.
7. `/tmbox-lab/` fristående (`python -m tmbox_gateway.terminal16_public`) laddar
   och går att köra utan konsolfel utöver 404 på `/assets/server-design.css`.
8. Inga CSP-fel i konsolen på någon av sidorna.

Verifiering på fysisk TMBox behövs inte: ingen firmware, inget protokoll och
inga serverns bildrader ändras.

## 9. Utanför denna leverans (eget PR efter A, kräver beslut)

Antecknat vid genomlysningen, ska **inte** göras i detta PR:

- **Den gamla TMBox v2-testklienten i adminappen** (`#tmbox-v2-view` i
  `web/index.html`, funktionerna `startTMBoxV2`, `refreshTMBoxV2`, `pressV2Key`,
  `drawV2`, `bindV2Controls`, `connectBrowserTMBox` m.fl. i `web/app.js`,
  ~700 rader, samt tillhörande regler i `web/app.css`) går inte att nå sedan
  1.10.0 – `/#tmbox` skickar vidare till `/tmbox/`. Den kör firmwarens gamla
  klientlogik (`tmbox-nav.js`, protokoll v2 över `/v1/tmbox-v2/*`) och laddas
  på varje adminsida. Förslag: ta bort UI:t och JS:en. `protocol_v2.py`
  (`TMBoxStationService`) är den gemensamma trafiktjänsten och rörs inte.
- **Hjälp-sidans Flöden, Skärmkatalog och Referens** ritas av samma gamla
  motor (`tmbox-guide.js` är ”audited against Server 1.8.0 and firmware 0.4.6”,
  visar 20×4 och beskriver A/D-knappar som inte längre gäller). De motsäger
  `/tmbox/`. Förslag: skriv om mot profilen server-16x2 eller dölj tills vidare.
- **Målprofil:** ändringsloggen säger att TMBox v2 är 20×4 (ESP32-S3);
  `docs/server-terminal16.md` säger 16×2 ”tills vidare” och den serverstyrda
  motorn ritar bara 16×2. Behöver ett beslut innan någon simulator byggs om.
