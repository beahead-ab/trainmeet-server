# Cloud och server: ansvar och ändringsrätt

Normativt beslutsdokument för relationen mellan TrainMeet Cloud och TrainMeet
Server. Besluten D1–D6 nedan ersätter motsvarande formuleringar i README och i
`trainmeet-cloud`. När koden och det här dokumentet säger olika saker är det en
bugg i koden.

Boxens sida av kedjan är [`docs/protocol/v2/`](protocol/v2/README.md). Det här
dokumentet handlar om våningen ovanför: vem som får ändra en träffs
konfiguration, och hur ändringen rör sig.

## Utgångspunkt

En träff byggs i Cloud och körs på en server. Två förhållanden avgör
arkitekturen:

**Varje träff är unik.** Det finns ingen anledning att bära ändringar vidare
till nästa träff. Ingenting behöver alltså ta sig tillbaka uppströms, och ingen
historik behöver överleva träffen.

**Servern måste klara sig utan internet.** Normalfallet är att servern är
uppkopplad och att allt arbete sker i Cloud. Undantaget är att servern inte
lyckas nå Cloud — då ska träffen ändå kunna köras helt offline, på den
konfiguration som trycktes ner före träffen, och det som behöver rättas under
dagen ska gå att rätta lokalt.

Undantaget är sällsynt men får inte vara omöjligt. Det är det enda skälet till
att servern över huvud taget kan redigera en konfiguration.

## D1. Flödet är enkelriktat

Cloud → server. Aldrig tillbaka.

Servern föreslår inga ändringar uppströms, och Cloud tar inte emot några.
Förslagskön är **borttagen ur båda repona** — det här är alltså inte längre
ett beslut som väntar på att genomföras, utan en beskrivning av hur det ser
ut. Det som togs bort:

- **Server:** tabellen `cloud_change_outbox` med index, `queue_cloud_changes`,
  `pending_cloud_changes`, `mark_cloud_changes_sent`,
  `pending_cloud_change_count`, `push_change_proposals` i `central_sync.py`,
  `_local_configuration_changes` och `push_cloud_changes` i `http_server.py`,
  endpointen `POST /v1/cloud/changes`, samt `change_sender`-injektionen.
- **Cloud:** tabellen `change_proposals`, `receive_change_proposals`,
  `list_change_proposals`, `review_change_proposal`, endpointen
  `/config/proposals`, godkänn- och avslå-endpointsen, och vyn
  **Förbättringsförslag** i admin-UI:t.

Nedströmsvägen — sexsiffrig koppling, hämtning, auto-synk var femtonde sekund,
uttrycklig aktivering — är oförändrad. `cloud_auto_sync` styr enbart hämtning;
namnet till trots skickar den ingenting.

Två negativa tester håller fast frånvaron, eftersom en frånvaro är precis vad
som växer tillbaka i tysthet: `test_the_store_offers_no_way_to_send_anything_upstream`
kontrollerar att varken metoderna eller tabellen finns kvar, och
`test_there_is_no_route_for_sending_anything_back_to_cloud` kontrollerar
tråden — att `POST /v1/cloud/changes` svarar 404.

Befintliga databaser behåller en tom, föräldralös `cloud_change_outbox`-tabell.
Den migreras inte bort: ingen kod rör den längre, och att skriva i en träffs
databas för kosmetikans skull är inte värt risken.

## D2. Servern redigerar genom att producera en ny revision

En publicering är oföränderlig. Det ändras inte. Det som saknades var en väg
att producera ett nytt paket lokalt.

En lokal redigering skapar därför en **ny revision av paketet**, lagrad och
aktiverad genom exakt samma maskineri som en Cloud-publicering:

```text
publication-hht-2026-a              hämtad från Cloud, aktiverad
publication-hht-2026-a+local-r1     tre tider rättade
publication-hht-2026-a+local-r2     ett tåg inställt
```

Motorn, boxarna, `config_version`, TKL-terminalen och skärmvyerna ser bara "ett
paket". Ingenting nedströms behöver veta att revisionen kom lokalt.

