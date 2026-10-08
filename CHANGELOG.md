# Ändringar i TrainMeet Server

Versionsnumret sätts automatiskt vid merge till main. Se
[docs/VERSIONING.md](docs/VERSIONING.md) för hur nivån bestäms.

## Nästa version

### Dygnsskiftet en timme före första tåget, och Tidsmaskinen spolar dygn

Efter Caspers önskan.

- **Dygnsskiftet räknas ut**: en timme före den nya dagens första
  tågrörelse (avgång eller ankomst), aldrig före midnatt; utan tåg den dagen
  05:00. Går första tåget 06:20 sker skiftet 05:20. Under Inställningar →
  Träff och Cloud → Träffens dagar visar fältet den uträknade tiden; en fast
  tid går fortfarande att skriva in, och ett tomt fält är automatiskt igen.
- **Tidsmaskinen** har knapparna −1 dygn, +1 dygn och Nästa dygnsskifte (den
  nya dagen när den börjar). De fyller i dag och tid; Hoppa dit bekräftar.
  Daglistan går två veckor framåt och växer när man stegar längre.

### Dygnsskiftet kl. 05:00: alla tåg på sin utgångspunkt, med en toast i vyerna

Efter Caspers önskan: dygnsskiftet görs en timme innan trafikdygnet börjar,
inte vid midnatt, så att nattåg och sena tåg hinner in.

- **Skiftet sker kl. 05:00** (Inställningar → Träff och Cloud → Träffens
  dagar → Dygnsskifte kl., 00:00–11:59). Mellan midnatt och skiftet är det
  kvar gårdagens trafikdygn. Klockan går vidare hela tiden.
- **Vid skiftet** går träffen till nästa dag med den dagens tidtabell
  (inklusive Dagl), alla ankomst- och avgångsstatusar nollställs och varje
  tåg ställs på sin utgångspunkt, första stationen och avgångsspåret. Det
  gäller även ett tåg vars första avgång ligger före skiftet; det kan gå
  direkt.
- **Ett tåg som fortfarande är ute på linjen** kör klart på gårdagens dag:
  skiftet väntar på det, högst en halvtimme (förut sex timmar efter
  midnatt).
- **En toast** på Drift, i deltagarvyn, på skärmarna och i webb-TMBoxen:
  "Nytt trafikdygn: Dag 2 · Sön. Alla tåg står på sin utgångspunkt och
  statusarna är nollställda." Den visas en gång, också för den som öppnar
  sidan strax efter skiftet. Medan skiftet väntar säger toasten det.
- Med FastClock sker skiftet när FastClock passerar skiftets tid efter
  midnatt. Tidsmaskinen och Ställ klockan fungerar som förut.

### Webb-TMBoxen visar stationens tidtabell med förseningar, och man väljer hur mycket

Efter Caspers önskan: webb-TMBoxarna och iPhone-appen ska också kunna välja
om förseningar markeras.

- **Tidtabellen bredvid webb-TMBoxen.** När boxen har en station visas
  stationens tåg under boxen, med samma markeringar som i serverns övriga
  vyer: röd bricka och ny tid för sena tåg, grön för en för tidig avgång med
  persontåg, och "Nyss" när något ändras. Godståg och arbetståg får gå före
  sin tid utan markering.
- **Valet görs i kortet Din TMBox** (Förseningar i tidtabellen), med samma
  fem nivåer som i webbläsaren och på skärmarna. Valet sparas i webbläsaren;
  "Som träffen" följer trafikledningens förval.
- **/v1/tmbox/terminal/timetable** (webb-TMBoxen och iPhone-appen) ger nu
  varje rad tågets försening, den nya tiden, om den är beräknad och om tåget
  är för tidigt, samt träffens förval `deviation_level`. Servern räknar med
  samma regler som webbens vyer (`train_live.py`, samma fall som
  `train-live.test.cjs`).
- **Provbänken** (`/tmbox-lab/`) kör en egen testtrafik utan klocka och
  verkliga tider. Där finns inga förseningar att visa, och därför inget val.

### Tågen rör sig i realtid i alla vyer med linjer, och två tåg som möts på dubbelspår

Efter Caspers önskan: alla vyer med linjer ska visa tågrörelser i realtid.

- **Två tåg som möts på dubbelspår** går på var sitt spår, enligt träffens
  trafiksida, utan att taggarna täcker varandra. Det gäller Drift, skärmarnas
  Banöversikt och deltagarvyn, och provas nu med två tåg som båda har avgått
  och möts mitt på sträckan.
- **Kartan i Tidtabell och tågrutter** (Drift) låter det valda tåget röra sig
  med klockan, som Banöversikten. Förut stod det still en fjärdedel ut från
  stationen.
- **Tågdiagrammet på skärmarna** går med klockan varje sekund, inte bara när
  en ny bild hämtas.
- **Nästa händelser och tidtabellen** räknas om medan klockan går, även
  mellan hämtningarna. Det gäller Drift var femte sekund och deltagarvyn var
  tionde. "om N min" och ett tåg som står kvar och blir allt senare stämmer
  alltså hela tiden.

### Störningar och undanställning i automatiken

Förseningsdelarna ur simuleringen flyttar in i den vanliga automatiken, efter
Caspers önskan. Inställningen finns under Inställningar → Obemannade
stationer → Störningar och undanställning.

- **Störningar:**
  - Av (förval);
  - Normal trafik: vart fjärde tåg, 1–4 min;
  - Störd trafik: två av tre tåg, 1–12 min.
- **Var störningen sker:**
  - vid stationen: stationsarbete före avgång, och tåget väntar med
    "Stationsarbete pågår";
  - på linjen: längre gångtid till en automatisk mottagare;
  - båda.
- **Scenarionyckeln:** samma nyckel ger samma störningar. Förseningarna blir
  verkliga händelsetider och syns i tidtabellen enligt den valda nivån.
- **Undanställning vid slutstation** (på som förval, efter 5 spelminuter): ett
  tåg som slutar vid en automatisk station rangeras bort, så att spåret blir
  fritt. Förut höll tåget spåret resten av dagen, och nästa tåg in fick
  "Mottagningsspåret är upptaget", så automatiken stod still. Bemannade
  stationer ställer inte undan.

### Träffens dagar: dagen går fram vid midnatt, och en tidsmaskin

Efter Caspers önskan.

- **Träffen börjar på en dag admin väljer:** Inställningar → Träff och Cloud →
  Träffens dagar, med en veckodag eller "Alla dagar (Dagl)". Ingen koppling
  till verkliga datum.
- **Vid varje midnatt går träffen till nästa dag.** Tidtabellen för den
  dagens veckodag gäller, och Dagl alltid. Drift, deltagarvyn och skärmarna
  visar "Dag 2 · Sön".
- **På den nya dagen är alla statusar för ankomst och avgång nollställda.**
  Tågen står där tidtabellen säger, och automatiken börjar från dygnets
  början. Gårdagen står kvar som historik.
- **Samma veckodag en vecka senare** börjar också tom.
- **TKL-pass följer med** till den nya dagen.
- **Ett tåg som är ute vid midnatt kör klart på gårdagens dag** innan dagen
  byts. Det gäller ett tåg på linjen, en öppen klarering, eller ett avgånget
  tåg med stopp efter midnatt. Ett tåg som fastnat håller inte kvar gårdagen
  mer än sex spel­timmar.
- **FastClock:** dagen byts när klockan slår om från 23:59 till 00:00.
- **Ingen dag byts under en simulering.**
- **Tidsmaskin…** vid klockan på Drift, bara för admin: välj dag och tid.
  - Alla tåg flyttas dit tidtabellen säger då, och alla statusar nollställs.
  - En säkerhetskopia tas först.
  - **Ställ klockan** finns kvar för små justeringar och rör inte tåg som
    verkligen har kört.
- **Rättelser:**
  - Dagarna i tidtabellen läses som Cloud skriver dem: "M-F", "S" och
    intervall i kommalistor ("Mån-Fre,Sön") matchade förut aldrig.
  - Byte av trafikdag med FastClock som klocka gav ett fel.
  - Nästa händelser visar stopp efter midnatt efter kvällens, inte som
    passerade.
