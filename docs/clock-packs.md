# Klockpaket: egna urtavlor

En egen urtavla laddas upp till servern som ett **klockpaket** (`.tmclock`)
under Inställningar → Skärmar och klocka → Egna klockor. Klockan blir då en
stil bland de andra. Den kan väljas för alla klockskärmar, för en enskild
skärm via dess verktygsrad och i deltagarvyn. Den gäller för alla träffar på
servern.

Ett klockpaket innehåller bilder och inställningar, men ingen kod. Tavlan och
visarna är bilder, och hur visarna går står i `clock.json`. Därför kan ett
uppladdat paket aldrig köra något i en webbläsare.

Flera paket kan laddas upp på en gång: välj flera filer, eller ladda upp en
zip-fil som innehåller flera `.tmclock`.

## Inbyggda klockor och tidigare stilar

Sedan version 3.19 har servern två inbyggda klockor: en generisk analog och en
digital. Övriga tavlor är klockpaket:

- stationsuret;
- de nationella tavlorna (Svensk, Norsk, Dansk med flera);
- den schweiziska.

En träff eller ett äldre Cloud-paket kan fortfarande ange en av de tidigare
inbyggda stilarna, till exempel `stationsur`, `swedish` eller `swiss`. Den
visar då klockpaketet med samma id när det är uppladdat, och annars den analoga
klockan. Den schweiziska har id `sbb`, och de andra har sina gamla stilnamn som
id.

## Rätten att använda tavlan

Den som laddar upp intygar att den har rätt att använda tavlan. Det gäller
till exempel en egen formgivning, eller en tavla som är licensierad av den som
äger formgivningen. Intyget loggas tillsammans med namn och tid. TrainMeet har
inga licensbelagda tavlor inbyggda.

## Innehåll

```
mitt-ur.tmclock (zip)
├── clock.json     vad paketet heter, vilka lager det har och hur visarna går
├── dial.svg       tavlan: allt som står still (krävs)
├── hour.svg       timvisaren (krävs)
├── minute.svg     minutvisaren (krävs)
├── second.svg     sekundvisaren (valfri)
└── top.svg        något ovanpå visarna, till exempel navet eller ett glas (valfri)
```

Lagren kan heta vad som helst, eftersom `clock.json` anger filerna.

**Bilderna:**
- Bilderna ska vara SVG eller PNG.
- **Alla lager ritas på samma kvadratiska yta.** Tavlans mitt är den punkt
  visarna vrids kring. Ett bra mått är `viewBox="0 0 200 200"`, med mitten i
  `100,100`.
- **Varje visare ritas pekande rakt upp mot 12,** på samma yta som tavlan.
  Skärmen vrider den.
- Visarna kan ha en svans bakåt förbi mitten.
- En PNG ska vara kvadratisk och mellan 64 och 4 096 px i sida.

**Gränser:**
- högst 2 MB för hela paketet;
- högst 1 MB per lager;
- högst 20 egna klockor per server.

## clock.json

```json
{
  "format": 1,
  "id": "mitt-ur",
  "name": "Mitt ur",
  "version": "1.0",
  "author": "Föreningen",
  "layers": {
    "dial": "dial.svg",
    "hour": "hour.svg",
    "minute": "minute.svg",
    "second": "second.svg",
    "top": "top.svg"
  },
  "motion": {
    "hour": "minute",
    "minute": "jump",
    "minute_bounce": true,
    "second": "sweep",
    "sweep_seconds": 58.5
  }
}
```

| Fält | |
|---|---|
| `format` | Alltid `1`. |
| `id` | Små bokstäver a–z, siffror och bindestreck, högst 40 tecken. Saknas fältet görs id av namnet. **Ett paket med samma id ersätter det som redan finns.** Så byter du version utan att skärmarna behöver välja om. |
| `name` | Namnet i listan och på stilknappen. Högst 60 tecken. |
| `version`, `author` | Valfria. |
| `layers` | Filen för varje lager. `dial`, `hour` och `minute` krävs. |
| `motion` | Hur visarna går, se nedan. Utelämnat betyder att alla visare glider jämnt. |

