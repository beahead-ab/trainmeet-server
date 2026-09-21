# Train Meet US — första vertikala leveransen

## Cloud → lokal US-körning

Cloud bygger och publicerar config; den lokala Servern ensam äger körning,
klocka, Conductor-tilldelning och track warrants. Servern representerar en
vald träff. EU och US har separata trafikmotorer men kan inte vara aktiva
samtidigt. Se [aktuell Cloud/server-modell](CLOUD-ONLY-SERVER.md).

1. Publicera en granskad **US-träff** i Cloud och hämta dess sexsiffriga kod.
2. Öppna **Inställningar → Cloud-koppling** på den lokala Servern.
3. Använd `https://cloud.trainmeet.app/config` (eller din egen Config-server)
   och skriv in koden. Config sparas och valideras lokalt; ingen körning
   startar och ingen omstart krävs. Byte från en annan träff bekräftas separat.
4. Välj arbetsytan **Dispatcher**. Granska spårsegment, MP-gränser, tåg/tidtabell och källinstruktioner i
   **Review US package**. Bekräfta testprofilen och välj **Start US session**.
   En redan pågående session måste avslutas först.
5. **US clock** använder Clouds föreslagna tid/hastighet men börjar pausad.
   Dispatcher väljer när den ska gå. Klockan sparas lokalt och fortsätter
   efter serveromstart om den lämnades igång; stoppa den före en planerad paus.
6. Anslut och tilldela Conductor enligt driftstegen nedan. All fortsatt drift
   fungerar utan internet, inklusive start av serverns valda hämtade config.

Ny publicerad config kontrolleras automatiskt och aktiveras när den kan
bevara pågående drift. Öppna warrants och andra konflikter gör att den väntar.
**Sök configuppdatering** under Inställningar använder samma säkra kedja.
Paketarkivet och historiken bevaras, men gamla paket kan inte väljas som en
andra parallell träff. Lokal JSON-import och separata US-Cloud-kopplingar är borttagna.

Första installationens vanliga **hämta config** känner också igen US-paket
och leder vidare till arbetsytesväljaren. Lokalt administratörskonto krävs precis
som för EU; Cloud-inloggningar och lösenord kopieras aldrig.

Clouds dispatcher-distrikt och källinstruktioner visas som **planeringsunderlag**.
Distriktsbegränsade behörigheter, Train Token-överlämning och historisk TT&TO
är inte implementerade i denna profil. Den stöder en tidtabellsdag per session.
Äldre redan startade pilotsessioner behåller sin tidigare klockkälla tills
dispatcher uttryckligen ställer **US clock**; en uppdatering flyttar inte deras tid.

Tekniskt: `us_packages` sparar oföränderliga paket med SHA-256-kontrollsumma;
Cloud-koppling och beständig träffspärr delas med EU. Samma publicerings-ID
med annat innehåll avvisas. Start är revisions-/idempotensskyddad och låser
in en kopia. Kopplingstoken skickas aldrig till webbläsaren. Befintlig backup
omfattar tabellerna och serverns träffnollställning rensar även dessa.

## Fristående mileposts i runtime v2

Server stöder både `trainmeet.us.runtime/1` + `tm-us-twc-manual-v1` och
`trainmeet.us.runtime/2` + `tm-us-twc-manual-v2`. Uppdatera Server före publicering
av v2 i Cloud. Gamla utkast, publiceringar och körningar uppgraderas inte automatiskt.
Cloud-utkastets profil heter `tm-us-planning-v2`; den profilen är inte ett driftpaket.

V2 behåller fyra oberoende kataloger: `mp_systems`, `mileposts`, `locations` och
`limits`. Spårpunktens `mp` är inte tillåten i v2. `nodes` + `segments` anger
verkliga förbindelser; samma MP-värde eller X/Y skapar aldrig en förbindelse.
Platser kan omfatta flera spårpunkter. Tidtabellen måste ha en sammanhängande
spårväg genom samtliga platser; en plats får inte fungera som teleport mellan spår.

Körbesked väljer hela, ordnade segment med explicit riktning:

```json
{"segment_id": "crossover", "from_node": "main-1-switch", "to_node": "main-2-switch"}
```

Det fungerar även för växelförbindelser med samma MP-tal och för explicit anslutna
Territories. Konflikter kontrolleras atomiskt mot samma segment, gemensamma
ändpunkter och gemensamma `conflict_resources`. En geometrisk korsning skapar
inte en förbindelse: deklarera en konfliktresurs när korsande spår delar utrymme.
**Delsträckor angivna med numeriska MP-intervall ingår inte i v2.** Behövs en
inre tillståndsgräns ska den först vara en uttrycklig spårpunkt som delar segmentet.