- **API:**
  - `/v1/display.calendar` (`start_day`, `day_number`, `weekday`, `week`);
  - `POST /v1/runtime/time-machine` med `day_number`, `time` och
    `meet_generation`;
  - `POST /v1/runtime/calendar` med `start_day` och `meet_generation`.

### Tidtabellen visar verkliga tider, förseningar och vad som nyss ändrats, i fem nivåer

Som i SJ:s app, efter Caspers önskan. Gäller deltagarvyns Tidtabell och Nästa
händelser, Nästa händelser på Drift och skärmarnas Översikt.

- **Fem nivåer,** eftersom tåg nästan alltid är lite sena och det annars blir
  plottrigt:
  1. Ingen markering.
  2. När det inträffar (förval): raden lyser kort och får "Nyss".
  3. Diskret: dessutom förseningen i liten röd text från +5 min.
  4. Fler: röd bricka från +3 min, den nya tiden, och för tidig avgång.
  5. Allt: från +1 min, för tidig ankomst, och förseningen vid tågnumret på
     kartan och i tågdiagrammet.
- **Vem väljer:** admin sätter träffens förval under Inställningar → Skärmar
  och klocka. Varje webbläsare väljer eget under Inställningar → Visning, i
  deltagarvyn under Visning, och varje skärm i sin verktygsrad. Att byta nivå
  markerar ingenting.
- **För tidigt:** en för tidig avgång är en grön "−2" för persontåg. Godståg
  och arbetståg får gå tidigare och markeras aldrig.
- **`/v1/display`:** `display.deviation_level`; endpoint
  `/v1/settings/deviation-level`, bara admin.
- **Försening:** på nivå 4 syns en röd bricka med vita siffror från +3 min. Den
  planerade tiden står överstruken och den nya tiden bredvid. Ankomsterna
  framåt räknas om med förseningen.
- **Tåg som står kvar:** ett tåg som står kvar efter sin avgångstid räknas upp
  som "beräknad". Ett läge som bara tidtabellen gav räknas som i tid.
- **Status i tidtabellen:** varje tåg visar var det är, till exempel "På väg
  mot Bor", "Väntar i Alvesta", "Vid Bor" eller "Ankom 09:56". Förut stod det
  "gick 117 min sedan", räknat bara från tidtabellen.
- **Markering vid ändring:** en rad som just ändrats lyser upp kort och får
  "Nyss". Det sker aldrig när listan ritas första gången. Med minskad rörelse
  blir det bara "Nyss".
- **`/v1/display` har `movement_live`:** per rörelse läge, spår och
  träffklockans tid för ankomst och avgång. Tiden kommer ur händelserna.
  - En övergång som systemet räknade fram i efterhand ("tåget hoppar fram")
    har ingen tid.
  - Vem som gjorde något och anteckningar lämnas aldrig ut.
  - Ett nytt index på `tkl_events` och en cache gör att det inte kostar något
    vid varje uppdatering.

### Tågdiagrammet på Drift går med klockan

Casper såg inte tågen röra sig i diagrammet. Drift hämtar läget när något
händer och annars var 30:e sekund, och diagrammet och klockan stod still
däremellan. Kartan hade redan en egen takt.

- **Klockan, nu-linjen och tågen på linjen** följer nu träffklockan varje
  sekund.
- **Ett avgånget tåg** står på nu-linjen, så långt fram på sträckan som det har
  kommit sedan den faktiska avgången. Det räknas på samma sätt som på kartan,
  så ett försenat tåg syns där det är och inte där tidtabellen har det.
  Förut stod etiketten kvar vid avgången.
- **Skärmens tågdiagram** räknar på samma sätt.
- **Den tända sträckan** går från avgången till ankomsten vid nästa station.
  Förut gick den från ankomsten till avgången därifrån.
- **Nu-linjens tid** avrundas nedåt, som klockan. Förut stod det 09:18 när
  klockan visade 09:17.

### Begripligt besked när servern inte svarar

Casper såg "The string did not match the expected pattern." på klockraden i
Drift. Det är Safaris text när ett svar inte är JSON, här troligen Render- eller
Cloudflare-sidan medan servern startade om efter en uppdatering.

- **Sidan:** varje anrop från administrationen säger nu "Servern svarade inte.
  Den kan hålla på att starta om – försök igen om en stund." när svaret inte
  är JSON eller inget svar kommer alls ("Load failed"). Texten står på sidans
  språk.
- **Servern:** ett fel som ingen förutsåg ger ett JSON-svar (500,
  `internal_error`), och hela spåret skrivs i loggen. Förut stängdes
  anslutningen utan svar.

### Tågen står där tidtabellen säger (#136)

