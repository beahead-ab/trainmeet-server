# TMBox protokoll v2

Normativt kontrakt mellan en fysisk TMBox och TrainMeet Server. Kontraktet
äger topics, meddelandekuvert, revisionsregler och tillståndsmaskiner. När
koden och det här dokumentet säger olika saker är det en bugg i koden.

Produktbeskrivningen finns i
[`docs/tmbox.md`](https://github.com/beahead-ab/trainmeet-tmbox/blob/main/docs/tmbox.md)
i `trainmeet-tmbox`. Det här dokumentet konkretiserar den till trådnivå och
är den normativa källan för klienter som använder protokollet.

Kontraktet versioneras med servern. `trainmeet-tmbox` konsumerar en kopia.

## 1. Grundprinciper

**Servern äger sanningen.** Boxen skickar kompletta, idempotenta kommandon
och renderar de tillstånd servern skickar tillbaka. En tangenttryckning som
bara flyttar en markör eller skriver en siffra går aldrig på tråden.

**Boxen är tyst tills den har något komplett att säga.** Stationens
konfiguration och aktuella läge cachas i RAM och bläddras lokalt. Ingen
rundresa per tangenttryck, ingen rundresa för att slå upp ett tåg.

**All tid är träffklockan.** Aldrig väggklockan. Träffklockan kan gå i annan
hastighet och kan stoppas. Boxen har ingen egen klocka; tiden kommer i
snapshoten.

**Ingen bro mellan v1 och v2.** Protokollen kör på skilda prefix
(`tambox/v1/…` respektive `tmbox/v2/…`) så en gammal enhet aldrig råkar
tolka v2-trafik. Det finns inget krav på att samma broker servar båda
samtidigt i drift, och ingen översättning mellan dem byggs.

**Ingen händelseuppspelning.** Det finns inget `last_event_id` och ingen
uppspelning av missade händelser. Retained `assignment` + `config` +
`snapshot` vid återanslutning **är** hela synkmekanismen. Snapshoten är
sanningen; händelser är flyktiga.

## 2. Transport

MQTT över träffens lokala Mosquitto. QoS 1 genomgående. Lösenordsfritt på
träffens isolerade nät — ett medvetet val (beslut B3), inte ett förbiseende.
Kuvertet reserverar `device_token` så TLS och enhetsautentisering kan läggas
till som egen slice utan protokollbrott; fältet valideras inte idag.

Serverupptäckt sker via mDNS. Servern annonserar `_tmbox._tcp` med TXT-posten
`protocol=<version>` och `server_id=<gateway-id>`. Boxen slår upp IP direkt
(`MDNS.IP`) i stället för ett värdnamn — ett mDNS-värdnamn går ofta inte att
slå upp via vanlig DNS på ett isolerat träffnät.

## 3. Topics

`{id}` är enhetens permanenta id, härlett ur efuse-MAC och visat som en kort
kod: `TMBOX-7A42F1`.

| Riktning | Topic | QoS | Retain |
|---|---|---|---|
| box → server | `tmbox/v2/device/{id}/hello` | 1 | nej |
| server → box | `tmbox/v2/device/{id}/assignment` | 1 | **ja** |
| server → box | `tmbox/v2/device/{id}/config` | 1 | **ja** |
| box → server | `tmbox/v2/device/{id}/config/ack` | 1 | nej |
| box → server | `tmbox/v2/device/{id}/presence` | 1 | **ja** |
| box → server | `tmbox/v2/device/{id}/command` | 1 | nej |
| server → box | `tmbox/v2/device/{id}/ack` | 1 | nej |
| server → box | `tmbox/v2/device/{id}/snapshot` | 1 | **ja** |
| server → alla | `tmbox/v2/gateway/{gateway_id}/status` | 1 | **ja** |

Snapshot-topicet är per **enhet**, inte per panel — enheten är stationsbunden,
inte panelbunden. `config` skiljer statisk stationskonfiguration från dynamisk
trafikstatus så de kan uppdateras oberoende av varandra.

Boxen sätter last will på sitt `presence`-topic så en box som tappar strömmen
annonseras som offline utan att någon behöver fråga. Gatewayn gör samma sak på
sitt statustopic.

## 4. Meddelandekuvert

### 4.1 Kommando (box → server)

```json
{
  "protocol_version": 2,
  "message_id": "TMBOX-7A42F1-9f31c2a4",
  "device_id": "TMBOX-7A42F1",
  "device_token": null,
  "station_id": "st-cda",
  "action": "clearance.request",
  "expected_revision": { "scope": "movement", "key": "movement-101-a", "value": 3 },
  "payload": { "clearance_id": "clr-88f2", "connection_id": "connection-a-b" }
}
```

`sent_at` och `expires_at` stämplas av **gatewayn**, inte av boxen. En ESP32
har ingen tillförlitlig klocka vid start, och servern har redan den logiken för
v1. En box ska därför inte skicka tidsstämplar; gör den det ignoreras de.

`expected_revision` anger **exakt en** scope och nyckel (§5). Kommandon som
inte rör ett befintligt tillstånd — `device.hello`, `train.lookup` — utelämnar
fältet.

### 4.2 Kvittens (server → box, på `.../ack`)

```json
{
  "protocol_version": 2,
  "message_id": "TMBOX-7A42F1-9f31c2a4",
  "status": "accepted",
  "reason": null,
  "revision": { "scope": "movement", "key": "movement-101-a", "value": 4 },
  "snapshot": { "…": "samma innehåll som snapshot-topicet" }
}
```

`status` ∈ `accepted | rejected | duplicate`. Snapshoten bifogas **alltid**, så
boxen aldrig behöver en extra rundresa för att se utfallet av sitt eget
kommando.

Avslagsorsaker (`reason`) som en klient måste kunna hantera:
`stale_revision`, `expired_command`, `unknown_action`, `unknown_movement`,
`unknown_track`, `track_occupied`, `not_assigned`, `station_mismatch`,
`clearance_not_pending`, `clearance_expired`.

`track_occupied` betyder att spåret redan är tilldelat en rörelse som inte
avgått, på samma station och dag (`docs/tmbox.md` §8). En rad utan avgångstid
avgår aldrig och håller sitt spår resten av dagen. Jämförelsen görs mot det
effektiva spåret — `actualTrack` om någon flyttat tåget, annars tidtabellens.

En box som får ett avslag visar orsaken och **väntar på nästa snapshot**. Den
agerar aldrig på den gamla cachade datan igen.

Ett läsande kommando — idag bara `train.lookup` — får dessutom ett
`result`-block i kvittensen. Skrivande kommandon har inget `result`; deras
utfall syns i den bifogade snapshoten.

### 4.3 Retained `config` (server → box)

Ändras bara vid ny driftpaket-aktivering. Ersätts i sin helhet vid varje
mottagning — ingen delta-logik.

```json
{
  "protocol_version": 2,
  "config_version": 12,
  "station": { "id": "st-cda", "code": "CDA", "name": "Charlottendal" },
  "tracks": [
    { "id": "track-cda-1a", "display_label": "1A", "sort_order": 10 }
  ],
  "connections": [
    { "connection_id": "connection-a-b", "other_station_code": "VST",
      "track_type": "single", "dispatch_mode": "clearance", "display_row": 1 }
  ],
  "display": { "rows": 4, "cols": 20, "charset": "ascii" }
}
```

`tracks` är ett utdrag ur spårkatalogen för den tilldelade stationen, i den
ordning spårväljaren ska visa dem. `connections` är stationens
topologikonfiguration: vilka grannar som visas på vilken rad. Den ersätter v1:s
fasta A–D-slots.

### 4.4 Retained `snapshot` (server → box)

Ändras vid varje operativ händelse som rör stationen. Ersätts i sin helhet.

```json
{
  "protocol_version": 2,
  "revision": {
    "config_version": 12,
    "movements": { "movement-101-a": 4 },
    "cases": { "clr-88f2": 2 }
  },
  "movements": [
    { "id": "movement-101-a", "train_number": "101",
      "arrival_time": null, "departure_time": "09:20",
      "departure": "positioned", "arrival": "none",
      "assignedTrackId": "track-cda-1a", "actualTrack": null,
      "crewReady": true }
  ],
  "active_clearances": [
    { "clearance_id": "clr-88f2", "movement_id": "movement-101-a",
      "connection_id": "connection-a-b", "status": "waiting",
      "from_station_id": "st-cda", "to_station_id": "st-vst" }
  ],
  "line_messages": [],
  "clock": { "time": "09:23", "running": true }
}
```

Fältnamnen blandar `snake_case` och `camelCase`. Det är avsiktligt och följer
scopen: `crewReady`, `assignedTrackId` och `actualTrack` har samma namn som i
TKL-lagrets befintliga API, så samma rörelseobjekt kan passera mellan klienter
utan omskrivning.

`clock` är träffklockan. `running: false` betyder stoppad träffklocka, inte
förlorad kontakt.

## 5. Revision och idempotens

Tre skilda revisionsutrymmen, inte en global räknare. En global revision skalar
inte: en orelaterad händelse vid en annan station skulle ge falska
`stale_revision`-avslag.

| Scope | Nyckel | Ökar vid |
|---|---|---|
| `movement` | `movement_id` | uppställt, förare, spårbyte, avgång, ankomst för den rörelsen |
| `case` | `clearance_id` eller linjen-ledig-meddelandets id | begär, svara, avbryt, revidera |
| `config` | stationen | ny driftpaket-aktivering, ny topologi eller spårkatalog |

Servern avvisar med `stale_revision` när värdet inte matchar — per nyckel, inte
globalt.

**Idempotens.** `message_id` cachas per enhet, oavsett scope. Ett återskickat
`message_id` ger samma svar utan ny sidoeffekt. Om ett kommando hann skickas
men svaret tappades återanvänder boxen samma `message_id` för att fråga efter
utfallet — aldrig för att skapa ett nytt beslut.

## 6. Kommandokatalog

| `action` | Innebörd | Scope i `expected_revision` |
|---|---|---|
| `device.hello` | Presenterar enheten, publiceras på eget topic | — |
| `device.presence` | Online/offline, publiceras på eget topic | — |
| `device.config.ack` | Kvitterar mottagen config, publiceras på eget topic | `config` |
| `train.lookup` | Slår upp ett tågnummer vid stationen | — |
| `train.position.set` | Tåg uppställt (TKL) | `movement` |
| `train.crew_ready.set` | Lokförare på plats (TKL) | `movement` |
| `train.track.change` | Byter tilldelat spår | `movement` |
| `train.departed` | Tåget har avgått | `movement` |
| `train.arrived` | Tåget har ankommit | `movement` |
| `train.approaching` | Tåget närmar sig, observerat men ej inne | `movement` |
| `clearance.request` | Begär klarering | `movement` |
| `clearance.response` | Svarar på en begäran | `case` |
| `clearance.cancel` | Avbryter egen begäran | `case` |
| `line.available.publish` | Skickar linjen-ledig | `movement` |
| `line.available.acknowledge` | Kvitterar visning av linjen-ledig | `case` |

`movements[].allowed_actions` säger vad servern accepterar för rörelsen just
nu. Firmware härleder samma sak ur sin cache för att välja knappetiketter,
men beslutet är alltid serverns: en knapp som ser tillåten ut men avvisas
visar orsaken och väntar på nästa snapshot.

`clearance.approved`, `clearance.rejected`, `clearance.expired` och
`clearance.revised` är **händelser**, inte kommandon. De når boxen genom
snapshoten, inte via egna topics.

`state.sync` finns inte som kommando. Retained `assignment` + `config` +
`snapshot` vid varje anslutning **är** state.sync.

## 7. Tillståndsmaskiner

### 7.1 Klarering

```text
waiting
  → approved | rejected           mottagarens clearance.response
  → cancelled                     avsändarens clearance.cancel
  → expired                       TTL passerad utan svar
  → invalidated_by_revision       spårbyte eller omdirigering under waiting
```

TTL kontrolleras **lat vid varje request och response**. Korrektheten får
aldrig bero på att ett bakgrundsjobb hunnit köra. En stoppad träffklocka
stoppar också TTL:en — en rast får inte låta öppna begäranden förfalla.

En enkelspårsförbindelse har en delad kanal, `connection_id`. En
dubbelspårsförbindelse har en oberoende kanal per riktning,
`{connection_id}:{from_station_id}`, så motriktade rörelser aldrig blockerar
varandra. Kanalen modelleras som två kanaler, inte som flaggor på en.

`approved` avgör ärendet men frigör inte linjen. Kanalen hålls tills tåget är
inne: mottagarstationens `train.arrived` för samma tågnummer frigör den. Ett
`rejected`, `cancelled`, `expired` eller `invalidated_by_revision` frigör den
direkt.

### 7.2 Linjen är ledig

```text
delivered_to_device → display_acknowledged
```

Ett ensidigt meddelande, aldrig en fråga. Det beläggningskontrolleras aldrig
mot en klareringsbegäran — det är inget beslut, bara information. Det
modelleras som en egen statusrad, inte som ett skenbart klareringsärende med
bara en part. Mottagaren kan bara kvittera att meddelandet visats; det finns
inget godkänn och inget neka att ge det.

### 7.3 Uppställt, förare och härledd REDO

TKL deklarerar två saker via `train.position.set` och `train.crew_ready.set`.
Båda är trafikrelevant lägesinformation och lagras beständigt på servern med
samma omstartsgaranti som `positioned` alltid har haft.

**`REDO` går inte att sätta.** Servern härleder det ur `positioned &&
crew_ready` plus sina egna regler och exponerar utfallet i `movements[]
.departure` samt i `movements[].allowed_actions`. En klient som ändå skickar
`ready` säger i praktiken båda deklarationerna, och båda registreras.

Rangerarnas `train_readiness`-flöde är ett **eget** flöde med egna roller och
egen historik, och slås aldrig ihop med detta (beslut B2).

### 7.3 Anslutning

```text
boot → wifi_connecting → discovering → connecting → waiting_for_assignment
     → ready → reconnecting → state_resync → ready
```

`state_resync` betyder konkret: vänta på retained `assignment`, `config` och
`snapshot`. De får anlända i **valfri ordning**.

## 8. Säkerhetsgrammatik

`#` får aldrig lämna ett operativt beslut. `KLART`, `EJ KLART`, `AVGÅTT` och
`ANKOMMIT` bekräftas alltid via `A` eller `B`. `#` betyder välj, OK, bekräfta
data, kvittera visning.

Regeln gäller synkront i motorn och i alla klienter — box, webbsimulator,
Swift, TKL-terminal. Ingen klient får implementera ett undantag på egen hand.
`B`-etiketten är alltid **`B=EJ`**.

## 8.1 Spårbarhet

Ett kommandos `message_id` är också dess korrelations-id. Loggrader,
auditjournalen och trafikmotorns egen post bär samma id, så hela vägen från
mottaget MQTT-meddelande till registrerad effekt går att hämta med en fråga.

Loggen är strukturerad `nyckel=värde`. Hemligheter — parkopplingskoder,
åtkomsttokens, lösenord, `device_token` — redigeras bort på fältnamn innan
något skrivs, så en slarvig anropare kan inte läcka en genom att skicka den
till en loggrad.

## 9. Scheman och exempel

`schemas/` innehåller ett JSON Schema per meddelandetyp. `examples/` innehåller
kompletta payloads för de två referenskonfigurationerna enligt beslut B5:

- **Charlottendal** (`charlottendal-*.json`) — den verkliga
  integrationsreferensen, med driftplatserna `C` och `RBG`. Allt ska bevisas
  fungera mot data som TrainMeet Cloud faktiskt kan publicera.
- **Cda/Lek/Vst/Kun** (`fiktiv-*.json`) — konstruerad enhetstestfixtur där
  topologin behöver vara kontrollerad, bland annat för dubbelspårskanaler.

`tests/test_protocol_contract.py` validerar varje exempel mot sitt schema, så
de två aldrig kan glida isär.

## 10. Vad firmwaren ännu inte skickar

Firmwaren i `trainmeet-tmbox` implementerar anslutningsdelen av v2 men inte
hela kommandosidan. Servern ska vara tolerant mot det som saknas och aldrig
avvisa ett i övrigt giltigt meddelande för fältens skull:

- `hello` bär idag `device_code`, `model` och `firmware_version`, men ännu inte
  `hardware_version`, `protocol_version` eller `display`. Utan `display` antas
  16×2 och `ascii`.
- Kommandon bär ännu inte `expected_revision`. Ett kommando utan fältet
  behandlas som optimistiskt och accepteras om tillståndet tillåter det. Ett
  kommando **med** fältet villkoras strikt, per scope och nyckel.
- Boxen sätter ännu ingen last will på sitt `presence`-topic.

Detta är en lista över kända luckor att stänga, inte en tillåten permanent
avvikelse.
