# Train Meet US — första vertikala leveransen

## Verifierad inventering, 2026-09-18

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
3. Välj **Import US package**. Välj `examples/us-twc-training.json` från detta
   repo. Läs övningsbanan, bekräfta testprofilen och välj **Start US session**.
   De två parallella spåren är avsiktligt fiktiva och oberoende.
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

Cloud-editor/publicering av US-paket, offline-identitetskontrakt, verifierade
verkliga banor/regelprofiler, historisk TT&TO, avancerade villkor/ersättningar,
returarkiv till Cloud och full fysisk terminaltest. Dessa ingår inte i den
första vertikala pilotleveransen.
