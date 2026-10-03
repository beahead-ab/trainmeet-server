# TMBox 16×2-profilen

Normativ text för hur en TMBox med 16×2-display och 4×4-knappsats talar med
TrainMeet Server, från firmware 0.7.0 och Server 1.10.0. Säger koden och den
här texten olika saker är det en bugg i koden. `tests/test_terminal16_protocol_doc.py`
jämför tabellen *Värden* nedan med konstanterna i Servern.

Sedan Server 2.0.0 är 16×2-profilen det enda protokollet över MQTT; `tambox/v1` och `tmbox/v2` är borttagna. Innehållet i v2 (kommandon, lägen, revisionsregler) gäller webbläsarboxarna över HTTP och beskrivs i [`../v2/README.md`](../v2/README.md).

## 1. Grundprinciper

- Servern är enda trafikauktoritet. Boxen visar det Servern skickar och
  skickar tillbaka tangenttryck. Den namnger aldrig station, mottagare eller
  åtgärd; ett kommando med `action`, `station_id` eller `connection_id` avvisas.
- Servern tilldelar station och sida. Boxen kan inte välja själv.
- I den här profilen kvitterar **och verkställer** `#` den åtgärd som bilden
  visar, till exempel *Begär klartecken* eller *Ge klart*. Regeln i v2-profilens
  §8, att `#` aldrig får lämna ett operativt beslut, gäller inte här: varje
  tangent i bilden bär `acts`, och klienten spärrar handlande tangenter en kort
  stund efter ett skärmbyte (§6).

## 2. Transport

- MQTT mot Serverns broker. Klient-id = enhets-id, till exempel
  `esp8266-308398b57200`. Ren session, keepalive 10 s, ingen inloggning.
- QoS 1 åt båda håll. **Inget retain**: båda sidor ignorerar sparade meddelanden.
- Boxen hittar Servern med DNS-SD-tjänsten `_tmbox._tcp`, TXT `protocol=…`
  (högsta protokoll Servern talar) och `server_id=…`. Den minns `server_id` och visar *FLERA SERVRAR* om flera svarar.

## 3. Ämnen

| Riktning | Ämne | Innehåll |
| --- | --- | --- |
| box → server | `tmbox/terminal/device/<id>/hello` | ny anslutning: kod, modell, version, `boot` |
| box → server | `tmbox/terminal/device/<id>/presence` | livstecken med `nonce` |
| box → server | `tmbox/terminal/device/<id>/command` | ett tangenttryck |
| server → box | `tmbox/terminal/device/<id>/alive` | svar på livstecknet, samma `nonce` |
| server → box | `tmbox/terminal/device/<id>/frame` | ny bild när något ändrats |
| server → box | `tmbox/terminal/device/<id>/ack` | svar på ett kommando, med ny bild |

Exempel, fångade från mosquitto 2026-10-01 (bilden förkortad):

```text
…/hello     {"device_code": "TBX-B57200", "model": "NodeMCU ESP8266 16x2", "firmware_version": "0.7.1", "hardware_version": "server-16x2", "boot": "7cd2916f535b49d2-1"}
…/presence  {"nonce": "7cd2916f535b49d2-1-p1", "boot": "7cd2916f535b49d2-1"}
…/alive     {"boot": "7cd2916f535b49d2-1", "nonce": "7cd2916f535b49d2-1-p1"}
…/command   {"command_id": "7cd2916f535b49d2-1-2", "view_token": "dfe2bb9d4d9a68fe8e5991e3", "key": "#", "train_number": "428", "entry_context": "…", "boot": "7cd2916f535b49d2-1"}
…/ack       {"status": "accepted", "message": "", "command_id": "7cd2916f535b49d2-1-2", "boot": "7cd2916f535b49d2-1", "frame": {…}}
```

## 4. Session och boot

- Varje anslutning får ett nytt `boot`. Alla meddelanden bär det.
- Servern svarar på `presence` direkt, före sin arbetskö. Ett `presence` från en
  session Servern inte känner får **tystnad**; då ger boxen upp och skickar ett
  nytt `hello`.
- Servern glömmer en tyst session efter `SESSION_IDLE_SECONDS` och håller högst
  `MAX_SESSIONS` samtidigt. Större meddelanden än `MAX_PAYLOAD_BYTES` kastas.

## 5. Kommandon

- `command_id` är unikt per `boot` (`<boot>-<n>`). Samma id med samma innehåll
  ger `duplicate` och utförs aldrig två gånger; samma id med annat innehåll
  avvisas.
- `view_token` binder trycket till bilden det gjordes på. En gammal token ger
  `rejected` med en ny bild.
- Ett tågnummer skickas med `#` som `train_number` (1–5 siffror) och
  `entry_context` ur bilden. Siffrorna stannar i boxen tills dess.
- Är tåget en egen avgång med fri sträcka begär samma tryck klartecken, eller
  reserverar på en direktsträcka (Server 2.1.0). `93#` räcker alltså, och `*`
  återtar förfrågan tills mottagaren har svarat. Allt annat som numret hittar
  (en förfrågan att svara på, ett tåg att ta emot, ett besked) väntar på sin
  egen tangent. Går förfrågan inte att skicka blir svaret `rejected` med
  skälet, och bilden visar tåget med *Begär klartecken*.
- Systemet följer spelet i stället för att stoppa det (Server 2.1.0). Klartecken
  krävs för avgång, men inte att tågets ankomst är registrerad: *Rapportera
  avgång* finns så fort klartecknet finns. Har tåget inte setts komma, hoppar det
  fram till stationen med avgången. Varje tidigare del av rutten som står öppen
  avslutas: en väntande förfrågan återtas, ett klartecken släpps så att sträckan
  blir fri, och avgången där räknas som gjord.
