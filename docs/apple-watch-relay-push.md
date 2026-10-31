# Apple Watch via Cloud: relä och push

Gemensamt kontrakt för TrainMeet Server (#205, #206), TrainMeet Cloud (#90,
#91) och TrainMeet TMBox för iPhone och Apple Watch (#36, #37). Samma text
står i `docs/apple-watch-relay-push.md` i båda serverrepona. Protokollversion
**1**.

Planen är `beahead-ab/trainmeet-tmbox-ios#35`. Steg 2 är reläet, steg 3 är
push. Klockan är samma box som telefonen: samma nyckel, samma station.

## Varför det finns, och gränsen

watchOS släpper inte fram vanliga appar till ett lokalt nät, så klockan når
inte en Pi på träffen. Pi:n kan inte heller nås från internet. Därför håller
Pi:n själv en utgående förbindelse till Cloud, och Cloud lämnar klockans anrop
vidare.

Det är ett smalt undantag från D1 (Cloud #5, Server #14), beslutat av Casper
den 29 oktober: **en egen kanal bara för livetrafik från klockan**.

- Upp till Cloud går bara svaren på klockans egna anrop och push-händelser
  med de fält som står nedan. Ingen konfiguration, ingen träffdata och ingen
  historik. Förslagskön förblir borttagen.
- Cloud är ingen allmän proxy. Bara sex anrop går genom reläet, utan query,
  och bara `Authorization` och kroppen följer med.
- Pi:n förblir trafikauktoritet. Den kontrollerar nyckel, box och väg igen
  och kör samma kod som när boxen ringer direkt.
- Ingen port öppnas på träffen. Pi:n gör bara utgående HTTPS-anrop.
- Cloud sparar ingenting av trafiken. Förfrågan och svar finns i minnet tills
  klockan fått svaret eller gett upp.
- Nycklar, push-token och engångsbevis loggas aldrig, varken i Cloud eller på
  servern.

Allt är **avstängt** tills det slås på, på båda sidor:

| Var | Hur det slås på | Utan det |
|---|---|---|
| Cloud, reläet | `TRAINMEET_CLOUD_WATCH_RELAY=1` | Reläet svarar `relay_disabled` |
| Cloud, push | APNs-nyckeln i miljön (se nedan) | Push är "inte konfigurerat" |
| Servern | Inställningar → Träff och Cloud → **Apple Watch via Cloud** (`POST /v1/cloud/live`) | Inga anrop till Cloud, inga notiser |

Servern måste vara kopplad till Cloud (sexsiffrig kod) innan kanalen kan slås på.

## Översikt

```text
Klocka ──HTTPS──▶ Cloud /relay/<id>/v1/tmbox/... ──┐
                                                   │ (i minnet, tidsgräns)
Pi ──utgående HTTPS, long-poll──▶ Cloud /api/server-relay
     ◀── förfrågningar ──┘   svar i nästa utbyte ──▶

Pi ──utgående HTTPS──▶ Cloud /api/server-push ──▶ APNs ──▶ telefon och klocka
Notisens Ge klart/Neka ──▶ servern: POST /v1/tmbox/answer (direkt, via reläet eller bryggan)
```

## 1. Reläet (steg 2)

### 1.1 Anropen som går genom reläet

Exakt dessa, och inga andra. Inte `POST /v1/clock` (styr klockan och kräver
administratör), inte registreringen av nya klienter och inget av
administrationen.

| Metod | Väg | Vad |
|---|---|---|
| `GET` | `/v1/tmbox/terminal` | Boxens bild |
| `POST` | `/v1/tmbox/terminal` | Ett tryck |
| `GET` | `/v1/tmbox/terminal/timetable` | Stationens tidtabell |
| `GET` | `/v1/clock` | Träffklockan |
| `POST` | `/v1/tmbox/push` | Registrera push-token (steg 3) |
| `POST` | `/v1/tmbox/answer` | Svar från en notis (steg 3) |

Kroppar och svar är exakt desamma som när klockan eller telefonen ringer
servern direkt.

### 1.2 Klockan → Cloud

```http
GET https://cloud.trainmeet.app/relay/<relay_id>/v1/tmbox/terminal
Authorization: Bearer <boxens nyckel>
```

Basadressen är reläadressen (`https://cloud.trainmeet.app/relay/<relay_id>`);
vägen läggs till precis som mot serverns egen adress. Det betyder att
`ServerClient` kan användas oförändrad med reläadressen som `baseURL` och
samma token.

- Ingen query. `POST` har en JSON-kropp, högst 4096 byte. `GET` har ingen kropp.
- Svaret är serverns status och JSON oförändrade, med huvudet
  `X-TrainMeet-Relay: 1`.
- Tidsgränser i Cloud: 5 s för `GET`, 25 s för `POST`.
- Takt: 5 anrop/s per box (20 i en följd), 40 anrop/s per relä (120 i en
  följd), högst 32 väntande förfrågningar per relä.

Reläets egna fel har samma form som serverns, `{"error": <kod>, "message":
<text>}`:

| Status | `error` | Betyder | Vad klockan gör |
|---|---|---|---|
| 404 | `relay_disabled` | Reläet är avstängt i Cloud | Bryggan |
| 404 | `relay_unknown` | Adressen finns inte (servern bortkopplad eller flyttad till en annan träff) | Hämta adressen från telefonen igen; bryggan |
| 404 | `relay_not_allowed` | Vägen eller metoden går inte genom reläet | Programfel |
| 400 | `relay_query_not_allowed`, `relay_invalid_body` | Fel form | Programfel |
| 413 | `relay_body_too_large` | Över 4096 byte | Programfel |
| 401 | `relay_authorization_required` | Ingen `Bearer`-nyckel | Programfel |
| 429 | `relay_rate_limited` | För tätt; `Retry-After` i sekunder | Vänta |
| 503 | `relay_offline` | Pi:n är inte ansluten just nu (`Retry-After: 10`) | Bryggan direkt |
| 503 | `relay_busy` | Kön är full | Vänta, försök igen |
| 504 | `relay_timeout` | Pi:n svarade inte i tid | Se 1.5 |

Alla andra statusar och koder kommer från servern själv, till exempel 401
`invalid_credential` (boxen är borttagen) eller 403 `box_not_assigned`.

### 1.3 Servern ↔ Cloud: utbytet

Servern ringer, Cloud svarar. Inget går åt andra hållet.

```http
POST https://cloud.trainmeet.app/api/server-relay
Authorization: Bearer <serverns kopplingsnyckel>
Content-Type: application/json

{"protocol": 1, "wait": 25, "responses": [
  {"id": "r-…", "status": 200, "body": "{\"lines\": [...]}"}
]}
```

- `wait`: 0–25 sekunder. Cloud håller svaret öppet tills en förfrågan kommer
  eller tiden gått, som configens long-poll.
- `responses`: svaren på förra utbytets förfrågningar, högst 32. Bara `id`,
  `status` och `body` (serverns JSON som text, högst 512 KiB).

```json
{"protocol": 1, "relay_id": "…", "push": {"configured": true},
 "requests": [{"id": "r-…", "method": "GET", "path": "/v1/tmbox/terminal",
               "authorization": "Bearer …", "body": null, "expires_in_ms": 4800}]}
```

- Högst 16 förfrågningar per utbyte. En förfrågan lämnas ut **en gång** och
  aldrig efter sin tidsgräns. En omstartad eller återansluten Pi får alltså
  aldrig gamla trafikkommandon.
- Ett nyare utbyte för samma koppling avslutar ett äldre som väntar.
- En Pi som inte hörts av på 35 s räknas som frånkopplad (`relay_offline`).
- Fel: 401 `{"code": "link_invalid"}` kopplingen är ogiltig; 503 `{"code":
  "relay_disabled"}` reläet är avstängt i Cloud; 400 okänd version eller fel
  form. En äldre Cloud utan reläet svarar 401 eller 404 utan kod; servern visar
  då `cloud_disabled`.

Servern kör sedan förfrågan: samma vägar som ovan, samma
`identities.authenticate`, bara boxar (`esp32_panel`, alltså TMBox på
telefon och webb), samma `_require_box_access` och samma metoder som
HTTP-servern. Ett `id` som setts förut körs inte igen. Svaret skickas med i
nästa utbyte, som börjar direkt.

### 1.4 Överlämningen: hur telefonen och klockan får adressen

Telefonen frågar servern, på det vanliga sättet (direkt eller på träffens nät):

```http
GET /v1/tmbox/watch
Authorization: Bearer <boxens nyckel>
```

```json
{"protocol": 1,
 "relay": {"state": "connected", "url": "https://cloud.trainmeet.app/relay/…"},
 "push": {"state": "on", "registered": {"phone": true, "watch": false}},
 "answer_path": "/v1/tmbox/answer"}
```

`relay.state`:

| Värde | Betyder |
|---|---|
| `off` | Kanalen är avstängd på servern |
| `unlinked` | Servern är inte kopplad till Cloud |
| `connecting` | Påslagen, inte ansluten ännu |
| `connected` | Ansluten; `url` är satt |
| `cloud_disabled` | Cloud har inte reläet påslaget |
| `link_rejected` | Cloud känner inte kopplingen (bortkopplad i Cloud) |
| `error` | Cloud nåddes inte; servern försöker igen |

`url` finns bara i `connected`. En server äldre än denna svarar 404: då finns
varken relä eller push.

Telefonen lägger `relay.url` i överlämningen till klockan, bredvid
anslutningen och nyckeln (`WatchConnectionSnapshot`). Förslag: ett nytt
valfritt fält `relayURL`, så att en äldre klocka läser samma snapshot.
Telefonen frågar igen när appen öppnas, när servern byts och när `state`
ändrats, och skickar en ny snapshot om adressen ändrats eller försvunnit.

Adressen hör till serverns koppling **och** träffen. Kopplas servern om till
en annan träff får den en ny adress, och den gamla svarar `relay_unknown`.

### 1.5 Vägval i klockan (#36)

1. Serverns egen adress är publik (till exempel `server.trainmeet.app`):
   ring direkt. Reläet behövs inte.
2. Annars, om det finns en reläadress: reläet.
3. Annars, eller vid `relay_offline`, `relay_unknown`, `relay_disabled` eller
   nätfel: bryggan genom telefonen, som i dag.

Bryggan (`WatchBridgeOperation` i appen) får två nya operationer som speglar
reläets två nya vägar, med samma vakt som i dag (ingen URL, inga huvuden från
klockan, kroppen högst 4096 byte):

| Operation | Metod och väg |
|---|---|
| `push` | `POST /v1/tmbox/push` |
| `answer` | `POST /v1/tmbox/answer` |

Ett tryck (`POST /v1/tmbox/terminal`) som fått `relay_timeout` eller nätfel
har **okänt utfall**. Skicka det aldrig igen med ett nytt `command_id`. Läs
om bilden (`GET`) på vilken väg som helst; samma tryck med samma
`command_id` ger högst `duplicate` från servern, aldrig en andra åtgärd.

## 2. Push (steg 3)

### 2.1 Registrering hos servern

Telefonen och klockan registrerar var sin token för boxen. Klockan gör det
direkt, via reläet eller via bryggan.

```http
POST /v1/tmbox/push
Authorization: Bearer <boxens nyckel>

{"device": "watch", "token": "<APNs-token, hex>", "environment": "production"}
```

- `device`: `phone` eller `watch`.
- `token`: enhetens APNs-token som hex, 64–200 tecken.
- `environment`: `production` (TestFlight och App Store) eller `sandbox`
  (utvecklingsbyggen från Xcode). Följer appens `aps-environment`.
- Ta bort: samma anrop med `"token": null`.
- Svar: `{"registered": true, "device": "watch", …}` och samma fält som
  `GET /v1/tmbox/watch`.
- Fel: 400 `invalid_push_registration` eller `invalid_push_token`, 401/403 som
  för boxen.

Registrera vid varje start och när token ändras. Registreringen tas emot även
när kanalen är av; `push.state` säger om notiser skickas. En token som Apple
säger inte längre gäller tas bort av servern. En borttagen box får inga
notiser.

Bundle-id (APNs topic) väljer Cloud efter `device`:

| `device` | Bundle-id |
|---|---|
| `phone` | `se.beahead.trainmeet.tmbox` |
| `watch` | `se.beahead.trainmeet.tmbox.watchkitapp` |

### 2.2 Händelserna

Servern skickar de händelser som redan finns i boxens `attention`, till boxar
som registrerat push. Det gäller också när appen är stängd: servern tittar på
de registrerade boxarna en gång i sekunden.

| `kind` | Till | Notisen (sv) | Knappar |
|---|---|---|---|
| `incoming_request` | Mottagaren | **3510 från CDA** · Begär klartecken. Ge klart eller neka. | Ge klart, Neka |
| `request_approved` | Avsändaren | **3510 klart** · LEK gav klart. | – |
| `request_denied` | Avsändaren | **3510 nekat** · LEK nekade begäran. | – |
| `incoming_train` | Mottagaren | **3510 på väg** · Från CDA. | – |

Texten följer boxens språk (sv, en, da, nb, de; annars sv).

En händelse som inte kommit iväg på 60 s skickas inte: efter ett avbrott blir
det ingen storm av gamla notiser. APNs får försöka leverera i 5 minuter.

### 2.3 Servern → Cloud

```http
POST https://cloud.trainmeet.app/api/server-push
Authorization: Bearer <serverns kopplingsnyckel>

{"protocol": 1, "events": [{
  "event_id": "e-…", "kind": "incoming_request",
  "train_number": "3510", "from_station": "CDA", "to_station": "LEK",
  "request_id": "<förfrågans id>", "proof": "<engångsbevis>",
  "language": "sv", "expires_in": 300,
  "recipients": [{"device": "watch", "token": "…", "environment": "production"}]}]}
```

- Exakt dessa fält. Ett okänt fält är ett fel (400 `invalid_push`), inte en tystnad.
- `proof` finns bara på `incoming_request`, och då också `request_id`.
- 1–10 händelser per anrop, 1–4 mottagare per händelse.
- Svar 200: `{"push": {"configured": true}, "results": [{"event_id": "…",
  "sent": 2, "failed": 0, "invalid": ["<token_ref>"]}]}`. `token_ref` är de
  första 16 hex-tecknen av SHA-256 av token i gemener, så att Cloud kan peka ut
  en ogiltig token utan att upprepa den. `duplicate` eller `throttled` kan stå
  på en händelse.
- 503 `{"code": "push_not_configured"}`: Cloud har ingen APNs-nyckel.
- 401 `{"code": "link_invalid"}`: kopplingen är ogiltig.
- Takt i Cloud: 1200 händelser i timmen per server, 300 notiser i timmen per token.

### 2.4 Notisen från APNs

Huvuden: `apns-push-type: alert`, `apns-priority: 10`, `apns-topic` enligt
tabellen ovan, `apns-expiration` (nu + `expires_in`), `apns-collapse-id`
(`<kind>:<request_id>`, annars händelsens id).

```json
{"aps": {"alert": {"title": "3510 från CDA", "body": "Begär klartecken. Ge klart eller neka."},
         "sound": "default", "category": "TMBOX_REQUEST",
         "interruption-level": "time-sensitive", "thread-id": "tmbox"},
 "tmbox": {"v": 1, "kind": "incoming_request", "event_id": "e-…",
           "request_id": "…", "proof": "…",
           "train_number": "3510", "from_station": "CDA", "to_station": "LEK",
           "expires_at": 1730000000}}
```

Kategorierna appen registrerar (`UNNotificationCategory`):

| Kategori | Knappar (`UNNotificationAction.identifier`) |
|---|---|
| `TMBOX_REQUEST` | `TMBOX_APPROVE` "Ge klart"; `TMBOX_DENY` "Neka" (`.destructive`) |
| `TMBOX_EVENT` | inga |

Knapparna ska inte öppna appen (`options` utan `.foreground`), så att svaret
kan ges med handleden nere. Tidskänslig kräver förmågan *Time Sensitive
Notifications* i appen. `tmbox.expires_at` är Unix-sekunder; en notis efter den
tiden besvaras inte.

### 2.5 Svaret från notisen

```http
POST /v1/tmbox/answer
Authorization: Bearer <boxens nyckel>

{"request_id": "<tmbox.request_id>", "proof": "<tmbox.proof>", "answer": "approve"}
```

`answer` är `approve` (Ge klart) eller `deny` (Neka). Vägen: telefonen ringer
servern direkt; klockan ringer direkt om servern är publik, annars via
reläet, annars via bryggan (en ny bryggoperation, `answer`).

Servern avgör, med samma kommando som boxens # (`clearance.response`):

| Status | Svar | Betyder |
|---|---|---|
| 200 | `{"status": "accepted", "decision": "approved"}` | Klart givet (eller `denied`: nekad) |
| 200 | `{"status": "duplicate", "decision": "approved"}` | Redan besvarad med det här beviset, till exempel från den andra enheten. Ingen ny åtgärd; `decision` är det som gäller. |
| 409 | `request_not_waiting` | Förfrågan är redan avgjord eller återtagen |
| 409 | `assignment_changed` | Boxen har fått en annan station sedan notisen |
| 409 | `answer_rejected` | Servern tog inte emot svaret |
| 410 | `proof_expired` | Beviset har gått ut (10 minuter) eller servern har startats om |
| 403 | `proof_invalid` | Beviset hör till en annan box eller förfrågan |
| 400 | `invalid_answer` | Fel form |

Beviset är slumpat, gäller i 10 minuter, för en box och en förfrågan, och
bara en gång. Telefonen och klockan får samma bevis; den som svarar först
avgör, den andra får `duplicate`. Ett svar gäller aldrig "det som råkar stå på
boxen" utan bara förfrågan i notisen.

Efter svaret får avsändaren `request_approved` eller `request_denied`, som när
svaret ges på boxen.

## 3. Komplikationer och Smart Stack (#37)

Push uppdaterar inte en komplikation i realtid. Appen kan vid varje notis
läsa om bild och tidtabell (direkt, relä eller brygga) och be WidgetKit
ladda om sin tidslinje inom plattformens budget. Visa senast bekräftade
läge med tid.

## 4. Konfiguration

### Cloud (miljön, `/etc/trainmeet-cloud/cloud.env`)

| Variabel | Värde |
|---|---|
| `TRAINMEET_CLOUD_WATCH_RELAY` | `1` slår på reläet |
| `TRAINMEET_CLOUD_APNS_KEY_ID` | Nyckelns Key ID (10 tecken) |
| `TRAINMEET_CLOUD_APNS_TEAM_ID` | Apple-teamets Team ID (10 tecken) |
| `TRAINMEET_CLOUD_APNS_KEY` | Innehållet i `.p8`-filen, på en rad utan BEGIN/END-raderna |
| `TRAINMEET_CLOUD_APNS_KEY_FILE` | Alternativ: sökväg till `.p8`-filen |
| `TRAINMEET_CLOUD_APNS_PHONE_TOPIC` | Valfri, förval `se.beahead.trainmeet.tmbox` |
| `TRAINMEET_CLOUD_APNS_WATCH_TOPIC` | Valfri, förval `se.beahead.trainmeet.tmbox.watchkitapp` |

Nyckeln skapas i Apple Developer → Certificates, Identifiers & Profiles →
Keys, med *Apple Push Notifications service (APNs)* ikryssad. Samma nyckel
fungerar för `production` och `sandbox`. Den läggs aldrig i Git, i en issue,
i chatten eller på en Pi. Status utan hemligheter: `GET /api/system/watch`
(administratör).

Cloud signerar providertoken (ES256) själv och anropar APNs med `curl
--http2`; curl finns i Cloud-imagen. Reläets kö finns i minnet och kräver en
Cloud-process, som i dag.

### Servern

`GET /v1/cloud/live` och `POST /v1/cloud/live {"enabled": true|false}`
(administratör). Svaret säger `relay.state`, `relay.url`, `push.state`,
antal registrerade boxar och skickade notiser, utan nycklar.

`push.state`: `off`, `unlinked`, `unknown` (inte hört från Cloud ännu), `on`,
`cloud_not_configured`.

## 5. Vad som inte ingår

- Ingen konfiguration, ingen träffdata och ingen historik går upp.
- Ingen push utan internet. Då gäller bryggan och knackningen medan appen
  står framme.
- Ingen direkt väg från klockan till en privat adress eller Bonjour.
- Klareringen avgörs alltid av servern. Cloud och appen bestämmer ingenting.
