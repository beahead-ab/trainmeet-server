# TMBox 16×2-profilen

Normativ text för hur en TMBox med 16×2-display och 4×4-knappsats talar med
TrainMeet Server, från firmware 0.7.0 och Server 1.10.0. Säger koden och den
här texten olika saker är det en bugg i koden. `tests/test_terminal16_protocol_doc.py`
jämför tabellen *Värden* nedan med konstanterna i Servern.

Den äldre v2-profilen beskrivs i [`../v2/README.md`](../v2/README.md).

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
- `status` är `accepted`, `rejected` eller `duplicate`. `message` är en text att
  visa, på boxens språk.

## 6. Bilden

- `profile` = `server-16x2`, `rows` = 2, `cols` = 16, `lines` = två rader text.
- `lcd` = färdiga tecken för HD44780 (`hd44780-5x8-cgram-v1`), högst 8 egna
  tecken (`glyphs`).
- `keys` = de tangenter som gör något just nu: `{label, acts}`. `acts` är sant
  när tangenten ändrar trafiken. Efter ett skärmbyte väntar klienten
  `input_guard_ms` innan en tangent med `acts` skickas; bläddring svarar direkt.
- `entry` = inmatningen av tågnummer: `context`, `max_length`, `row`, `column`,
  `commit` (`#`), `cancel` (`*`), `erase` (`B`), `shortcut` (`A`).

## 7. Tidsgränser i boxen (firmware)

Provas i `trainmeet-tmbox`, inte här:

- `presence` var 5:e sekund.
- Ingen kontakt efter 15 s utan `alive` eller `frame`: boxen kopplar ner och hälsar om.
- *VANTAR PA SVAR* efter 1,5 s, *INGET SVAR* efter 30 s; siffrorna ligger kvar.

## 8. Onlineläge i Servern

Klienter visar *Online* högst `DEVICE_ONLINE_SECONDS` efter senaste meddelandet
från en känd session, *Ingen kontakt* därefter och *Offline* efter
`DEVICE_OFFLINE_SECONDS`.

## Värden

| Namn | Värde |
| --- | --- |
| Ämnesprefix | `tmbox/terminal/device/` |
| `MAX_SESSIONS` | 256 |
| `SESSION_IDLE_SECONDS` | 45 |
| `MAX_PAYLOAD_BYTES` | 4096 |
| `input_guard_ms` | 500 |
| `entry.max_length` | 5 |
| `DEVICE_ONLINE_SECONDS` | 20 |
| `DEVICE_OFFLINE_SECONDS` | 900 |
