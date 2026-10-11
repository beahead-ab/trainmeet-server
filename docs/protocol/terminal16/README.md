# TMBox 16×2-profilen

Normativ text för hur en TMBox med 16×2-display och 4×4-knappsats talar med
TrainMeet Server, från firmware 0.7.0 och Server 1.10.0. Från Server 4.5.0 och
firmware 0.8.0 gäller samma profil för en box med 20×4-display (§3 och §6) och
Servern kan tala om för boxen när den ska ge ljud (§6a). Säger koden och den
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
| box → server | `tmbox/terminal/device/<id>/hello` | ny anslutning: kod, modell, version, `display`, `boot` |
| box → server | `tmbox/terminal/device/<id>/presence` | livstecken med `nonce` |
| box → server | `tmbox/terminal/device/<id>/command` | ett tangenttryck |
| server → box | `tmbox/terminal/device/<id>/alive` | svar på livstecknet, samma `nonce` |
| server → box | `tmbox/terminal/device/<id>/frame` | ny bild när något ändrats |
| server → box | `tmbox/terminal/device/<id>/ack` | svar på ett kommando, med ny bild |

Exempel, fångade från mosquitto 2026-10-01 (bilden förkortad):

```text
…/hello     {"device_code": "TBX-B57200", "model": "NodeMCU ESP8266 16x2", "firmware_version": "0.7.1", "hardware_version": "server-16x2", "boot": "7cd2916f535b49d2-1"}
…/hello     {"device_code": "TBX-B57200", "model": "NodeMCU ESP8266 PCF8574", "firmware_version": "0.8.0", "hardware_version": "server-16x2", "display": {"rows": 4, "cols": 20}, "boot": "7cd2916f535b49d2-1"}
…/presence  {"nonce": "7cd2916f535b49d2-1-p1", "boot": "7cd2916f535b49d2-1"}
…/alive     {"boot": "7cd2916f535b49d2-1", "nonce": "7cd2916f535b49d2-1-p1"}
…/command   {"command_id": "7cd2916f535b49d2-1-2", "view_token": "dfe2bb9d4d9a68fe8e5991e3", "key": "#", "train_number": "428", "entry_context": "…", "boot": "7cd2916f535b49d2-1"}
…/ack       {"status": "accepted", "message": "", "command_id": "7cd2916f535b49d2-1-2", "boot": "7cd2916f535b49d2-1", "frame": {…}}
```

