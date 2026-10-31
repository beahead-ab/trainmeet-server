# Lokföraren

Förarens app på TrainMeet Server, på `/lokforaren/` (#225, #226). Den går att
lägga på telefonens hemskärm. Designen finns som Claude Design-kanvas
"Lokföraren – körplan och NU-linje"; Codex tidigare skisser ligger kvar i
[design/lokforaren](design/lokforaren/README.md) som historik.

## Vem som kommer in

Förare är vanliga konton på servern med rollen **Förare** (`user`). De bjuds in
under **Inställningar → Användare** som andra konton. En administratör kan
bjuda in, redigera och ta bort förare; rollerna delar bara ägaren ut.

En förares session ger aldrig administratörsbehörighet: `/v1/admin/*`, klockan,
trafiken och inställningarna svarar 401. Föraren kommer bara åt `/v1/driver/*`.
Loggar en förare in på serverns vanliga inloggning skickas hen till
Lokföraren. Ägare och administratörer kan också använda appen.

## Hitta dit

- Appen heter **Lokföraren** och är en egen app: eget namn, egen ikon (LF i
  stationsskylten) och eget id och scope (`/lokforaren/`), skild från
  serverns admin. `/lokforare` och `/forare` leder också dit.
- Utan inloggning: kortet **Lokföraren** högt upp på träffens publika sida,
  och **Kör du tåg på träffen?** under inloggningen.
- För administratörer: ikonen med loket i sidhuvudet, **Lokförare** i menyn
  och **Inställningar → Lokförare och köruppdrag**, med adressen, en QR-kod
  och de tre stegen (bjud in, skanna, lägg till på hemskärmen).

### Nya versioner (cachebust)

Servern räknar fram appens byggstämpel, en hash av appens egna filer, och
skriver in den där det står `__LF_BUILD__`: i varje fil-adress i `index.html`
(`?v=…`) och i service workerns cachenamn. En ny version ger alltså en ny
`sw.js`, en ny cache och nya adresser; den gamla cachen tas bort. Appens filer
läses från cachen (de ändras aldrig under samma adress), sidan själv alltid
från nätet först. `/v1/driver/session` och `/v1/driver/clock` säger samma
stämpel: skiljer den sig laddas appen om vid start, och annars visar den
**Ny version av Lokföraren** tills föraren trycker eller kommer tillbaka till
appen. Samma stämpel laddas bara om en gång, så det kan aldrig bli en loop.

## Tjänsterna

Förarna väljer bland, i den här ordningen:

1. Köruppdragen som gjorts tillgängliga under **Inställningar → Lokförare och
   köruppdrag** (nedan), för träffen som körs.
2. Tjänsterna i träffens paket från Cloud, under `driver_duties`.
3. Övningstjänsten **132 DEMO** (fakta ur #225, tiderna i Tveiten är exempel).

### Köruppdrag i Inställningar

Köruppdragen laddas upp på varje server för sig, separat från Clouds paket.
Det går i tre uttryckliga steg, så att inget når förarna av misstag:

1. **Ladda upp.** PDF:en (högst 80 MB) sparas oförändrad i serverns
   datakatalog, `lokforaren/<sha256>.pdf` bredvid `trainmeet.db`. Samma fil
   igen för samma träff blir samma köruppdrag.
2. **Läs in.** Tjänsterna läses ur PDF:en i administratörens webbläsare med
   pdf.js (`web/pdfjs/`, Apache-2.0) och `lokforaren-koruppdrag.js`, och
   skickas till servern i formen nedan med `pages: [från, till]`. Servern
   tolkar ingen PDF; den kontrollerar formen med `normalize_duty`, som för
   Cloud. Det som inte gick att läsa blir avvikelser att titta på.
3. **Gör tillgängligt för förarna.** Först då syns tjänsterna under Välj
   tjänst, och bara på träffen köruppdraget laddades upp för. **Dra tillbaka**
   tar bort dem ur listan; den som redan kör behåller sin tjänst.

Tjänsterna ligger i databasen och följer med i säkerhetskopiorna; PDF-filerna
gör det inte. Föraren kan öppna förlagan (originalsidorna) från tjänsten.

```json
{
  "id": "duty-132",
  "number": "132",
  "operator": "CargoNet",
  "vehicle": "CN 02 CD312",
  "vmax": 80,
  "start": "17:10",
  "end": "21:02",
  "trains": [{"number": "G 5453", "from": "Spiten", "to": "Kjøllstad", "vmax": 80}],
  "notes": ["Fri text till föraren"],
  "events": [
    {"kind": "duty_start", "time": "17:10", "station": "Spiten", "notes": ["Förberedelse"]},
    {"kind": "departure", "time": "17:30", "station": "Spiten", "station_id": "station-spi", "track": "2", "train": "G 5453"},
    {"kind": "stop", "arrival": "17:58", "departure": "18:14", "station": "Tveiten", "track": "1", "train": "G 5453", "notes": ["Rundgång"]},
    {"kind": "pass", "time": "18:20", "station": "Steinsnes", "train": "G 5453"},
    {"kind": "arrival", "time": "18:25", "station": "Kjøllstad", "train": "G 5453"},
    {"kind": "duty_end", "time": "21:02", "station": "Kjøllstad"}
  ]
}
```

- `kind` är `duty_start`, `duty_end`, `departure`, `arrival`, `stop` (med
  `arrival` och `departure`), `pass` eller `note`.
- Tider är `HH:MM` i träfftid. En tid som ligger mer än sex timmar före den
  förra har gått över midnatt; `24:05` går också.
- En händelse som inte går att läsa hoppas över, inte hela tjänsten.
- En aktiverad tjänst sparas som den såg ut vid aktiveringen, så att en ny
  publicering inte byter körplan under någon som kör.

## Körplanen och NU-linjen

- Tidsaxel: en träffminut är 7 px, uppehåll längre än 40 minuter ritas 160 px
  högt, och rader som ligger tätt får den plats de behöver. Axeln är styckvis
  linjär och strikt växande, så linjen glider jämnt och hoppar aldrig.
- Klockan hämtas från `/v1/driver/clock` varannan sekund och räknas fram
  mellan svaren (`clock-sync.js`). Utan svar i tolv sekunder fryses klockan,
  nedräkningen och linjen; telefonens egen klocka används aldrig.
- Linjen följer med en tredjedel ner. Scrollar föraren släpper den och kommer
  tillbaka efter fyra sekunder eller med **Till NU**.
- Före start visas en nedräkning. Vid 20 och 10 träffminuter kommer en
  påminnelse i appen (ljud och vibration om det är på); vid 10 öppnas
  körplanen. Push när appen är stängd ingår inte än (kräver VAPID-nycklar).

## Incheckning och ikon

- **Checka in** sparar station, valfritt spår, tjänst, tåg och träfftid. Den
  flyttar aldrig NU-linjen och styr ingenting.
- En incheckning syns i 10 minuter i verklig tid och tonar sedan ut under 5
  (`CHECKIN_VISIBLE`, `CHECKIN_FADE` i `driver.py`). Bara den senaste räknas.
- Den som stänger av **Visa min ikon på banan** syns inte för andra.
- Ikonen beskärs och skalas ned i telefonen (canvas, 256 px JPEG). Servern
  tar emot PNG, JPEG eller WebP upp till 96 kB och läser bara filhuvudet.
- Ett borttaget konto lämnar ingen ikon, tjänst eller incheckning efter sig.

## API

| Metod | Väg | Vad |
|---|---|---|
| GET | `/v1/driver/session` | konto, träff, aktiv tjänst, klocka, träffens stationer |
| GET | `/v1/driver/clock` | träffklockan |
| GET | `/v1/driver/duties?q=132` | tjänster i träffens paket och DEMO |
| GET | `/v1/driver/duties/{id}` | en tjänst |
| POST | `/v1/driver/active` | aktivera `{duty_id}` |
| POST | `/v1/driver/active/end` | avsluta |
| POST | `/v1/driver/checkins` | `{station, station_id?, track?}` |
| GET | `/v1/driver/presence` | förarna på träffen och deras senaste incheckning |
| POST | `/v1/driver/profile` | `{visible}` |
| POST | `/v1/driver/avatar` | `{image: "data:image/jpeg;base64,…"}` |
| POST | `/v1/driver/avatar/remove` | ta bort ikonen |
| GET | `/v1/driver/avatars/{user_id}` | ikonen |
| GET | `/v1/driver/documents/{id}.pdf` | förlagan, om köruppdraget är tillgängligt |

Alla kräver en inloggad session; POST från en annan sida nekas.

För administratörer (ägare och administratörer, aldrig förare):

| Metod | Väg | Vad |
|---|---|---|
| GET | `/v1/driver-admin/overview` | förarna och vad de kör, köruppdragen |
| POST | `/v1/driver-admin/documents` | PDF:en som den är (`Content-Type: application/pdf`, `X-Filename`) |
| GET | `/v1/driver-admin/documents/{id}` | köruppdraget och dess tjänster |
| GET | `/v1/driver-admin/documents/{id}.pdf` | PDF:en, för att läsa in den igen |
| POST | `/v1/driver-admin/documents/{id}/index` | `{pages, duties, warnings}` |
| POST | `/v1/driver-admin/documents/{id}/publish` | `{published: true/false}` |
| POST | `/v1/driver-admin/documents/{id}/remove` | ta bort köruppdraget och filen |