- En ankomst kan alltid tas emot. Ett tåg som ingen skickat visas med numret och
  i tidtabellen med *Placera på spår*, och `B` väljer annat spår. Ett tåg med
  klartecken men utan rapporterad avgång placeras via `B` och spårvalet, så att
  `#` efter *Ge klart* aldrig tar emot tåget. Tåget hoppar fram på samma sätt.
- Ett upptaget spår stoppar inte en ankomst. Kvittot blir `<nr> UPPT SPÅR` i
  stället för `<nr> MOTTAGET`.
- Inga besked väntar på `#OK` (Server 2.4.0). Ett besked som `<nr> ÅTERTAGET`,
  `<nr> NEKAT`, `<nr> ANK SP2`, `INGET TÅG` eller `INGA FRÅGOR` har den andra
  stationens kod på rad 2 och försvinner efter `NOTICE_SECONDS`, och boxen går
  då till startskärmen av sig själv. `#` och `*` stänger det direkt men gör
  inget annat. En fråga som väntar på ett beslut (`#Ja *Nej`) står kvar.
- Tar avsändaren tillbaka en förfrågan eller ett klartecken får båda
  stationerna `<nr> ÅTERTAGET`: avsändaren med mottagarens kod, mottagaren med
  avsändarens. Mottagaren står alltså inte kvar på `INGA FRÅGOR` eller med
  *Placera på spår* för ett tåg som aldrig gick. En box som håller på med ett
  annat tåg störs inte. En ny förfrågan ersätter `INGA FRÅGOR` direkt.
- `status` är `accepted`, `rejected` eller `duplicate`. `message` är en text att
  visa, på boxens språk.

## 6. Bilden

- `profile` = `server-16x2`, `rows` = 2, `cols` = 16, `lines` = två rader text.
- `lcd` = färdiga tecken för HD44780 (`hd44780-5x8-cgram-v1`), högst 8 egna
  tecken (`glyphs`).
- `keys` = de tangenter som gör något just nu: `{label, short, acts}`. `acts` är sant
  när tangenten ändrar trafiken. `short` är ett ord på högst tolv tecken som en
  telefon skriver under tangenten (KÖ 1, GE KLART, AVGÅTT); boxen bortser från det. Efter ett skärmbyte väntar klienten
  `input_guard_ms` innan en tangent med `acts` skickas; bläddring svarar direkt.
- `entry` = inmatningen av tågnummer: `context`, `max_length`, `row`, `column`,
  `commit` (`#`), `cancel` (`*`), `erase` (`B`), `shortcut` (`A`),
  `labels` och `short` för samma fyra tangenter under inmatning.

## 7. Tidsgränser i boxen (firmware)

Provas i `trainmeet-tmbox`, inte här:

- `presence` var 5:e sekund.
- Ingen kontakt efter 15 s utan `alive` eller `frame`: boxen kopplar ner och hälsar om.
- *VANTAR PA SVAR* efter 1,5 s, *INGET SVAR* efter 30 s; siffrorna ligger kvar.

## 8. Onlineläge i Servern

Klienter visar *Online* högst `DEVICE_ONLINE_SECONDS` efter senaste meddelandet
från en känd session, *Ingen kontakt* därefter och *Offline* efter
`DEVICE_OFFLINE_SECONDS`.

## 9. Över HTTP: webben och iPhone

Webbklienten `/tmbox/` och iPhone-appen TrainMeet TMBox använder samma bild och
samma tangentregler över HTTP i stället för MQTT. De får ingen egen trafiklogik.

- `POST /v1/browser-clients` med `{"workspace": "tmbox"}` registrerar en klient
  och svarar med `client_id`, `device_code` och `access_token`. iPhone skickar
  även `"client": "ios"` och `"app_version"`; den får då en kod `IOS-XXXXXX` och
  modellen *TMBox · iPhone* i Klienter. Rättigheterna är desamma som en webbox:
  ingen station förrän administratören tilldelar den.
- `GET /v1/tmbox/terminal` hämtar bilden, `POST /v1/tmbox/terminal` skickar ett
  tangenttryck med `command_id`, `view_token`, `key` och vid inmatning
  `train_number` och `entry_context`, precis som §5.
- `GET /v1/tmbox/terminal/timetable` hämtar stationens tidtabell för boxens
  station och sida: `station {code, name}`, `side`, `clock`, `revision` och
  `rows`. Varje rad har `movement_id`, `train_number`, `kind`
  (`departure`/`arrival`), `time`, `station {code, name}` (andra änden),
  `side`, `track`, `state` och `selected`. `state` är `planned`, `requested`,
  `cleared`, `departed` eller `arrived`. Läsningen ger ingen trafikrätt; utan
  station är `rows` tom.
- DNS-SD-posten `_tmbox._tcp` pekar på MQTT-porten. TXT `http=<port>` anger
  webbporten, så att iPhone hittar samma server på träffens nät.

## Värden

| Namn | Värde |
| --- | --- |
| Ämnesprefix | `tmbox/terminal/device/` |
| `MAX_SESSIONS` | 256 |
| `SESSION_IDLE_SECONDS` | 45 |
| `MAX_PAYLOAD_BYTES` | 4096 |
| `input_guard_ms` | 500 |
| `NOTICE_SECONDS` | 3 |
| `entry.max_length` | 5 |
| `DEVICE_ONLINE_SECONDS` | 20 |
| `DEVICE_OFFLINE_SECONDS` | 900 |
