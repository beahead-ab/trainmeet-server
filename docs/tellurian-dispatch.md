# Tellurian Dispatch som klareringsauktoritet (#222)

Status 2026-10-31: **etapp 2 och 3 är byggda och verifierade mot en simulator.**
Ingenting är verifierat mot Stefans server, som inte finns publikt. Läget där
Tellurian bestämmer (etapp 4) går inte att slå på.

Ärende: [#222](https://github.com/beahead-ab/trainmeet-server/issues/222).
Ram och kommandologg: [#219](https://github.com/beahead-ab/trainmeet-server/issues/219),
[PR #221](https://github.com/beahead-ab/trainmeet-server/pull/221) och
[externa trafikintegrationer](external-integrations.md).

Källa: [tellurianinteractive/Tellurian.Trains.Dispatch](https://github.com/tellurianinteractive/Tellurian.Trains.Dispatch)
på `0a0f42a` (Release 1.1.0, 2026-01-12): `SPECIFICATION.md`, `README.md` och
`Tellurian.Trains.Dispatch/ActionStateMachine.cs`. Biblioteket är GPL-3.0.
TrainMeet länkar inte in det och kör ingen kod därifrån. Integrationen sker
över HTTP mot en separat process, enligt Stefans `SPECIFICATION.md`.

## Princip

**Tellurian bestämmer, TrainMeet kompletterar.** TrainMeet får lägga till allt
som inte ändrar trafikläget. Allt som ändrar trafikläget går genom Tellurian,
annars är det avstängt. Se tabellen i #222.

## Vad som finns

| Del | Fil | Läge |
| --- | --- | --- |
| Simulator (HATEOAS + SSE) | `src/tmbox_gateway/integrations/tellurian_sim.py` | Bara tester och manuell provkörning, binds till `127.0.0.1` |
| Adapter: adress, transport, läsning, spegling, mappning, handlingar, kommandon | `src/tmbox_gateway/integrations/tellurian.py` | Läsning används i skugga; skrivvägen finns men är inte inkopplad |
| Inställning, skugga och förhandsrapport i servern | `src/tmbox_gateway/integrations/tellurian_service.py` | Avstängd som standard |
| Inställningar → Klarering → Tellurian Dispatch | `web/index.html`, `web/tellurian-settings.js`, `translations/tellurian.txt` | Av eller Skugga |
| Raden Klarering på Drift | `web/index.html` (`#drift-clearance`), `web/tellurian-settings.js` | Bara i Skugga |
| Tester | `tests/test_tellurian_simulator.py`, `tests/test_tellurian_contract.py`, `tests/test_tellurian_settings.py`, `tests/js/tellurian-settings.test.cjs` | Körs i CI |

### Lägen

| Läge | Vad händer |
| --- | --- |
| **Av** (standard) | Ingenting läses från Tellurian. TrainMeet klarerar som i dag. |
| **Skugga** | Servern läser och speglar Tellurian bredvid sin egen klarering, utan att skriva. Administratören ser status och avvikelser under Inställningar → Klarering, och en rad på Drift (se nedan). Trafiken, boxarna och skärmarna påverkas inte. |
| **Tellurian bestämmer** | Etapp 4. Visas som "kommer när Stefans server finns" och avvisas av servern (`409 tellurian_authority_unavailable`). |

Inställningen är per träff (`runtime_settings`, nyckel `clearance_tellurian:<region>:<träff>`),
skickas aldrig till Cloud och försvinner när träffdata nollställs. Bara
EU-trafikspel kan använda den; en US-träff visar avsnittet som inte tillgängligt.

### Raden på Drift

I läget Skugga står en rad direkt under klockan på Drift: *Klarering ·
TrainMeet klarerar. Tellurian Dispatch läses bredvid.* följt av kontakten
(samma ord och färger som i Inställningar), och sedan antingen antalet
skillnader mot Tellurian eller, utan kontakt, när Tellurian senast svarade
och felkoden i klartext. Simulatorn märks "simulerad". Länken leder till
Inställningar → Klarering, där skillnaderna står var för sig. Ordet är
"skillnader" och inte "avvikelser", eftersom Drift redan använder det för
tågen.

Raden läser samma `GET /v1/clearance/tellurian` som inställningssidan: var
femte sekund i skugga, så att "ingen kontakt" (15 sekunder utan svar) syns i
tid, annars var trettionde för att märka att en annan administratör slagit på
skuggan. I läget Av, på en US-träff och när svaret inte är statusen (till
exempel 403) syns ingen rad alls. Raden har ingen knapp: **Byt till TrainMeets
klarering** hör till etapp 4, när Tellurian bestämmer.

### HTTP i TrainMeet Server (administratör)

| Väg | Vad |
| --- | --- |
| `GET /v1/clearance/tellurian` | Inställning, skuggans status och avvikelser, senaste förhandsrapport |
| `POST /v1/clearance/tellurian` | Spara `{meet_id, base_url, test_instance, mode}`; `mode` är `off` eller `shadow` |
| `POST /v1/clearance/tellurian/test` | Testa anslutning med formulärets (osparade) adress; bara läsning |

Alla tre kräver administratör. POST kräver dessutom samma ursprung, som
klockkällan. En TMBox, en app eller en oinloggad klient får 403/401.

## Säkerhet

- **Bara lokal adress:** privat IPv4 (10/8, 172.16/12, 192.168/16), link-local
  (169.254/16) eller ett `.local`-namn. HTTP är tillåtet där. Ett `.local`-namn
  måste dessutom slå upp till en lokal adress vid varje anrop, och anslutningen
  görs till just den adressen.
- **Testinstans:** en publik adress, loopback eller IPv6 kräver det uttryckliga
  valet "testinstans". `trainmeet.app` och dess underdomäner godtas aldrig, inte
  ens som testinstans.
- Bara ursprung (schema, värd, port), aldrig sökväg, inloggningsuppgifter eller
  frågesträng.
- Inga omdirigeringar (3xx är fel), ingen miljöproxy, timeout 3 s per anrop,
  storleksgräns 1 MiB per svar och 16 KiB per händelse.
- En serverlämnad `href` används bara om den är en sökväg under `/api/v1/` på
  samma ursprung. Andra länkar tas bort och visas som okända åtgärder.
- Tellurians svar och felmeddelanden loggas aldrig och visas aldrig: bara
  felkoder (`unreachable`, `timeout`, `redirect`, `too_large`, `bad_json`, …).
- Boxar, appar och webbläsare pratar aldrig med Tellurian. Allt går via
  serverns egen behörighetskontroll.

Produktgränsen (`tests/test_product_boundaries.py`, `TellurianIsALocalServiceTests`)
håller fast att Tellurian är en lokal tjänst på träffens nät och inte Cloud:
D1 gäller oförändrat, Cloud-modulerna nämner inte Tellurian, och skugga och
rapport har ingen skrivväg.

## Mappning

| TrainMeet | Tellurian | Regel i adaptern |
| --- | --- | --- |
| Station (`code`) | Dispatcher (`signature`) | Exakt matchning. Dubbletter på någon sida matchas aldrig. Skillnad bara i versaler visas som tips men räknas som omatchad. |
| Sträcka (stationspar) | Sektionernas `from`/`to` | Matchas på stationsparet. Antal passager = `totalTrackStretches − 1`. En sträcka utan tåg i Tellurian just nu visas som "utan tåg", inte som fel. |
| Tåg | `train` ("SJ IC 456"), `trainId` | 1) källans tåg-id om tågturen har `source_train_id` (planens `Train.Id`, se nedan) och det pekar ut exakt ett tåg, också när två tåg delar nummer; 2) tågnummer om det är entydigt hos båda; 3) delar två TrainMeet-tåg numret: bolag och tågslag (`service_key`, "DSB IC") mot Tellurians etikett ("DSB IC 116"); 4) annars första avgångsstation. Tvetydigt = omatchat, aldrig gissat. |
| Klocka | – | Rapporten varnar om TrainMeet inte följer FastClock. Tellurians klocka syns inte i API:t. |

