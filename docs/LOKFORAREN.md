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

Köruppdragen läggs in på varje server för sig, separat från Clouds paket, och
kommer från två filer med var sin uppgift:

1. **Planeringsfilen (JSON)**, samma fil ur planeringsprogrammet som går till
   Cloud, ger tjänsterna. Den läses i administratörens webbläsare
   (`lokforaren-koruppdrag.js`, `fromPlan`) och servern får färdiga tjänster i
   formen ovan. Servern tolkar ingen plan; den kontrollerar formen med
   `normalize_duty`, som för Cloud. Samma fil igen för samma träff (känns igen
   på en kontrollsumma) uppdaterar samma köruppdrag.
2. **Köruppdragens PDF** (högst 80 MB, gärna alla 600 sidorna) är förlagan.
   Den sparas oförändrad i serverns datakatalog, `lokforaren/<sha256>.pdf`
   bredvid `trainmeet.db`. Webbläsaren läser texten med pdf.js (`web/pdfjs/`,
   Apache-2.0) och hittar sidorna för varje tjänst (`matchPages`); servern får
   bara sidnumren.
3. **Gör tillgängligt för förarna.** Först då syns tjänsterna under Välj
   tjänst, och bara på träffen köruppdraget lades in för. **Dra tillbaka**
   tar bort dem ur listan; den som redan kör behåller sin tjänst.

Ur planeringsfilen (`DriverDuties`, med planeringsprogrammets `$id`/`$ref`):

| Planen | Lokföraren |
|---|---|
| `Identity` | tjänstens nummer |
| delarna (`Parts`), från anrop till anrop på ett tåg | tåg med tågslagets prefix (`G 5453`), avgång, uppehåll, genomfart och ankomst med station och spår |
| `FromTrack`/`ToTrack` på en del | anvisning om uppställningsspåret där fordonet hämtas eller lämnas |
| `Runaround`, `PushWagons`, `TurnLoco`, anropets anteckningar | Rundgång, Skjut vagnar, Vänd loket, fri text |
| andra tåg på samma station samtidigt | Möter, Kör förbi, Blir förbikörd, med tåg och tider |
| omloppens delar (`HasCoupleNote`, `HasUncoupleNote`, `DisplayUseNote`, lyft) | Koppla, Koppla loss, Använd, Lyft på, Lyft av, per veckodag |
| passagerarbyte på stationen, låsnyckel (`LockKey`) | Byt passagerare här, Inget passagerarbyte, Hämta/Lämna nyckel |
| tågslagets `DefaultPreparationMinutes`/`DefaultFinishingMinutes`, eller tjänstens egna tider | Förberedelse och Avslutning |
| tågslagets bolag, omloppets fordon (`ScheduledObjects`) | bolag och fordon |
| ett ensamt anrop (till exempel växling 08:30–11:30) | hela tiden på stationen |

Sidorna i PDF:en: varje sida ägs av tjänsten vars nummer står på den, i en
rubrik eller sidfot (`Tjänst 39 – Dagl – Sida 2`, `Tjeneste 48 – Dagl – Side 4`)
eller på planeringsprogrammets omslag (ordet Tjänst/Tjeneste och numret ensamt
på en rad). Mittsidan med tidtabellen saknar nummer och hör till tjänsten den
står i. En innehållsförteckning och sidor för tjänster som inte finns i planen
räknas inte. Utan nummer alls känns tjänsten igen på sina tågnummer. Tjänster
som inte hittas och sidor som inte nämner tjänstens tåg blir avvikelser i
granskningen. Värnamos häfte (612 sidor, 131 tjänster på 4 sidor och 11 på 8)
ger 142 av 142 utan avvikelser.

Sedan delas förlagan i en liten PDF per tjänst med pdf-lib (`web/pdflib/`,
MIT) i webbläsaren, och delarna sparas i `lokforaren/delar/<sha256>.pdf`.
Föraren får bara sin tjänsts sidor (i Värnamo ungefär 125 kB i stället för
hela häftet på 2,9 MB) och öppnar dem i telefonens egen PDF-visare. Går det
inte att dela får föraren hela PDF:en på rätt sida. Nya sidor eller en ny PDF
tar bort de gamla delarna.

Uppställningsspår: delens `FromTrack`/`ToTrack` i planen är spåret där
fordonet hämtas eller lämnas, inte avgångs- eller ankomstspåret. De blir
anvisningar ("Före avgång: hämta fordonet från spår 21 (X2 uppställning)").
Växling (tågslag med `IsShunting`) har inget tågnummer i köruppdraget och
visas med tågslagets namn, till exempel Skifteoppdrag.

### Anvisningarna på förarens språk

Anvisningarna på köruppdragets sidor ("Krysser NSB L 410 13:41–13:43",
"Ti,To,L: Koble NSB 02 Di4 til toget", "Utveksl omstigende passasjerer her")
räknas fram ur
planeringsfilen efter samma regler som planeringsprogrammet använder när det
skriver köruppdragen; ingen text läses ur PDF:en. De skickas som uppgifter,
inte som färdig text, och Lokföraren skriver dem på förarens språk (svenska,
danska, norska, engelska eller tyska). Samma köruppdrag blir alltså "Möter NSB
L 410 13:41–13:43" för en svensk förare och "Krysser NSB L 410 13:41–13:43"
för en norsk. Värnamos 142 tjänster stämmer med häftet.