### Hur visarna går

| Inställning | Värden | |
|---|---|---|
| `hour` | `smooth` (förval) eller `minute` | `minute`: timvisaren flyttar sig en gång i minuten. |
| `minute` | `smooth` (förval) eller `jump` | `jump`: minutvisaren står still och hoppar vid varje hel minut, som på ett elektriskt stationsur. |
| `minute_bounce` | `false` (förval) eller `true` | Med `jump`: minutvisaren studsar en knapp grad efter hoppet. |
| `second` | `smooth` (förval), `tick` eller `sweep` | `tick`: ett steg i sekunden. `sweep`: sekundvisaren gör varvet på `sweep_seconds` sekunder och väntar sedan vid 12 tills minuten är full. |
| `sweep_seconds` | 30–60, förval 58,5 | Gäller bara med `sweep`. |

## Vad en SVG får innehålla

Servern godtar bara SVG-filer som ritar. Följande nekas, med en förklaring:

- `<script>`, `<foreignObject>`, `<iframe>`, `<object>`, `<embed>`, ljud, video och `<a>`;
- animationer (`<animate>`, `<set>` med flera). Visarna rör sig ändå;
- händelseattribut som `onload`;
- länkar till andra filer, både i `href` och i `url(…)` och `@import`. Bara
  interna referenser (`#namn`) och inbäddade bilder (`data:image/png;base64,…`)
  är tillåtna;
- `<!DOCTYPE>` och `<!ENTITY>`.

En vanlig SVG från Inkscape, Illustrator eller Figma går igenom. Exportera som
"vanlig SVG" eller "optimerad SVG" och bädda in eventuella bilder.

Skärmarna visar lagren som bilder (`<image>`), inte som en del av sidan.
Öppnas ett lager direkt i webbläsaren får det en sandlåda utan skript.

## Så gör du en egen klocka

1. Börja från exemplet: "Ladda ner exempelpaketet" under Egna klockor, eller
   ```
   python -m tmbox_gateway.clock_pack example mitt-ur/
   ```
2. Rita tavlan och visarna i ett ritprogram, på samma kvadratiska yta. Lägg
   varje visare i ett eget lager och exportera lagren som var sin SVG.
3. Ändra `clock.json`, alltså namn, id och hur visarna går.
4. Kontrollera paketet med samma regler som servern använder:
   ```
   python -m tmbox_gateway.clock_pack check mitt-ur/
   ```
5. Packa det:
   ```
   python -m tmbox_gateway.clock_pack build mitt-ur/ -o mitt-ur.tmclock
   ```
   En zip-fil som du packar själv fungerar också, även med mappen inuti, som
   när man komprimerar en mapp i Finder.
6. Ladda upp, kryssa i rätten att använda och välj klockan som stil. Ladda
   upp igen med samma `id` för att se en ändring.

## API

| | |
|---|---|
| `GET /v1/clock-faces` | Admin. Klockorna med uppladdare, tid och intyg. |
| `POST /v1/clock-faces` | Admin. `{file_name, data (base64), rights_confirmed: true}`. `data` är ett klockpaket eller en zip med flera, och svaret har dem i `uploaded`. Ett fel i ett av paketen stoppar alla. Ryms inte alla paket under taket på 20 klockor laddas inget upp. |
| `POST /v1/clock-faces/delete` | Admin. `{id}`. En skärm som visar klockan byter till den analoga klockan. |
| `GET /v1/clock-faces/<id>/<sha16>/<lager>` | Öppen, som `/v1/display`. Lagret sparas länge, eftersom adressen byts med varje ny version. |
| `GET /v1/clock-faces/exempelur.tmclock` | Exempelpaketet. |

`/v1/display` och `/v1/clock` har klockorna i `clock.faces`, med stil, namn,
lager och gång, men inte vem som laddade upp dem. En uppladdad klockas stil är
`custom:<id>` i `clock.available_styles`.