Dispatcher väljer **From point / To point**, inte ett gissat MP-intervall.
Kartans MP-, plats- och gränsmarkörer samt den utfällbara referenskatalogen
hålls åtskilda från körbesked. Kartan är schematisk, inte en avståndsskala.
Positionsrapporter väljer segment och exakt `node_id` eller tillhörande
`milepost_id`. En rapporterad position varken aktiverar eller frigör ett körbesked.

Referensnamn och instruktioner översätts inte; gränssnittet har fem språk.
Utfärdad körbeskedstext lagras oföränderlig. En Cloud-uppdatering som ändrar
spår, MP-betydelser, platser, gränser eller profil väntar medan öppna körbesked
eller rapporterade positioner berörs. Rena X/Y-ändringar får tillämpas utan att
återställa trafik, klocka eller historik.

Planeringsgränser är **inte behörigheter**. V2 inför inte territoriella
Dispatcher-roller, automatisk överlämning, NFC, fler dygn eller andra regelverk.
Den befintliga TWC-kedjan och serverbehörigheten gäller oförändrade.
V1 använder fortsatt sina äldre MP-intervall.

Kontraktet verifieras med samma `tests/us_runtime_v2_package.json` i Cloud och
Server samt negativa tester för tvetydiga referenser och parallella spår.

## Ursprunglig inventering, före Cloud 1.2.0

- Server: `beahead-ab/trainmeet-server`, bas `f9a7d6b` (1.4.2). Isolerad
  worktree/branch `codex/trainmeet-us-twc`. Leveransen omfattar denna pilot,
  inte hela den framtida US-produktens regelprofiler och Cloud-editor.
- Cloud: `beahead-ab/trainmeet-cloud`, lokal main `f17d06b` (1.0.12), ren.
  Cloud har Python/SQLite, React/TypeScript, roller/inbjudningar, filimport,
  versionslagrade publiceringar och serverkopplingar. Det är inte Supabase.
- Server har Python HTTP, lokal SQLite/WAL, administratörssessioner och
  parkopplade klienter. Trafikmotor/MQTT är EU-specifika. TKL levereras lokalt
  och hämtar serverläge med polling. US använder inte EU-klarering.
- Cloud publicerar idag EU-schema v3 (`cloud/domain.py:build_runtime_package`).
  Mileposts, warrants och sessionsbundna US-körningar saknas. EU:s format ska
  därför inte utökas genom att döpa om stationsklareringar till warrants.
- Inloggningarna i Cloud och Server är separata. Verifierad återanvändning är
  lokal admin-session, klientregister och parkoppling, inte befintlig SSO.
- Samma `trainmeet.db` används för nya `us_*`-tabeller: befintlig SQLite-backup
  omfattar dem. US-start påverkar inte EU:s aktiva publicering eller motor.

## Genomförandeplan

1. Egen US-domän och schema `trainmeet.us.runtime/1`, med strikt validering.
2. Beständig session som fryser paketet; unikt ID per faktisk körning.
3. Serverauktoritativ TWC-kedja, versionskontroll, idempotens och händelselogg.
4. Dispatcher: alla tåg / oberoende scrollbar linje / warrantkort och formulär.
5. Conductor: server-tilldelad körning, kvittens/readback och rapportering.
6. Tester för parallellspår, gemensamma resurser, samtidighet, behörighet,
   omstart och EU-regressioner. Lokal visuell verifiering på bred och smal skärm.

## Avgränsad regelprofil

`tm-us-twc-manual-v1` är en uttrycklig **modelljärnvägs-testprofil**, inte ett
påstående om efterlevnad av ATSF/SP/GCOR eller en verklig järnvägsregelbok.

Utkast → överfört → mottaget → readback rapporterad → dispatcher bekräftar
och aktiverar → conductor begär frigivning → dispatcher avslutar.
Mottaget/readback rapporterad ger inte authority. Frigivning väntande blockerar
fortfarande konflikter. Position påverkar aldrig warrantets livscykel.
Ej aktiverade warrants kan makuleras. Aktiv authority kan inte makuleras tyst.

Spårvägen är ordnade segmentintervall, inte bara ett spårnamn. Konfliktkontroll
sker i samma SQLite-transaktion som aktivering. Intervall på samma segment
inklusive gemensamma gränser blockerar varandra; deklarerade konfliktresurser
reserveras konservativt för hela berörda segmentet. Separata parallellspår utan
gemensamma resurser får samtidiga warrants. Fritext är endast kompletterande
information och kontrolleras inte maskinellt. Särregler för tillåten överlappning,
villkorad aktivering, ersättning av aktiv authority och TT&TO är senare steg.