Alternativet — ett redigeringslager vid sidan av paketet, upplöst vid läsning —
avvisas. Det skulle kräva upplösning i tre läsvägar (`RuntimePublication
.timetable`, `display_snapshot`, `snapshot_payload`), hantering av
föräldralösa poster, och en skillnad mellan Clouds och serverns rader som
måste bäras överallt. Revisioner ger samma förmåga utan något av det.

Konsekvenser:

- Serverns redigerbara arbetskopia (`local_config.py`, idag 406 rader) utökas
  med `trains` och `tracks`, och får en väg som sår den från ett hämtat
  Cloud-paket.
- De två parallella världarna — "Cloud-paket" och "lokal konfiguration med
  `trains: []`" — slås ihop till en. Det är den enskilt största
  förvaltningsvinsten i det här beslutet.
- Rader som servern skapar myntar id i egen namnrymd, `local-`, så en
  framtida Cloud-publicering aldrig kan krocka med dem.

## D3. Redigering hör till ett driftläge, inte till en behörighet

Servern har två driftlägen, och redigering är öppen i exakt ett av dem:

| Läge | Cloud | Redigering på servern |
|---|---|---|
| `cloud-linked` | nås, är redaktör | **låst** |
| `offline-meet` | nås inte, eller är medvetet frånkopplad | **öppen** |

Så länge servern når Cloud är Cloud redaktör, och servern vägrar lokala
ändringar. Det är regeln, inte en rekommendation.

**Övergången är uttrycklig, aldrig automatisk.** Servern som är
Cloud-kopplad och tappar kontakten visar att Cloud inte kan nås och erbjuder
att ta träffen offline — men flyttar sig inte själv. En administratör som vet
att nätet ska försvinna kan gå till `offline-meet` i förväg, medan nätet
fortfarande fungerar.

Skälet till att inte läsa nätstatus per anrop: nätstatus är sällan binärt. En
regel som avgörs av ett live-svar skulle låsa upp och låsa igen under en
störning, mitt i någons redigering. Läget är i stället klibbigt: när det väl
är satt står det kvar tills en människa ändrar det, oavsett vad nätet gör
under tiden.

## D4. Återgång till `cloud-linked` kastar de lokala revisionerna

Att gå tillbaka till Cloud-styrning innebär att Clouds publicering gäller igen.
De lokala revisionerna kastas.

Det får aldrig ske tyst. Innan övergången — och innan aktivering av en hämtad
Cloud-version på en server i `offline-meet` — visas vad som kastas: antal
revisioner och vilka rader som ändrats, lagts till eller tagits bort.

Det här är ett acceptabelt beslut just för att varje träff är unik. Det som
kastas var aldrig avsett att leva vidare.

## D5. Spårkatalogen: Cloud äger ursprunget, servern äger driften

Cloud myntar spårens identitet vid import och publicering (schema 3). Servern
får därutöver:

- lägga till ett spår som Cloud inte publicerat, i `local-`-namnrymden,
- inaktivera ett spår som inte går att använda under träffen,
- och upprätthålla **beläggning**: ett spår kan bara vara tilldelat en
  icke-avgången rörelse per station och dag.

Beläggningskontrollen är körtidslogik, inte konfiguration, och gäller i båda
driftlägena. Skärmen `SPAR 1A UPPTAGET` finns i protokollet och i flödesbilden
men saknar serverstöd; det stödet hör hit.

## D6. Ingen historik lämnar träffen

Ingen synk uppströms (D1), ingen export av lokal historik, och inga
historikbaserade förslag mellan träffar.

Konsekvens för TMBox-scopen: `track_preferences` i `docs/tmbox.md` §8 —
historikbaserade spårförslag rangordnade tilldelat → tidtabell → historik →
stationsdefault — **utgår**. Historik mellan träffar är meningslös när varje
träff är unik. Rangordningen tilldelat → tidtabell → stationsdefault står kvar.

## Vad som inte ändras

Protokoll v2 mot boxarna, trafikmotorn, klareringsaggregatet, träffklockan och
allt annat körtidstillstånd berörs inte. En box vet inte, och ska inte veta,
om konfigurationen den fick kom från Cloud eller från en lokal revision — den
ser en `config_version` som ökade, och läser om.

Spårkatalogen som sådan (schema 3) är en förutsättning för D5, inte något som
ändras av besluten här.