### Källans id i driftpaketet

Cloud läser planeringsexporten (planeringsprogrammets JSON) och bär planens
egna id som text hela vägen till den publicerade träffconfigen
(trainmeet-cloud #112, dokumenterat i Clouds `docs/CUMULATIVE-IMPORT.md`):

| Fält i paketet | På | Ur planen |
| --- | --- | --- |
| `stations[].source_station_id` | station (eller driftplats) | `OperationLocation.Id` |
| `connections[].source_connection_id` | sträcka | `TrackStretch.Id` |
| `connections[].source_dispatch_stretch_id` | sträcka | `DispatchStretch.Id` för klareringssträckan sträckan ingår i (saknas för sidospår utan klarering) |
| `trains[].source_train_id` | tidtabellsrad | `Train.Id` |
| `services[].source_train_id` | tjänst | `Train.Id`, bara när alla tjänstens rader har samma |

Adaptern använder i dag bara `services[].source_train_id`. Stationerna matchas
fortfarande på signatur och sektionerna på stationspar; stationernas och
sträckornas id finns för när Stefans server visar sig använda samma id (fråga
12 nedan). Ett paket utan fälten (en äldre träff, eller ett underlag som inte
är planen) matchas som förut. Två tåg som delar nummer (SJ X 116 och DSB IC 116)
matchas på källans id när båda har ett eget, och annars på `service_key` mot
Tellurians etikett. Har två tjänster samma id, eller samma bolag och tågslag,
gissas inget.

## Handlingar

Vilka handlingar som visas kommer **bara** från `actions` i Tellurians svar för
stationens dispatcher. Adaptern räknar aldrig själv fram vad som är tillåtet;
den översätter bara namn till TrainMeets ord och tangenter.

| TMBox-handling | Tellurian | Tangent | Kommentar |
| --- | --- | --- | --- |
| Begär klart | `request` | `#` | |
| Ge klart | `accept` | `#` | |
| Neka | `reject` | `*` | |
| Avbryt egen begäran | `revoke` när sektionen är `Requested` | `*` | |
| Återta klart | `revoke` när sektionen är `Accepted` | `*` | |
| Avgått | `depart` | `#` | Tåget blir `Running` |
| Ankommit | `arrive` | `#` | Bara när alla passager är gjorda |
| Bemanna | `manned` | – | Krävs före första begäran |
| Passage | `pass/{index}` | – | Bara för stationen som styr platsen |
| Inställt, avbrutet, ångra | `canceled`, `aborted`, `undoTrainState` | – | Bara administratör |
| Rensa sträckan | `clear` | – | Står inte i #222. Behandlas tills vidare som administratörshandling (öppen fråga) |
| Kö (`A`) | – | `A` | Skrivs aldrig till Tellurian |
| Annat spår (`B`) | – | `B` | Döljs tills Tellurian har en åtgärd |

## Kommandon (förberett för etapp 4)

`TellurianCommands` i `tellurian.py` är skrivvägen. Den är **inte kopplad** till
TMBox, TKL, Drift eller någon HTTP-väg; testerna använder den mot simulatorn.

1. Stationen kommer från serverns inloggning. Kommandot får bara använda
   åtgärder ur den stationens dispatcher-vy. Tellurians `href` bär ingen
   dispatcher, så kontrollen kan bara göras i TrainMeets inkörsport.
2. Dispatchern läses om direkt före sändning, och POST går till den `href` som
   då lästes för just det tåget och den åtgärden.
3. Kommandot reserveras i kommandologgen (`SqliteCommandJournal` från #221) med
   scope träff + `tellurian` + anslutning + station, innan det skickas.
4. `confirmed` först när återläsningen visar effekten. Ett tappat svar ger
   `unknown`; det skickas **aldrig** om automatiskt. `reconcile` och
   `reconcile_pending` stämmer av genom läsning, också efter omstart.
5. Tellurian har inga kvitton per kommando. Avstämningen bekräftar därför att
   effekten finns, inte vem som gjorde den. En sektion i läget `Arrived` döljs
   av biblioteket ur dispatcherns vy; `arrive` bekräftas av POST-svaret plus att
   sektionen är borta, och ett tappat svar på `arrive` förblir `unknown`.

Platsen för etapp 4 är `AUTHORITY_AVAILABLE` i `tellurian_service.py`. Före
aktivering behövs: koppling av `TellurianCommands` till TMBox/TKL/Drift genom
serverns behörighetskontroll, att TrainMeets egen klareringsmotor fattar inga
beslut för träffen, paus och tydligt läge på boxar och skärmar vid avbrott inom
15 sekunder, administratörens byte till TrainMeets klarering med varning, och
spärr mot aktivering när stationer är omatchade (rapporten räknar redan fram
`activation.blockers`).

## Skuggläge i detalj

- Läsning: `GET /api/v1/dispatchers` och dispatcherns `href` (eller den
  dokumenterade `GET /api/v1/dispatchers/{id}`).
- SSE `GET /api/v1/events`. En händelse leder till ny läsning av berörda
  dispatchers (`dispatcherIds` i händelsen, annars alla). Efter en återansluten
  ström läses allt om, eftersom händelser kan ha missats.
- Bryts strömmen läses alla var tredje sekund tills den är tillbaka. Med ström
  görs en lätt läsning var femte sekund som livstecken.
- Ingen lyckad kontakt på 15 sekunder ger "ingen kontakt" med tid för senaste
  kontakt.
- Avvikelser jämför Tellurians sektioner i `Requested`/`Accepted`/`Departed`
  med TrainMeets öppna klareringsärenden (begärd/klar/avgått) per stationspar
  och tågnummer: "bara i TrainMeet", "bara i Tellurian" och "olika läge".
  TrainMeets ärenden läses direkt ur databasen, utan att ärenden löper ut.

## Simulatorn och dess antaganden

`TellurianSimulator` följer `SPECIFICATION.md` och bibliotekets regler:
tillstånden `Planned/Manned/Running/Completed/Canceled/Aborted` och
`None/Requested/Accepted/Rejected/Revoked/Departed/Arrived/Canceled`, att ett tåg
måste vara `Manned` före första begäran, att nästa sektion väntar tills
föregående har avgått, passage bara för den dispatcher som styr platsen, ett
nivås ångra, `clear` för inställda/avbrutna tåg på linjen, att `Arrived` döljs
och att listorna begränsas till tio sektioner (`StationDispatcher`).

Varje svar bär rubriken `X-Tellurian-Simulator`. Rapporter och skuggstatus
märks då "simulerad", så att de aldrig kan tas för en verifiering mot Stefans
server.

Det som specifikationen inte bestämmer är **antaganden** och ska stämmas av:

1. Sektionsfälten `from`, `to`, `trainId`, `departureTime`, `arrivalTime`.
   Specifikationen säger bara "train identification and schedule". Adaptern
   klarar sig utan dem: en avgång i en dispatchers lista går från dess station.
2. `GET /api/v1/dispatchers` som lista med `id`, `signature`, `name`, `href`.
3. Händelsernas JSON (`type`, `sectionId`, `trainId`, `train`, `dispatcherIds`,
   `previousState`, `newState`) och `event:`-namnen.
4. 409 när en åtgärd inte är tillgänglig, 404 för okänd sektion.
5. `pass/{index}`: simulatorn använder nästa spårsträckas index (1 för första
   signalen). Adaptern postar bara den `href` den fått.
6. Kapacitet: bibliotekets `ActionStateMachine` 1.1.0 filtrerar inte på kapacitet
   (`TrackStretchCapacityManager` finns men anropas inte). Simulatorn erbjuder
   `depart` bara när första blocket är fritt och inget mötande tåg finns på ett
   enkelspårsblock av samma sträcka, och `pass` bara när nästa block är fritt.
7. Motriktad trafik: bibliotekets `NextControlPoint` använder `TrackStretch.End`
   även när tåget går mot sträckans definierade riktning, vilket ser ut att ge
   fel plats (ingen `pass`). Simulatorn följer färdriktningen.
8. `Completed` sätts när sista sektionen ankommer (specifikationen säger det;
   1.1.0 gör det inte i `Execute`).

Manuell provkörning av inställningssidan: starta simulatorn med
`PYTHONPATH=src python -m tmbox_gateway.integrations.tellurian_sim`, ange
adressen den skriver ut och kryssa i Testinstans.

## Funktionsmatris (2026-10-31)

| Funktion | Simulator | Stefans server |
| --- | --- | --- |
| Läsa dispatchers och sektioner (HATEOAS) | verifierad | ej testad |
| SSE, filter `dispatcherId`, återgång till läsning vid avbrott | verifierad | ej testad |
| Skrivning med återläsning, tappat svar → `unknown`, avstämning efter omstart | verifierad | ej testad |
| Två stationer parallellt; en box kan inte agera för en annan station | verifierad | ej testad |
| Förhandsrapport (API, stationer, sträckor, tåg, klocka, läge) | verifierad | ej testad |
| Skuggläge med avvikelser, utan skrivning | verifierad | ej testad |
| Raden på Drift i skugga (kontakt, skillnader, ingen kontakt) | verifierad (webbtest) | ej testad |
| Auktoritetsläge | saknas (etapp 4) | saknas |
| Källans id från Cloud | Cloud bär id för plats, sträcka och tåg (#112); adaptern läser tågets | – |

## Frågor till Stefan

Utöver frågorna i #222 (status, version och licens för servern, testinstans,
klocka, stabila id, spårbyte och kö, om TrainMeet får nämnas):

1. Exakt form på `GET /api/v1/dispatchers` och sektionerna: finns `from`/`to`
   (signaturer), `trainId` och tider, och heter fälten så?
2. Händelsernas JSON och `event:`-namn i SSE. Finns `dispatcherIds`? Skickas
   livstecken (kommentarsrader) när det är tyst?
3. Vilka statuskoder svarar servern med när en åtgärd inte längre är tillgänglig?
4. Vad betyder `{controlPointIndex}` i `pass/{index}`?
5. Hur vet servern vilken dispatcher som gör `revoke`, när `href` saknar
   dispatcher?
6. Var kontrolleras kapaciteten i servern: filtreras `depart`/`accept` bort,
   eller avvisas POST? Spärras mötande tåg för hela enkelspårssträckan?
7. Går `pass` i motriktad trafik (se antagande 7)?
8. Hur läser man tillbaka en sektion efter `arrive`, när den försvinner ur
   dispatcherns vy? Finns `GET /api/v1/train-sections/{id}`?
9. Är listorna begränsade till tio sektioner per dispatcher i servern?
10. Sätts `Completed` när sista sektionen ankommer?
11. Ska `clear` vara en handling för stationerna eller bara för tågledningen?
12. Kan servern läsa samma planeringsexport som Cloud, och vilka id får då
    `OperationLocation`, `DispatchStretch` och `Train`?