Bifogade ATSF/SP-bilder är referenser. Testbanan är avsiktligt fiktiv och laddas
bara efter aktivt val; inga MP-tal eller spårantaganden från bilderna importeras.

## Prova från början till slut

1. Installera/uppdatera Server enligt README och skapa administratör vid första
   installationen. Använd en separat testserver för övningen.
2. Öppna **US Dispatcher** eller `http://SERVER:8787/us/dispatcher` och logga in
   med den lokala serverns administratör. På HTTPS-installationer används
   förstås serverns vanliga HTTPS-adress utan porttillägg.
3. Koppla en publicerad US-övningsträff från Cloud enligt stegen ovan.
   Granska den valda configen, bekräfta testprofilen och välj **Start US session**.
   `examples/us-twc-training.json` är en utvecklingsfixture, inte en importväg i Serverns gränssnitt.
4. Välj **Connect conductor**. Öppna samma servers `/us/conductor` på telefonen,
   ange namn och engångskoden. Markera Training 101 hos dispatcher och välj
   **Assign**. Conductor får inte själv byta till ett annat tåg.
5. Välj **+ Draft**, **Proceed**, Main 1, MP 10 till MP 20. Granska texten och
   spara utkastet. **Transmit** gör det tillgängligt för conductor men ger
   ännu inget körtillstånd.
6. Conductor väljer **Acknowledge receipt**, läser tillbaka den exakta texten
   via radio/telefon och väljer **Report readback**. Dispatcher kontrollerar
   återläsningen och väljer **Verify readback & activate**. Först nu visas
   **In effect**. Servern kontrollerar konflikter i samma transaktion.
7. Använd **Report** för en verkligt observerad position. Tid eller position
   flyttar aldrig tåget automatiskt och frigör aldrig dess tillstånd.
8. När hela tåget lämnat gränserna väljer conductor **Report clear of limits**.
   Gränserna förblir reserverade tills dispatcher väljer **Confirm release**.
9. **Finish session** fungerar först när alla warrants är avslutade/makulerade.
   Historiken sparas och en ny session får nya körnings-ID:n.

I piloten väljs sträcka/MP i ett uttryckligt formulär. Dragmarkering i kartan
från designskissen är ännu inte kopplad till driftens kommandon. Ett kartstreck
eller en tidtabell ska aldrig uppfattas som ett utfärdat körtillstånd.

## Identitet, lokal drift och återanslutning

Dispatcher använder serverns befintliga administratörskonto. Conductor är en
ny begränsad klienttyp, parkopplad lokalt. Dispatcher tilldelar körningen;
klienten kan inte välja behörighet själv. Den får inga EU-paneler/adminrättigheter.
Cloud-lösenord kopieras inte. Gemensam identitet via signerade offline-grants
behöver ett separat beslutat Cloud/Server-kontrakt och byggs inte genom en ny
parallell lösenordsdatabas.

UI, font och ikon levereras från lokal server. Inga CDN eller Cloud-anrop under
spel. Polling ger senast bekräftad revision. Vid avbrott markeras läget som
inaktuellt och knappar spärras. Osäkert kommandoresultat klarläggs med en
read-only statusfråga, aldrig genom automatisk omsändning eller offlinekö.
Om servern ännu saknar ett bekräftat resultat för ett osäkert kommando kvarstår
spärren. Fortsätt inte genom att rensa webbläsarens lagring: kontrollera
serverhistoriken och utred avbrottet först. Automatisk återhämtning av ett
bevisat aldrig mottaget kommando är en kvarvarande begränsning i piloten.

## Kvar efter första kedjan

Distriktsbehörigheter och Train Token-överlämning, offline-identitetskontrakt, verifierade
verkliga banor/regelprofiler, historisk TT&TO, avancerade villkor/ersättningar,
returarkiv till Cloud och full fysisk terminaltest. Dessa ingår inte i den
första vertikala pilotleveransen.

## Val av EU eller US

Överst i Server finns **EU-tågträff / US-tågträff**. US öppnar Dispatcher,
med Conductor som egen vy. EU återgår till den befintliga servervyn med TKL.
Länkarna öppnas i samma flik och ändrar inte sessioner, klocka eller tillstånd.
En inloggning/parkoppling krävs fortfarande enligt respektive vys behörigheter.
US behåller engelska som förvalt språk, separat från EU:s språkval.