- **Möten** räknas vid ankomsten: ett annat tåg på samma station när det
  egna tåget står där. Motsatt riktning är ett möte; samma riktning är att
  köra förbi eller bli förbikörd. Stationer med `HideMeets` och tåg som börjar
  eller slutar där räknas inte. Tiden är när båda står på stationen.
- **Veckodagar** (`days`, 1 = måndag) står bara när anvisningen inte gäller
  alla dagar tjänsten körs, först på raden ("Ti,To,L: …", "M–O: …"). Samma anvisning för flera
  fordon eller dagar skrivs en gång.
- Formen, en händelses `instructions` (högst 12):

```json
{"kind": "crosses", "meets": [{"train": "NSB L 410", "from": "13:41", "to": "13:43"}]}
{"kind": "couple", "vehicles": ["NSB 02 Di4"], "days": [2, 4, 6]}
{"kind": "from_parking", "train": true}
{"kind": "fetch_from_track", "track": "21", "usage": "X2 uppställning"}
{"kind": "pick_up_key", "key": "K16", "place": "Spiten"}
{"kind": "exchange_passengers"}
```

`kind` är en av `crosses`, `overtakes`, `overtaken_by`, `exchange_passengers`,
`no_exchange`, `couple`, `uncouple`, `use`, `from_parking`, `to_parking`,
`lift_on`, `lift_off`, `fetch_from_track`, `put_on_track`,
`shunt_wagons_to_departure`, `shunt_wagons_to_arrival`, `circulate`, `turn`,
`turn_and_circulate`, `push_wagons`, `pick_up_key` och `leave_key`. En okänd
sort hoppas över (`normalize_instruction` i `driver.py`), så en äldre server
visar resten. Fri text ur planen (`notes`) visas som den står.

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
- **Sekunder**: tryck på klockan i sidhuvudet eller på NU-linjens tid för att
  visa eller dölja sekunderna, eller ställ in det under Inställningar →
  Visning (**Visa sekunder**). Valet gäller alla klockor i appen och sparas i
  telefonen; sekunderna visas från början.
- Anvisningarna står under raden de hör till, så att NU-linjen aldrig går
  rakt genom texten.
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
- **Ikonen hör till kontot.** Det finns bara en ikon per konto, och samma
  ikon visas i Lokföraren, på banan och i serverns admin under Användare.
  Byter föraren ikon i Lokföraren syns den i admin direkt. Den som har admin
  byter sin egen ikon när hen redigerar sig själv under Användare, och den
  syns då i Lokföraren. Ägare och administratörer ser alla ikoner; andra ser
  bara sin egen via kontot. Utan ikon visas initialerna.
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
| GET | `/v1/driver/duties/{tjänst-id}.pdf` | tjänstens egna sidor, om köruppdraget är tillgängligt |
| GET | `/v1/driver/documents/{id}.pdf` | hela förlagan, om tjänsten saknar egna sidor |

Kontots ikon, samma som ovan, för serverns admin:

| Metod | Väg | Vad |
|---|---|---|
| GET | `/v1/account` | det egna kontot med `initials` och `avatar_url` |
| POST | `/v1/account/avatar` | `{image: "data:image/jpeg;base64,…"}` |
| POST | `/v1/account/avatar/remove` | ta bort ikonen |
| GET | `/v1/account/avatars/{user_id}` | ikonen; ägare och administratörer alla, andra sin egen |

Alla kräver en inloggad session; POST från en annan sida nekas.

För administratörer (ägare och administratörer, aldrig förare):

| Metod | Väg | Vad |
|---|---|---|
| GET | `/v1/driver-admin/overview` | förarna och vad de kör, köruppdragen |
| POST | `/v1/driver-admin/documents` | `{filename, fingerprint, name, duties, warnings}` ur planeringsfilen |
| GET | `/v1/driver-admin/documents/{id}` | köruppdraget och dess tjänster med sidor |
| POST | `/v1/driver-admin/documents/{id}/pdf` | PDF:en som den är (`Content-Type: application/pdf`, `X-Filename`) |
| GET | `/v1/driver-admin/documents/{id}.pdf` | PDF:en, för att läsa sidorna igen |
| POST | `/v1/driver-admin/documents/{id}/pages` | `{pages: {"39": [12, 15]}, total, warnings}` |
| POST | `/v1/driver-admin/documents/{id}/parts` | en tjänsts egna sidor som PDF (`X-Duty: 39`) |
| GET | `/v1/driver-admin/duties/{tjänst-id}.pdf` | en tjänsts egna sidor, för granskningen |
| POST | `/v1/driver-admin/documents/{id}/publish` | `{published: true/false}` |
| POST | `/v1/driver-admin/documents/{id}/remove` | ta bort köruppdraget och filen |