Efter Nollställ träffen fanns inga tåg på stationerna (Benny, #136). Nu står
varje tåg där tidtabellen säger vid klockans tid: efter en nollställning, när
en träff startar, när admin ställer klockan och när trafikdagen byts.

- **På stationen det senast skulle ha kommit till,** på stoppets planerade
  spår, och räknat som ankommet där. Ett genomgående tåg kan därför begära
  avgång direkt.
- **Ute på linjen enligt tidtabellen:** tåget står kvar på avgångsstationen,
  eftersom det saknar klarering. Avgången görs som vanligt.
- **Före första avgången:** tåget står uppställt på sin första station. Kommer
  ett annat tåg in på samma spår innan det avgår, oftast samma tågsätt som
  vänder, ställs det upp först när det tåget har kommit.
- **Efter sista ankomsten:** tåget står på sista stationen.
- **Lägena är riktiga:** TKL, boxarna, kartorna och automatiken ser dem. Det
  tåget skulle ha gjort före stationen räknas som gjort, men skrivs inte som
  händelser.
- **Ett tåg med verkliga händelser rörs aldrig:** en klarering, ett
  linjebesked, något som TKL eller en box har registrerat, eller ett läge från
  trafiken.
- **Spärrarna** för en ny Cloud-version och för borttagning i den lokala
  tidtabellen räknar inte tidtabellens lägen som registrerat trafikläge.
- **Automatiska stationer** fortsätter från lägena. Ett tåg som står
  uppställt skickas även om dess planerade avgång var före automatikens
  start. Ett tåg som redan har kommit fram skickas aldrig igen.

### Tågen rör sig mellan stationerna, och dubbelspårets sida väljs per träff

Efter Drift-skissen i Claude Design och Caspers önskan att se tågen röra sig.

- **Ett avgånget tåg glider mot nästa station** i takt med träffklockan, från
  den faktiska avgången till den planerade ankomsten. Förut stod det stilla en
  fjärdedel ut från avgångsstationen. Servern sparar träffklockans tid i
  avgångshändelsen och lämnar den i `/v1/display` (`departed_seconds`). En äldre
  avgång utan klocktid räknas från den planerade avgången. Ett tåg som är klart
  men inte avgånget står kvar vid stationen. Medan klockan går flyttas tågen en
  gång i sekunden utan att kartan ritas om.
- **Vänster- eller högertrafik på dubbelspår per träff:** landet ger förvalet,
  vänster i Sverige och Norge och höger i Danmark, Tyskland och USA. Admin kan
  välja annat under Inställningar → Träff och Cloud → Trafik på dubbelspår.
  Förut gick tågen alltid till vänster.
- **Antalet tåg inne** står som en liten bricka vid stationens hörn, som i
  skissen, i stället för siffran inne i stationen. Stationen lyser inte längre
  blått, och inget namn läggs på brickan.

### Träffklockan står still medan servern startar om

En gående träffklocka hoppade fram med avbrottet gånger hastigheten när servern
startade om: en minuts omstart i 4× blev fyra minuter i träffen. Render startar
om servern vid varje driftsättning. Nu stannar en ordnad nedstängning (SIGTERM
från Render, systemd eller uppdateraren) klockan, och nästa start låter den gå
vidare från samma tid innan någon sida eller box hinner fråga. En klocka som
admin själv stoppat förblir stoppad, och FastClock följer sin egen klocka som
förut. Vid en krasch, utan ordnad nedstängning, går klockan som tidigare.

### Banöversikten och tågdiagrammet: räls, stationsbrickor och luftigare text

Efter Caspers skärmbilder av Banöversikt och Tågdiagram på helskärm och
kartan på Drift, och en skiss i Claude Design.

- **Räls:** banan ritas som räls med syllar, som i Lovable-projektet. Enkelspår
  är ett spår och dubbelspår två, så de går att skilja åt. Förut var alla
  sträckor lika tjocka linjer.
- **Stationerna är brickor som täcker sina spår:** en cirkel vid enkelspår, en
  stående bricka där dubbelspår går in i sidled, en liggande i höjdled och en
  rundad kvadrat där dubbelspår möts från två håll.
- **Bara namnen står på kartan,** i vanlig vikt. Koden och raden "KOD · n tåg"
  är borta. Antalet tåg inne står i brickan, som lyser blått, och bara när
  något tåg är inne.
- **Tågen på dubbelspår går på sitt eget spår,** till vänster i färdriktningen.
  Två tåg åt var sitt håll på samma sträcka täcker inte varandra.
- **Skärmarna (Banöversikt och Översikt):**
  - ritas som kartan på Drift, i skissens mått, och fyller bredden och mer av
    höjden;
  - kortet och ramen runt Banöversikten är borta;
  - remsan med tåg på linjen har mindre text.
- **Tågdiagrammet på skärmen:**
  - bara namnen, högerställda, i stället för namn och kod som bröts och krockade;
  - tunnare linjer och mindre tågnummer;
  - ingen ram;
  - fönstret börjar en halvtimme före nu i stället för en timme, så det mesta
    av ytan visar det som kommer.
- **Tågdiagrammet på Drift** visar namnen när det finns plats och koderna på
  en telefon, aldrig båda, och har tunnare linjer.
- **Tågets ruttkarta i tidtabellsdialogen på Drift** visar nu också sträckorna
  utanför rutten och de upptagna sträckorna. De saknade färg i Drift
  (`--display-line` och `--display-primary` finns bara på skärmarna).
- Det gamla TV-läget i ritningen är borttaget, eftersom alla kartor nu ritas
  på samma sätt. Nytt prov: `tests/js/topology-rails.test.cjs`.

### Fliken visar Mötesspåret i stället för den gamla orange ikonen

Logotypbytet (#119) lade Mötesspåret på samma adress som den gamla orange
ikonen hade, och webbläsare som redan sparat ikonen för adressen fortsatte
visa den i fliken. Ikonlänkarna på alla sidor bär nu ikonens version i
adressen, så webbläsaren hämtar den nya, och servern svarar på
`/favicon.ico`, som webbläsare frågar efter på egen hand. Den byggs av
ikonens egna PNG-filer med `tools/build-favicon.py`.

### Tågdiagrammet på Drift visar rätt dygn tidigt på morgonen

Stod klockan före dagens första tåg – som efter Nollställ träffen, när den
ställs på planens start – och var ett nattåg fortfarande ute vid samma
klockslag nästa morgon, flyttade diagrammet på Drift hela klockan till nästa
dygn. Axeln visade rätt klockslag, men tågen var nästa dygns, så bara
nattåget syntes, och i Hela dagen hamnade nu-linjen nästan längst till höger
(#137). Nu flyttas i stället varje tåg till den förekomst som ligger närmast
klockan, som skärmens tågdiagram redan gör. Hela dagen visar tidtabellen som
förut. Ett fönster kring midnatt får också rätt klockslag på axeln.

### Uppdateringen från 3.4 startar servern med den pågående körningen

En server som körde 3.4 eller äldre kom inte upp på 3.5: tjänsten stannade
direkt med `ConfigurationMismatchError: The persisted run uses another
published configuration`, och uppdateraren återställde den gamla versionen.
Den sparade körningen bär ett fingeravtryck av configen, och 3.5.0 tog bort
sträckans eget trafikläge (`dispatch_mode_override`) ur configen. Därmed fick
samma config ett nytt avtryck, och servern trodde att körningen hörde till en
annan. Nu känner servern också igen avtrycket som en äldre version skrev för
exakt den här configen, med sträckornas lägen så som publiceringen bär dem,
precis som den redan gjorde för 1.9.0:s layoutfält. En annan config vägras
fortfarande, och ett läge som publiceringen inte har gissas aldrig.
Sträckornas läge – begäran, klartecken och tåg på linjen – följer med genom
uppdateringen.

### Sidan Tidtabell: Clouds Data-vy på servern

Admin har nu en sida för tidtabellen på servern, `/tidtabell`, med
tabellikonen i sidhuvudet och en rad under Inställningar → Träff och Cloud.
Sidan är Clouds egen Data-vy, samma kod som i Cloud (2.12.0), med serverns
tidtabell: dubbelklicka en cell, ändra och spara, så gäller ändringen direkt i
driften. Lokalt ändrade celler är markerade, och raden överst säger hur många
lokala ändringar som finns, med **Återgå till Cloud-versionen**. Har någon
annan sparat medan du ändrade skrivs ingenting över: vyn ber dig trycka
Avbryt och göra om ändringen i den nya tidtabellen.

När Cloud publicerar en ny version medan lokala ändringar finns visas det
direkt på sidan, utan omladdning: vad Cloud-versionen ändrar, vilka lokala
ändringar som då försvinner, och knapparna **Behåll mina ändringar** och
**Ta Cloud-versionen**. Under Inställningar står en länk **Välj under
Tidtabell**. Vyn hämtas först när sidan öppnas, så deltagarvyn och Drift
laddar den aldrig, och den följer serverns ljusa eller mörka läge.

### Cloud frågar innan den ersätter lokala ändringar

En ny Cloud-version aktiverades förut automatiskt så fort banan var fri. Med
lokala ändringar i tidtabellen hade de då försvunnit utan att någon frågats.
Nu hämtas versionen men väntar, och Cloud-statusen säger att ett val behövs.
`GET /v1/runtime/pending` visar vad Cloud ändrar och vilka lokala ändringar
som försvinner, och admin väljer **Ta Cloud-versionen** eller **Behåll mina
ändringar** med `POST /v1/cloud/local-decision`. Ta tar en säkerhetskopia
först och går sedan samma väg som en automatisk aktivering, med samma spärrar;
hindrar trafiken väntar den tills banan är fri. Behåll gäller bara just den
versionen: servern hämtar den inte igen, och frågan kommer tillbaka när Cloud
publicerar en nyare. Utan lokala ändringar fungerar allt som förut. Valet görs
på sidan Tidtabell.

### Tidtabellen kan ändras på plats, ovanpå Clouds version

Admin kan nu flytta en tid, byta spår, ändra dagar, från och till, ta bort
ett tåg eller göra en sträcka dubbelspårig mitt under träffen, utan att gå
via Cloud. Ändringarna ligger som ett lager ovanpå Clouds publicering, med
samma publicerings-id och samma rad-id:n, så TKL-läget följer med raden och
boxarna ser den nya tiden direkt. Cloud ser ingenting, och Clouds version
står orörd: "Återgå till Cloud-versionen" tar bort lagret, efter en
säkerhetskopia. Tjänster och rutter byggs om med Clouds egen kod, så tågen
blir exakt de Cloud skulle ha gett.

Bara det som är fritt får ändras: ett tåg som är ute på linjen, har ett
öppet körtillstånd eller väntar på kvittens av ett linjebesked kan inte
ändras, och ett tåg med registrerade driftuppgifter kan inte tas bort. En
sträcka byter spårtyp bara när den är fri och ingen TMBox vid den är mitt i
en inmatning. Resten av trafiken går vidare under tiden. Varje sparning är en
ny revision; en sida som ligger efter får inte spara, och under en
simulering kan inget ändras. 16x2-boxarna byggs om efter en ändring, som vid
byte av trafikdag, så en box mitt i ett val får "Läget ändrades" och väljer
om.

API:et är `GET /v1/meet-data`, `POST /v1/meet-data` och
`POST /v1/meet-data/discard`, bara för admin. Sidan som använder det kommer i
nästa steg, tillsammans med valet när Cloud publicerar en ny version medan
lokala ändringar finns.

### Samma tidtabellskod som Cloud

Servern har nu Clouds egen kod för tidtabellen: tjänster och rutter ur
tågraderna, trafikdagar, spår, kontrollen och rimlighetskontrollen. Den ligger
som en byte-identisk kopia i `src/tmbox_gateway/timetable_core/`, låst med samma
digest som i Cloud, och kopieras med `node tools/sync-timetable-core.mjs`. Det
är den som bygger om tjänster och rutter när tidtabellen ändras på plats, så
att tågen blir exakt de Cloud skulle ha gett. Ett prov visar att kopian bygger
om tjänsterna och rutterna i ett riktigt Cloud-paket exakt.

### Försök igen fungerar

När en uppdatering hade misslyckats blinkade knapparna till och ingenting
hände. **Försök igen** skickade sin begäran utan innehåll; servern läser varje
POST som JSON och svarade 400, och knappen tittade aldrig på svaret utan läste
bara om det gamla "misslyckades". Eftersom **Installera och starta om** göms så
länge statusen säger misslyckades fanns det ingen väg ut ur vyn. Nu startar
båda knapparna uppdateringen på samma sätt – frågar först, skickar en riktig
begäran, säger till om servern nekar – och sidan följer stegen fram till den
nya versionen. Statusen bär `updated_at`, så det gamla felet som ligger kvar
tills uppdateraren skrivit sitt första steg inte läses som det nya försökets
svar; skriver uppdateraren ingenting på 30 sekunder står det att uppdateringen
inte startade. Gäller från versionen efter den här: en server som kör en
äldre version har kvar den gamla knappen tills den uppdaterats en gång.

### Programuppdatering säger varför installationen gick fel

En uppdatering som stannade i steget Installerar sa bara "Installationen
misslyckades, återställde föregående version" – orsaken fanns bara i journalen
på serverdatorn. Nu sparar installationen det den skriver i
`update-install.log` bredvid statusfilen, och Programuppdatering visar de
sista raderna under Teknisk information som **Installationslogg**. Loggen
skrivs av det nyss hämtade paketet, så den finns redan vid nästa försök på en
installation vars uppdaterare är den gamla.

Tre orsaker som förut fällde en uppdatering utan att synas är också borta.
`apt-get update` körs bara när ett systempaket faktiskt saknas – på en
uppdatering finns alla redan, och ett nätverksfel mot spegeln eller ett apt
som var upptaget med systemets egna uppdateringar behöver inte stoppa något
(och apt väntar nu på låset i stället för att ge upp direkt). Startkontrollen
väntar in att servern svarar på `/healthz`, upp till en minut, i stället för
att titta en gång efter två sekunder – `Restart=always` gjorde att en server
som kraschade vid start kunde se startad ut och en långsam se stoppad ut; går
starten fel skrivs tjänstens status och journalens sista rader i loggen.
Sammanfattningen sist i installationen kan inte längre fälla en installation
som redan kör, till exempel över en saknad `connection-code.txt`.
### Versionsnummer i Inter med tabellsiffror

Programuppdatering visade den installerade versionen, versionerna under Vad är
nytt och versionsraden i sidomenyn i JetBrains Mono, fast tal står i Inter med
tabellsiffror sedan #122. Nu är de det: 3.4.0 och listan i samma siffror som
klockan och tågnumren. Byggets id (26ac80b3) är en kod som läses upp och
behåller monospace, där den prickade nollan skiljer sig från en bokstav.

### Uppdateringens steg där man väljer att uppdatera

Räckan av steg för en pågående uppdatering (söker, hämtar, verifierar,
installerar, startar om, kontrollerar, klart) stod i en egen ruta under Vad är
nytt, långt ner på sidan. Nu står den i versionsrutan, under Installerad →
Tillgänglig, där uppdateringen startas, och syns bara medan en uppdatering
pågår eller har misslyckats.

### Ett trafikläge för hela trafikspelet

Trafikläget gäller nu hela trafikspelet: grannstationen godkänner varje tåg
(`clearance`), eller sträckan tas direkt om den är ledig (`direct`). En sträcka
har inget eget läge längre. TKL läste redan bara träffens läge, så TKL, boxarna
och servern säger nu samma sak. Paket som Cloud redan har publicerat med ordet
`automatic`, eller med `""` på en sträcka, avvisades förut med "Driftpaketet
innehåller ogiltigt …"; nu installeras de, och `automatic` läses som `direct`.
TMBox v2 får samma `dispatch_mode` på alla sträckor.

### Vad är nytt under Programuppdatering

Programuppdatering visar vad varje version gjorde, på rubriknivå: versionen,
datumet och rubrikerna, de tio senaste och resten bakom en knapp. Finns en
uppdatering visas först vad den innehåller. Rubrikerna skrivs av versionsroboten
när versionen höjs, ur PR-titlarna, till `RELEASES.json`; historiken sedan 1.0.1
är ifylld i efterhand. Samma mekanism används av Cloud och TKL.
### Tidtabellens konflikter bara under Träff och Cloud

Clouds kontrolluppgifter för tidtabellen, konflikter och observationer, visades
som ett märke i sidhuvudet på varje sida, också i driften. Nu finns de bara under
Inställningar → Träff och Cloud, där configen hämtas från Cloud och där de rättas.
En liten flagga med antalet konflikter vid Träff och Cloud i inställningsmenyn
säger att det finns något att titta på; en träff körs ofta hela dagen med en känd
konflikt, och då ska den inte synas överallt.

### Byta träff går att förstå (#128)

En kod för en annan träff gav "Bekräfta Byt träff", men ingen knapp hette så; det
som behövs är kryssrutan överst i dialogen. Nu säger meddelandet det, och rutan
markeras och får fokus. Krysset och Avbryt stänger dialogen direkt: frågan "Stäng
utan att spara ändringarna?" fick Avbryt att se ut att hålla kvar en, och en
träffkod är inget att spara. Ett misslyckat försök visar Cloud-kopplingen som den
är, i stället för "Kopplingen misslyckades". Att koppla igen slår inte längre på
"Hämta publicerade versioner automatiskt" om administratören har stängt av det, och
efter ett byte står det "Servern kör nu {träff}." i stället för att pågående drift
har bevarats. Dialogens rubrik, ingress och kryssruta finns nu på alla fem språken.
API:et svarar `meet_change_required` när bytet inte är bekräftat.
### Obemannad station svarar på TAM (#130)

En obemannad station gav aldrig klart till en TMBox när ett annat tåg samma dag
var planerat på mottagningsspåret, vilket på en mötesstation nästan alltid är
fallet. Automatiken räknade varje rad i dagens tidtabell på spåret som om tåget
stod där, även tåg som kommer senare. Nu räknas bara verklig beläggning, så som
simuleringen redan gjorde: tåg som har ankommit eller står uppställda på spåret.
Bemannade stationer kontrolleras som förut.

Under Inställningar → Obemannade stationer syns nu också vilka tåg automatiken
väntar med och varför, till exempel "Mottagningsspåret är upptaget", och tåg som
automatiken inte kan köra alls.
### Nollställ träffen fungerar på en uppdaterad Raspberry Pi (#129)

Uppdateraren skapade mappen `backups` som root, och servern, som kör som
`trainmeet-server`, fick inte skriva i den. Nollställ träffen tar alltid en
säkerhetskopia först och stoppade därför med "unable to open database file". Nu
ger installationen mappen och det den innehåller till servern vid varje
uppdatering, så en Pi som redan har uppdaterats rättas av nästa uppdatering. Går
mappen ändå inte att skriva i säger felet var den ligger och hur det rättas.

"Nollställ träffdata" ber om ordet på användarens språk (RESET, NULSTIL,
NULLSTILL, ZURÜCKSETZEN), men knappen låstes bara upp av NOLLSTÄLL. Nu låser det
visade ordet upp knappen, och NOLLSTÄLL gör det fortfarande.

### Träffens land: SE, DK, DE, NO eller US

TrainMeet Cloud (2.2 och senare) skickar träffens land i driftpaketet
(`meet.country`). Servern visar landet i stället för "EU": i sidhuvudet, under
Inställningar → Träff och Cloud, i deltagarvyn och på TMBox-sidan. Nya boxar får
landets språk (svenska, danska, tyska, norska; engelska för US); en box som fått
ett eget språk behåller det. Trafiken är densamma för alla europeiska länder, och
inget lagrat ändras: träffvalet, klockans inställningar och säkerhetskopiorna
gäller som förut. Ett paket utan land, från ett äldre Cloud, är en svensk träff;
ett okänt land stoppar aldrig paketet. `/v1/server-context`, `/v1/workspaces` och
`/v1/display` har fältet `country`.

### Typsnittet från #122 på de sista ställena

Tågnumret i boxens tidtabell (provbänken), tågmärket på ruttkartan i "Tidtabell
och tågrutter", milstolparna i US-diagrammet och minutfältet för städning står nu
i Inter med tabellsiffror i stället för JetBrains Mono. Driftklockan hade kvar en
`kr-mono`-klass som bara inte vann; den är bytt mot `kr-num`.

### Webb-TMBoxen återansluter när telefonen vaknar

En telefon som vaknar laddar ofta om sidan innan Wi-Fi är tillbaka. Webb-TMBoxen
gav då upp vid första misslyckade kontakten och visade **Starta ny TMBox**, som
skapade en ny box med ny enhetskod som trafikledningen fick tilldela på nytt.
Nu är den sparade boxen fortfarande samma box: den används direkt och ansluter
av sig själv när servern svarar, med samma enhetskod och station. Den frågar
också genast när sidan syns igen eller nätet kommer tillbaka. Bara när servern
säger att boxen inte finns längre (borttagen i Klienter) visas "Den här TMBoxen
finns inte längre på servern" och knappen för en ny.

### Nollställ träffen

Ny rad först i **Farozon**: börja om träffen med samma plan. Klockan går
tillbaka till planens starttid och står still, och klareringar, linjebesked,
tågens lägen, TKL:s anteckningar och automatikens tider tas bort. Plan,
Cloud-koppling, enheter, användare och klockans inställningar står kvar, och
servern startar inte om. Bekräftas med träffens namn; en säkerhetskopia tas
först och går att lägga tillbaka. `POST /v1/server/meet-reset`.

### Konton är namn, e-postadress och lösenord (version 3)

Användarnamnen är borta. Ett konto har ett namn som visas, en e-postadress och
ett lösenord, och man loggar in med e-postadressen. Adressen är unik och kan
bytas men inte tas bort; namnet behöver inte vara unikt.

- **Installationen** frågar efter namn, e-postadress och lösenord för ägaren.
- **Inloggning, Glömt lösenordet? och Jag har en kod** tar e-postadressen.
- **Användare:** inbjudan och redigering har Namn och E-postadress. Ett konto
  utan adress märks "Saknar e-post – kan inte logga in".
- **Brevet via TrainMeet Cloud** bär inget användarnamn längre. Det kräver
  TrainMeet Cloud 1.15 eller senare, som visar adressen i brevet.
- **Återställningskommandot** listar konton med nummer, namn och adress, och tar
  `--konto <nummer eller e-post>` och `--email <adress>`. `--user` finns inte
  längre.
- **Den gamla enda inloggningen** (`/v1/admin/access` och "Ändra inloggning")
  är borttagen.
- `/v1/auth/login`, `/v1/setup/admin`, `/v1/admin/password-reset` och
  `/v1/admin/users/redeem` tar `email`. Inloggningen tar också emot fältet
  `username` om det innehåller adressen, för äldre TKL.

**Vid uppgradering från version 2:** ett konto behåller sitt gamla användarnamn
som namn. Ett konto utan e-postadress kan inte logga in förrän ägaren ger det
en. En ägare utan adress kör `python -m tmbox_gateway.recover --state-dir …
--konto 1 --email <adress>` på serverdatorn. Hade två konton samma adress
behåller ägaren eller den äldsta administratören den, och de andra får en ny av
ägaren.

### Nollan har ingen prick längre: tider och nummer i Inter

JetBrains Monos nolla har en prick i mitten, och på avstånd går den ihop med en
åtta: 08:30 kunde läsas som 08:80 och tåg 4720 som 4728, särskilt på en TV
över rummet.

- Klockorna (Drift, Träffklockan, Banöversikt, Tågdiagram, Översikt och
  deltagarvyn), tågnummer, tider i listor och i tågdiagrammet, antal och
  "om 3 min" visas nu i Inter med tabellsiffror: vanlig nolla, och siffrorna är
  lika breda så att kolumner och klockor ligger kvar i rad.
- Antalet tåg bredvid stationskoden på kartorna är en egen del av etiketten
  (koden i monospace, siffran i Inter).
- Stationskoder, adresser, lösenord, anslutningskoder och versionsnummer behåller
  JetBrains Mono, där pricken hjälper att skilja en nolla från ett O.

### Obemannade stationer sköts automatiskt (#115)

En station utan TMBox eller TKL svarade aldrig på en tåganmälan, så tåget kunde
inte skickas. Nu sköts varje sådan station automatiskt när träffklockan går, med
simulatorns regler men i vanlig drift: den ger klart när mottagningsspåret är
ledigt, begär och anmäler avgång för sina egna tåg och anmäler ankomst när
tidtabellens gångtid har gått sedan avgången. Den första TMBox eller TKL som
arbetar med stationen gör den manuell. Tappar den kontakten väntar stationen;
admin kan lämna den till automatiken under **Inställningar → Obemannade
stationer**, där automatiken också kan stängas av. Se
[docs/automatic-stations.md](docs/automatic-stations.md).

### TMBox frågar "FLYTTA 93 HIT?" för ett tåg som inte syns komma (#115)

Stationer glömmer ibland att klarera och skickar bara tåget. Skriver
mottagaren tågnumret, eller väljer tåget i tidtabellen, frågar boxen nu
**FLYTTA 93 HIT?** med `#Ja B:Sp`: `#` flyttar tåget hit på planerat spår,
`B` väljer annat spår. Det gäller ett tåg som aldrig skickats, som förut
hette "Placera på spår", och nu även ett tåg som är klarerat men aldrig
anmält avgånget, som tidigare bara gick via spårväljaren. Avsändarens del
räknas som gjord och sträckan blir fri. Frågan nås bara med ett skrivet
eller valt nummer, så ett andra `#` efter Ge klart flyttar aldrig ett tåg.

### Anslutet ställverk är i tjänst utan trafikpass

En TKL-terminal behöver inte längre starta ett trafikpass för att begära,
besvara och avsluta tågklarering eller anmäla tågrörelser, precis som en
tilldelad TMBox. Utan pass registreras stegen under terminalens eget namn;
ett aktivt pass ger fortfarande operatörens namn. Felet
`tkl_shift_not_started` finns inte längre. Passen finns kvar för den som vill
ha namngiven operatör och överlämning.

### Kontrollrummet: nya Drift, Inställningar, skärmar och deltagarvy

Hela serverns gränssnitt är byggt om efter designen "Kontrollrummet", i
mörkt och ljust läge och på sv, da, nb, en och de.

- Drift: klockrad, karta, händelser, tågdiagram och tågpanel i den nya
  designen. Tåg som står på en station visas som antal (`KOD · n`) i stället
  för klossar, så kartan går att läsa även när många står inne.
- Inställningar: sidomeny med nio avsnitt i grupper, och Avbryt/Spara per
  avsnitt i stället för en gemensam spara-knapp.
- Skärmarna (klocka, Banöversikt, Tågdiagram, Översikt): gemensam
  verktygsrad som döljs efter fyra sekunder i helskärm och alltid syns i
  fönster. Större siffror, sekunder valbara per skärm, tydlig stoppad klocka,
  QR-koderna för Wi-Fi och Träffen i skärmens topp och valbart tidsfönster
  (2, 3, 6 eller 24 timmar) i Tågdiagrammet. Skärmarna har eget färgtema.
- Deltagarvyn på mobil: klocka, tåg på banan, nästa händelser och tidtabell
  med sökruta i en smal kolumn. "Anslut din TMBox" öppnas som ett blad från
  botten. Kartan går att dra i sidled. Gäster får ljust eller mörkt läge
  efter sin enhet.
- Gammal CSS och död kod som de nya vyerna gjort överflödig är borttagen.

### Helskärmsvyerna i designens form: ringar, På linjen och händelsetabell

Resten av granskningen av TV-skärmarna mot designen (SkarmBana, SkarmOversikt).

- Stationerna på TV-kartorna är ringar, som i designen: ljusa på kortet och
  blå när ett tåg står inne, namnen i halvfet stil. Sidfoten förklarar det.
- Banöversikt ligger på ett kort som i designen, och under kartan står tågen
  som är ute på linjen: tåg, sträcka och när det ska vara framme (gult när
  det är sent). Fler än fyra tåg: de tre första och "och N till".
- Översikt: Nästa händelser och På linjen just nu har en rubrikrad med grå
  rad och rader i kolumner – tid, tåg, "avgår Alvesta C mot Lekby" och
  "3 min"; på linjen tåg, sträcka och ankomsttid. Under tågen står om
  trafiken följer tidtabellen eller hur många som är sena.
- Statusraden ryms även när tåglistan är full eller visar "och N till".

### Helskärmsvyerna: QR-koderna i ramen, inte i innehållet

En granskning av TV-skärmarna mot designen (artboard 9–20), gjord i Claude
Design. QR-koderna tog plats från det skärmarna är till för: 150 px av
Tågdiagrammets höjd och 280 px av Översiktens bredd.

- QR-koderna (1 · Wi-Fi, 2 · Träffen) ligger i ramen som redan finns: i
  översta raden bredvid klockan på Banöversikt och Tågdiagram, som sista ruta
  i rutraden på Översikt och i hörnet på Träffklockan som förut. Tågdiagrammet
  får 852 px höjd med koderna påslagna (755 px förut). Är koderna avslagna för
  en skärm reserveras ingen plats alls.
- Översta raden följer designen: träffens namn i 36 px, skärmens namn och hur
  många tåg som är ute på linjen, klockan med ● och hastigheten, eller
  Stoppad i gult.
- Sidfoten förklarar färgerna: "Fylld tågbricka = på linjen" på Banöversikt,
  på linjen nu · planerat · nu på Tågdiagrammet.
- Översikt har ingen översta rad, som i designen. Rutraden börjar med klockan,
  träffens namn, om klockan går och trafikdagen.
- Tågdiagrammet: stationens namn och kod på samma rad, tågnummer med mörk kant
  så att linjerna inte skär dem och aldrig två nummer ovanpå varandra. Tåg ute
  på linjen har en blå bricka vid nu-linjen, och tiden står i en gul bricka
  överst. Diagrammet ritas för den höjd det faktiskt har.
- Banöversikt och Översikt: tågen inne på en station läggs inte över ett tåg
  på linjen eller över stationens namn (4142 och 421 vid Alvesta C, 8282 vid
  Elisabethstad).
- Drift: valt tågs sammanfattning har ett mellanrum under kartan även när
  den kant-till-kant-ritade kartan visas på en telefon.

### Designkorrektur: samma ram och samma kortrubrik på alla sidor

En genomgång av server.trainmeet.app mot designen (UX-genomgången, DriftEU,
ServerSida och Deltagare), inloggat och oinloggat, på dator och mobil.

- Samma sidmarginal överallt: 24 px från kanten och 12 px under sidhuvudet.
  Drift och Inställningar hade dubbel marginal (46 px) och ett glapp överst.
- Sidhuvudet: TrainMeet-märket som det är (blått tåg) i stället för en
  färgfiltrerad ikon, och samma märke på deltagarvyn som tidigare var orange.
  ⚙, ? och Logga ut är ritade ikoner, och ikonen för sidan man står på är
  markerad. Utloggad (inloggningssidan) visar sidhuvudet bara träffens namn –
  inga knappar som inte går att använda. På mobil ryms kontrollerna på en rad;
  statusbubblan syns där bara när den säger något (ny version, ingen kontakt).
- Drift: varje modul har samma rubrikrad (namn, grå rad, kontroller,
  "Öppna på skärm ↗"). Trafik just nu har filtren och skärmlänken i rubriken,
  händelserna i kolumner med "om 4 min". "Inne på stationerna" och
  "Tidslinje" är fortsatt öppna block enligt 1.23.0. Banöversikten är kortets innehåll, inte
  en ruta i rutan, och korten bredvid varandra är lika höga. Tågdiagrammet går
  kant i kant, visar ungefär fem timmar i stället för nästan hela dagen och har
  en röd streckad nu-linje enligt Drift-designen. Klockraden har fälten i mitten, inte i
  botten.
- Klienter: antalet som väntar är en gul etikett vid rubriken, adressen en
  bricka; en box som väntar är en ljusgul rad. TMBox-placering visar
  stationens namn först och koden diskret bredvid, Redigera som länk till
  höger.
- Inställningar: samma kortrubrik som på Drift. Spara är den lugna knappen,
  så sidan inte blir en kolumn av blått. Den här servern har fått sin rubrik,
  och rutorna som upprepade Träff och Cloud är borta. "+ Bjud in" ligger i
  rubriken på Användare. Knappen för avsnittet man läser är markerad, Farozon
  är röd i menyn, och kortet är vitt med röd ram och röda knappar.
- Deltagarvyn på mobil följer designens ordning: klockan, På banan just nu och
  sedan tidtabellen. Raden om när en virtuell TMBox tas bort klipptes av.

Andra varvet, efter jämförelse sida vid sida med designen i samma typsnitt:

- Inställningar har designens form: varje kort har en rubrik med en grå rad,
  korta rader med etikett och värde (Träff · Kör version · Cloud, Namn med
  fält och Spara på samma rad, Nätverk) och en fot för det finstilta. Byt
  träff ligger i foten på Träff och Cloud. Skärmar och klocka behåller fyra
  delar med var sin Spara, men varje del är en rad med Spara sist. Användare
  har roll som pill och Redigera som länk; Programuppdatering version, läge
  och knapp på en rad; Farozon varje åtgärd med vad den gör bredvid.
- Drift: klockraden säger "Går · 4× · Intern serverklocka" på en grå rad,
  klockkällan är växeln Intern | FastClock…, fälten heter Hastighet och Orsak
  vid stopp (valfritt), knappen Stoppa klockan, klockslaget visar timmar och
  minuter. Simuleringen har en knapp (virtuell TMBox finns under Öppna).
  Klienter: Tilldela är radens blå knapp, Ta bort en grå länk, och raderna har
  samma kolumner. Trafik just nu har designens brickor (tåg på linjen, inne på
  stationerna, avvikelser – grön när inget avviker), "avgår Vagnsta · 2 min",
  och På linjen just nu syns bara när ett tåg är ute. Tågdiagrammet är lägre
  och har röd streckad nu-linje som i designen. Kartorna på Drift och i
  deltagarvyn har blå ringar på grå spår.

### "Slutför och starta om" visade ett skriptfel

Sista steget i installationsguiden visade "configMessage is not defined" och
laddade aldrig om sidan när servern kommit tillbaka. Installationen var klar
och servern startade om, men det såg ut som att något gått fel. Samma fel gjorde
att "Starta om servern" under Programuppdatering inte gjorde någonting.
Meddelanderaden de använde försvann i 119f8a4 men namnet blev kvar. Nu skriver
de i sina egna meddelanderader. `tests/js/setup-finish.test.cjs` provar båda.

### Servern startar igen efter att en Cloud-config har aktiverats

En server som hade aktiverat en ny config från Cloud kunde inte starta igen.
Vid nästa omstart, till exempel den som installationsguiden gör efter
"Slutför", stoppade den med "Persisted revision does not match its payload",
och på en Raspberry Pi startade systemd om den i en loop. Trafikläget sparades
med ett revisionsnummer som lästes ett steg för tidigt, så numret och
innehållet skilde sig åt med ett. Felet fanns sedan 1.10.0.

Nu läses numret först när innehållet är klart. En databas som redan har felet
startar ändå: servern känner igen just den skillnaden, skriver en varning i
loggen och använder innehållet. En Pi som sitter fast blir frisk av
`sudo systemctl start trainmeet-server-update.service`.

### Inställningar: Skärmar och klocka i fyra delar

Kortet blandade fyra saker med en gemensam Spara-knapp. Nu är det fyra delar
med egen rubrik och egen Spara, som bara sparar sin del: **Klocka**,
**QR-koder på skärmarna**, **Parningskod för TMBoxar** (adress och kod, hur
länge koden gäller och hur länge en virtuell TMBox utan station finns kvar)
och **Träffens Wi-Fi**. Farozon ligger sist på sidan, under båda kolumnerna.

Wi-Fi fylls alltid i av administratören. Servern läser inte längre av vilket
nät den själv sitter på, så sidan ser likadan ut på en Raspberry Pi och i en
datorhall. Är fälten tomma blir det ingen Wi-Fi-kod på skärmarna.

### Rullgardinsmenyer

"Öppna på skärm" heter "Öppna skärm" och går att översätta. Menyerna stängs
när man klickar eller trycker utanför dem, trycker Escape, väljer något eller
öppnar en annan meny. Detsamma gäller menyn i US-klienten. Skärmens egna val
säger "Som i inställningarna: …" i stället för "Enligt inställningar · …".

### Fasta marginaler på mobilen

Sidorna glider inte längre i sidled på en telefon. Alla sidor har samma
marginal, 16 px, och det som är bredare rullar inuti sin egen ruta. Fält har
16 px text på pekskärmar, så att iPhone inte zoomar in sidan när man trycker
i ett fält.

### Rester av den gamla färgen

Primärknappar blev bruna när man höll över dem, och på iPhone låg färgen kvar
efter ett tryck. Samma kopparfärg fanns kvar på Ägare-märket, länkknappar och
inbjudningskoder. Allt följer nu UI-kitets blå. Hjälptexten i tomma
Wi-Fi-fält står i Inter, inte i kodtypsnittet.

### Inloggad hamnar man i Drift

Deltagarvyn på `/` är till för gäster. Den som är inloggad kommer till Drift:
`/`, loggan, skärmarnas tillbakalänk och gamla bokmärken till `/#workspaces`
leder dit. Förut räckte ett klick på loggan för att hamna i deltagarvyn.
Loggar man ut kommer man tillbaka till deltagarvyn.

### Skärmarna visar inte EU eller US

Skärmarnas rubrik visar träffens namn och vilken skärm det är, inte EU eller
US. Det är en inställning för den som sätter upp träffen, inte något salen
behöver se.

### Typsnitten är alltid Inter och JetBrains Mono

Tider, tågnummer, stationskoder och versionsrader under Drift ritades i
enhetens eget kodtypsnitt, siffrorna på den analoga klockan i dess
systemtypsnitt och simuleringsbannern i `system-ui`. En TV, en mobil och en
Raspberry Pi såg därför olika ut. Nu pekar allt på de två paketerade
familjerna. JetBrains Mono i halvfet (600) finns med, i stället för att ritas
som fet. Inter laddas en gång per sida, inte två. `tests/test_fonts.py`
håller fast vid det.

### Wi-Fi: text på deltagarvyn, lösenordet i skärmarnas kod

Deltagarvyn visar träffens nätverk och lösenord som text, utan QR-kod: den som
läser sidan är redan på nätet, och en TMBox kan inte skanna. Wi-Fi-koden hör
hemma på skärmarna och innehåller lösenordet, annars fungerar den inte.
Kryssrutan ”Visa lösenordet på deltagarvyn och skärmarna” är borttagen. Vill
man inte dela nätet lämnar man fälten tomma, så visas bara träffens kod.

### Stationsnamnen på kartorna

Där en kort bana ligger tätt intill huvudlinjen, eller ett spår går ned från
en station, flyttas namnet till den fria sidan av stationen i stället för att
krocka med grannens namn eller skäras av spåret. Kartor där inget krockar ser
ut som förut. Översiktens karta använder hela bredden. Deltagarvyns karta på
mobilen fyller kortets bredd.

Säkerhetskopian innehöll ingenting.

### Den virtuella TMBoxen ser ut som resten av servern

Deltagaren scannar QR-koden på TV:n, landar i deltagarvyn och trycker
**Starta virtuell TMBox**. Förut hamnade hen då på en sida med beige
bakgrund, kopparfärgade rubriker och andra typsnitt – en annan produkt.
`/tmbox/` och provbänken `/tmbox-lab/` använder nu Serverns UI-kit: samma
sidhuvud som deltagarvyn med träffens namn, samma kort, knappar och typsnitt.

På `/tmbox/` visar kortet **Din TMBox** om boxen väntar på station eller är
tilldelad, och enhetskoden stort medan den väntar. Knapparnas betydelse står i
ett eget kort i stället för i ett textstycke. Länken till provbänken är borta
från deltagarens sida; administratören når den från Hjälp.

**Själva boxen är oförändrad.** Det rosa skalet, den blå displayen och
knappsatsen ser ut exakt som den fysiska TMBoxen och är låsta i ett eget block
i `terminal16_web/style.css`. `tests/tmbox_case_golden.css` håller blocket och
ett test faller om någon ändrar det. Boxen renderades med den gamla och den nya
stilmallen och jämfördes pixel för pixel i fyra bredder: identisk.

### Simulatorns förval är TMBox v2

TMBox v2 är fastställd som ESP32-S3 med en 20×4-display. Simulatorn under
KÖR → TMBox v2 startar därför i 20×4 i stället för 16×2, och väljaren märker
ut vilken geometri som är produkten.

Alla fyra går fortfarande att välja — en box rapporterar sin egen geometri och
simulatorn ska kunna visa vilken som helst. Det som ändras är vad man ser utan
att välja något.

De boxar som redan finns är TMBox v1 Legacy, kör ESP8266 och en annan
firmware, och pratar inte med den här servern alls. 16×2 var deras geometri,
inte produktens.


### TMBox-dokumentation i webbadmin

Att veta vad boxen gör krävde en box, eller en fil att öppna lokalt. KÖR →
TMBox v2 har nu tre vyer bredvid testklienten:

**Flöden** visar tolv tangentsekvenser, körda genom samma tillståndsmaskin
som boxen. Varje steg visar tangenten, vad boxen gjorde med den och skärmen
som blev följden. Sekvenserna är firmwarens egna — de kommer ur
`golden_traces.txt` — och stegen är härledda, inte skrivna.

**Skärmkatalog** visar varje skärm boxen kan rita, i den geometri som är
vald. Rutorna ritas av `tmbox-render.js` ur referensstationen Charlottendal,
samma fixturer som `golden_frames.txt` är avtryckt ur, och ett test jämför de
två.

**Referens** samlar knappmodellen, inmatningslåset och — uttryckligen — vad
firmwaren inte gör: D-tangenten har inget fall alls, `clearance.cancel` och
`line.available.publish` saknas, och trafiköversikten är inte byggd.

Referensstationen flyttade ut ur testharnesset till `tmbox-fixtures.js`, som
både guldtestet i Node och sidan i webbläsaren laddar. Två kopior av en
referensstation är två som glider isär.

Scenariernas beskrivningar är det enda i vyn som är skrivet för hand, och en
av dem var fel: den påstod att A och B ignoreras på ett linjemeddelande, när
spåret säger att båda skickar ett `clearance.response`. Ett test jämför nu
varje sådant påstående mot vad tangenten faktiskt gjorde.


### Databaskopian togs på ett sätt som inte fungerar

Uppdateraren tog en kopia av databasen före varje installation, med `cp`. Men
varje lager i servern öppnar SQLite i WAL-läge, vilket betyder att det som
nyss skrevs ligger i `trainmeet.db-wal` bredvid huvudfilen tills något
checkpointar — och på en server som kör är det aldrig. Det är exakt läget en
uppdatering arbetar i.

Kopian blev därför en tom databas på fyra kilobyte. Det svåra är att den inte
går sönder: den öppnas utan protest, den saknar bara alla tabeller, och
`PRAGMA integrity_check` svarar `ok`. Felet syns först den dag någon behöver
kopian.

Kopian tas nu med SQLites eget backup-API, som läser genom skrivloggen och
tar en sammanhängande ögonblicksbild av en levande databas. Resultatet
kontrolleras mot källan innan det sparas, och en kopia som innehåller mindre
än källan kastas i stället för att sparas. Går kopieringen inte att genomföra
avbryts uppdateringen innan installationen börjar.

Kopieringen flyttades också till efter uppackningen. Det gör två saker: inget
kopieras i onödan för en uppdatering som ändå aldrig kommer fram, och koden
som kör är den nyhämtade — så den här rättningen når även installationer som
fortfarande står på en äldre version.

### Gamla kopior gallras

Ingenting tog någonsin bort dem, på en maskin vars hela lagring är ett
minneskort. De tio senaste sparas nu.

### Att återställa en kopia går att göra

Det fanns ingen väg tillbaka — varken knapp, kommando eller beskrivning.
Handgreppet som ligger närmast till hands, att kopiera filen på plats, gör
dessutom fel: lämnas `trainmeet.db-wal` kvar hör den till den gamla databasen
och SQLite spelar tillbaka den *över* kopian. Återställningen ger då tillbaka
just den data som skulle ersättas, och integritetskontrollen svarar `ok`.

`tmbox_gateway.backup restore` gör det i rätt ordning, vägrar tomma kopior och
finns beskrivet i README.

## 1.3.2

Serveradministrationen flyttar ur byggflödet.

### Inställningar som eget läge

För att uppdatera programvaran fick en operatör trycka **Bygg om träffen** —
ett flöde om träffens innehåll, som varnar att inget slår igenom förrän man
aktiverar. Kugghjulet på översikten hette dessutom "Öppna administration" men
landade i steg 1, som svarar på var träffen kommer ifrån.

Att köra, att bygga och att administrera är tre olika saker:

| Läge | Vad |
|---|---|
| KÖR | Det som händer under träffen |
| BYGG | Träffens innehåll: källa, bana, tidtabell, TMBoxar |
| **Inställningar** | Servern själv: identitet, åtkomst, programvara, Cloud-koppling, nollställning |

Nås med kugghjulet i topplocket. Programuppdateringen är ett klick från
översikten i stället för fyra. Innehållet är oflyttat i sak — samma sektioner,
samma API-anrop, samma sju uppdateringssteg.

BYGG har därför fyra steg, följt av serverinställningar som steg 5.

### Mindre

- `currentMode()` svarade `kor` för allt utom `bygg`. Den hade inga anropare,
  men samma tvåvägsval satt på startraden: ett sparat inställningsläge kunde
  aldrig läsas tillbaka, så en omladdning landade i KÖR.
- Flikraden hade `overflow-x: auto` men saknade `min-width: 0`, så den knuffade
  i stället för att scrollas vid smala fönster.

---

## 1.3.1

Layoutfel som nådde produktion, och en version som säger vilken.

### KÖR fick hela fönstret

Hela gränssnittet ritades i vänstra fjärdedelen av ett brett fönster. Skalet
bar kvar tvåkolumnsgridden från de tolv menypunkterna: byggsidofältet är dolt i
KÖR, men **grid-spåret fanns kvar**, så arbetsytan hamnade i det och mätte
250 px mot ett 1392 px skal.

Båda lägena sätter numera sin egen layout. Arbetsytan hade dessutom två regler
med samma selektor, en för visning och en för bredd; de är sammanslagna,
eftersom två ställen som båda sätter bredd glider isär.

### Ett formulär som krävde mer plats än som fanns

Vid 924 px svämmade formuläret för extern admininloggning över sitt steg med
26 px. `repeat(3, minmax(180px, 1fr))` kräver 598 px, och ett byggsteg vid
924 px fönster har 572 px — sidofältet tar resten. Medieförfrågan som skulle
fällt ihop det lyssnar på fönstret, inte på ytan, så den slog aldrig till.
`auto-fit` räknar på den plats som faktiskt finns.

### Mindre

- Kryssrutor och radioknappar fick den globala fältregelns fulla bredd och
  14 px radie, och renderades som stora rundade fyrkanter.
- Stationsväljaren i KÖR › Trafik sträckte sig över hela fönstret.
- *Öppna TKL* bar webbläsarens länkunderstrykning bland knappar.

---

## 1.3.0

Adminen byggd om. Tolv menypunkter blir två lägen.

### KÖR och BYGG

Fem körflikar och fem byggsteg i stället för tolv menypunkter. `mode` på
`body` är roten: enda variabeln som byter hela skelettet. Byggläget har eget
mörkt topplock, numrerad stegräcka och en panel för osparade ändringar.

BYGG steg 1 gör källvalet till serverns driftläge i stället för en etikett.
Steg 2 visar stationer, sträckor och A–D-paneler ur ett enda API, med samma
form vare sig innehållet kommer från Cloud eller ett lokalt utkast. Steg 5
samlar de tre systemmenypunkterna.

### Ingen tyst Cloud-aktivering

Pollern hämtade var femtonde sekund och anropade `install()`, vars `activate`
är `True` som standard, och sedan `request_restart()`. En operatör som rättat
tre avgångstider kl 13 förlorade dem när Cloud publicerade kl 14 — och träffen
startade om medan det hände.

Nu hämtar den, lagrar, markerar som väntande och slutar. `/v1/runtime/pending`
svarar med en **diff**, inte en räkning, och aktivering kräver att revisionens
id skickas tillbaka.

### Tidtabellen går att rätta i Cloud-läge

Den gamla grinden stängde varje skrivväg medan Cloud var redaktör, vilket
gjorde en rättad avgångstid omöjlig av samma skäl som en omritad bana. Under
träffen *är* servern driften. Grinden gäller numera bara stationer, sträckor
och paneler.

### Mindre

- Typsnitten serveras från servern i stället för från Google.
- Ett CSP-fel som legat på `main` är lagat: stapelbredder sattes med
  `style`-attribut, som serverns egen `style-src 'self'` avvisar.

---

## 1.2.0

Den första versionen där servern kan rätta en träff som redan är igång.

### Servern redigerar det Cloud publicerat (D2)

Servern kunde alltid bygga en egen konfiguration. Vad den **inte** kunde var
att redigera den Cloud publicerat — vilket är det enda som är värt att rätta
under en träff, när problemet är en tid i en tidtabell som gick ut för en
timme sedan.

Ett hämtat paket går nu att öppna som arbetskopia. Aktivering skriver en ny
paketrevision `<bas>+local-rN` genom samma maskineri som en Cloud-publicering,
så TKL och boxarna ser en `config_version` de inte sett och läser om. Rutter
och tjänststopp härleds ur tågraderna, så en rättad tid kan inte säga emot
tidtabellen som visar den.

Särfallet «topologi utan trafik» är borta: ett lokalt paket byggdes förut med
`trains: []`, alltså en järnväg utan tåg på.

### Driftlägen, och synlig kastning (D3, D4)

`cloud-linked` betyder att Cloud är redaktör och servern vägrar lokala
ändringar. `offline-meet` öppnar dem. Läget är beständigt tillstånd som en
människa satt och härleds **aldrig** ur om Cloud svarade just nu — ett
nätavbrott låser inte upp redigering, och ett nät som återkommer låser inte
mitt i någons arbete.

Att gå tillbaka till Cloud kastar de lokala revisionerna, och aldrig tyst:
servern visar först exakt vilka rader som ändrats, lagts till eller tagits
bort, och kräver bekräftelse.

### Uppdateringsförloppet

Sju verkliga steg i stället för tre, med **hälsokontroll före klart**. Förut
skrevs `complete` innan omstarten, så en administratör fick veta att
uppdateringen lyckats innan den provats en enda gång. `GET /healthz` är ny.

### Enkelriktat flöde mot Cloud (D1)

Förslagskön är borttagen ur båda repona. Cloud publicerar, servern hämtar,
ingenting går tillbaka.

### Spårbeläggning (D5)

`track_occupied` stod i protokollets lista över avslag utan att kunna
inträffa. Nu kan det: ett spår som redan är tilldelat en icke-avgången
rörelse går inte att tilldela igen, oavsett väg in.

### Mindre

- Inloggningsfältet är tomt. Applikationen fyller inte i användarnamnet, och
  `/v1/auth/status` lämnar det inte längre till oautentiserade anrop.
- Typsnitten serveras lokalt i stället för från Google Fonts, som serverns
  egen CSP avvisade vid varje sidladdning.
- Versionsnumret är riktigt, inte en git-sha. Commit-id finns kvar som
  bygginformation under **Teknisk information**.

### Kompatibilitet

| Kontrakt | Version |
|---|---|
| TMBox-protokoll | `protocol_version: 2` |
| Driftpaket från Cloud | `schema_version: 3` |
| TrainMeet Cloud | 1.0.0 eller senare |

Befintlig träffdata bevaras. Utkast sparade före 1.2.0 migreras vid läsning.