`display` är boxens storlek: `{"rows": 2, "cols": 16}` eller `{"rows": 4, "cols": 20}`.
Boxen väljer den själv, i WiFi-portalen, eftersom en sådan skärm inte kan säga hur
stor den är. En box som inte skickar `display`, eller skickar något annat, räknas
som 16×2; den stängs aldrig ute. Administratören ser storleken under Klienter.

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
- Ett genomgående tåg som inte har kommit till stationen i systemet ger först
  *Flytta hit* (`FLYTTA 93 HIT?`), också när det kunde skickas vidare. Först
  när tåget står här gäller numret avgången (Server 3.24, Benny #170). Före det
  begärde `93#` nästa station direkt, och tåget hoppade fram med avgången.
- Är tåget en egen avgång med fri sträcka begär samma tryck klartecken, eller
  reserverar på en direktsträcka (Server 2.1.0). `93#` räcker alltså, och `*`
  återtar förfrågan tills mottagaren har svarat. Allt annat som numret hittar
  (en förfrågan att svara på, ett tåg att ta emot, ett besked) väntar på sin
  egen tangent. Går förfrågan inte att skicka blir svaret `rejected` med
  skälet, och bilden visar tåget med *Begär klartecken*.
- Systemet följer spelet i stället för att stoppa det (Server 2.1.0). Klartecken
  krävs för avgång, men inte att tågets ankomst är registrerad: *Rapportera
  avgång* finns så fort klartecknet finns. Har tåget inte setts komma (avgången
  vald i listan), hoppar det fram till stationen med avgången. Varje tidigare del av rutten som står öppen
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

- `profile` = `server-16x2`. `rows` och `cols` är boxens storlek, 2 × 16 eller
  4 × 20; `lines` är lika många rader text, alla exakt `cols` tecken långa.
- På en **16×2** är rad 1 tåget och läget med tecken (`?` begärd, `<` `>` klart,
  `◀` `▶` på väg), kön eller frågan, och rad 2 tangenterna och klockan. En box
  utan något tåg på gång står på startbilden med **stationens namn centrerat på
  rad 1** (förkortat till stationskoden om namnet inte ryms), så att man ser att
  boxen står på rätt station.
- På en **20×4** är rad 1 och rad 4 exakt vad 16×2:ans rad 1 och 2 säger, med
  klockan längst till höger. Rad 2 och 3 är nya. På ett valt tåg är rad 2 *Till*
  eller *Från* och den andra stationens namn med planerad tid; rad 3 är läget i ord
  (*Ej begärd*, *Väntar svar*, *Klar att avgå*, *Avgått* för en egen avgång; *Ej
  skickad*, *Vill skicka*, *Klarerad*, *På väg hit* för en ankomst hit) och planerat
  spår här (*Sp 1*). Ryms inte ordet framför står bara namnet; bara ett namn längre
  än raden kortas. På startbilden är rad 2 och 3 de två närmaste tågen som inte
  redan står på rad 1: nummer, `AVG` eller `ANK`, den andra stationens kod och tiden.
  Ett besked (MOTTAGET, NEKAT, ÅTERTAGET, ANK SP2 …) har den andra stationens namn
  på rad 2 och en tom rad 3, så att beskedet läses för sig. Inmatningen har tomma
  rad 2 och 3; siffrorna ligger på rad 1, kolumn 6–10, som på 16×2.
- En skärm har bara åtta platser för egna tecken. Behövs fler i en bild, eller ett
  tecken boxen inte kan rita, står rad 2 och 3 tomma i just den bilden; rad 1 och 4
  visas alltid.
- `lcd` = färdiga tecken för HD44780 (`hd44780-5x8-cgram-v1`), högst 8 egna
  tecken (`glyphs`).
- `keys` = de tangenter som gör något just nu: `{label, short, acts}`. `acts` är sant
  när tangenten ändrar trafiken. `short` är ett ord på högst tolv tecken som en
  telefon skriver under tangenten (KÖ 1, GE KLART, AVGÅTT); boxen bortser från det. Efter ett skärmbyte väntar klienten
  `input_guard_ms` innan en tangent med `acts` skickas; bläddring svarar direkt.
- `attention` = vad boxen ska ge ljud för, se §6a.
- `entry` = inmatningen av tågnummer: `context`, `max_length`, `row`, `column`,
  `commit` (`#`), `cancel` (`*`), `erase` (`B`), `shortcut` (`A`),
  `labels` och `short` för samma fyra tangenter under inmatning.

## 6a. Ljud

Servern talar om *vad* boxen ska ge ljud för; boxen bestämmer toner och spelar dem
på sin summer. Varje bild bär

```text
"attention": {"epoch": "<serverns serie>", "seq": 7, "kind": "incoming_request"}
```

- `kind` är `incoming_request` (en begäran hit), `request_denied` (någon nekade din
  begäran), `request_approved` (någon gav klart på din begäran), `incoming_train` (ett
  tåg är på väg hit) eller `null`. Ordningen är också vikten: den första är viktigast.
  Boxens egna `ConnectionLost` och `ConnectionRestored` kommer från boxen själv.
- Bara förändringar är nyheter. Det första `attention` en box får efter `hello` är
  utgångsläget och är **tyst**; därefter spelas en ton när `epoch` är densamma och
  `seq` har ändrats till ett värde utom 0 med ett `kind`. Ett annat `epoch` (Servern
  har startat om, eller ny träff eller dag) är ett nytt utgångsläge, aldrig ett ljud.
  Ett ljud från före anslutningen spelas aldrig i efterhand.
- Riktningen avgör vem som hör: begäran hör mottagaren, svaret hör den som frågade och
  avgången hör mottagaren. Den som själv gjorde det hör ingenting. ÅTERTAGET, ankomst
  och besked ger inget ljud.
- Två ljud inom `ATTENTION_WINDOW_SECONDS` är ett tillfälle: boxen får det viktigaste,
  inte båda ovanpå varandra. `seq` räknas upp i en serie för alla boxar och återanvänds aldrig.

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
| `ATTENTION_WINDOW_SECONDS` | 2 |
| `DEVICE_ONLINE_SECONDS` | 20 |
| `DEVICE_OFFLINE_SECONDS` | 900 |
