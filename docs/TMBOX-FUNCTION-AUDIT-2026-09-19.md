# TMBox: funktionsrevision, flöden och gemensam produktmodell

Datum: 19 september 2026. Baslinje: TrainMeet Server 1.8.0 (`9b59e218`), TMBox 0.4.6 (`94a99350`). Detta är en kod- och testbaserad granskning, inte en certifiering av fysisk hårdvara.

## 1. Slutsats och rekommendation

**Ja: behåll en enda produktversion av TMBox, byggd för olika enheter. Men logiken är inte likadan i de två nuvarande klientvägarna.** ESP8266 använder V1-protokollet och en serverritad 16×2-panel. ESP32 använder V2-protokollet, lokalt gränssnitt och en annan serverväg för trafikärenden. Gemensamt versionsnummer finns redan; funktionslikhet återstår.

Varken två/fyra rader eller 16/20 tecken ska definiera en ny produktversion. Separera fyra saker:

| Egenskap | Exempel | Ägare |
| --- | --- | --- |
| Produktrelease | TMBox 0.4.6 | Gemensam releaseprocess |
| Hårdvarumål | NodeMCU ESP8266, ESP32 Classic, ESP32-S3 | Byggsystem och enhet |
| Displayprofil | 16×2, 20×2, 16×4, 20×4 | Enhetens kapacitet, godkänd av admin |
| Protokoll/kontrakt | Legacy V1, nuvarande V2, framtida gemensamt kontrakt | Server och versionsförhandlad klient |

Rekommendationen är en **gemensam auktoritativ trafikmotor på servern**, med tunna adapterlager för gamla och nya boxar. Enhetens program hanterar Wi-Fi, knappar, lokal inmatning, display och återanslutning. Den får aldrig själv besluta om körklarhet, beläggning, stationstilldelning eller avgång.

Byt inte ut den fungerande ESP8266-trafikvägen mot dagens ESP32-väg rakt av. Då skulle fungerande direkttrafik och återtagning gå förlorade. Portera inte heller hela ESP32:s minnesmodell till ESP8266 utan mätningar.

### Viktigast att åtgärda före löfte om likvärdig trafik

1. Avgångskedjan på ESP32: efter klartecken prioriteras fortfarande en ny begäran framför Avgått.
2. Direkttrafik och återtagning före avgång måste fungera från båda knappsatserna.
3. Ta bort Närmar sig ur det normala boxflödet; ankomst ska inte kräva detta extra steg.
4. Registrera Ankommit och faktiskt spår i **samma** servertransaktion.
5. Gör virtuell TMBox till en riktig, separat enhet med unik identitet och administratörens tilldelning.

Den här revisionen ändrar dokumentation/katalog, inte dessa trafikregler. En skärmbild eller ett lyckat kommandotest är inte bevis på en komplett körning mellan två operatörer.

## 2. Vad granskningen faktiskt omfattar

Granskat: V1-servermotor och LCD-rendering, V2-stationstjänst och tillåtna handlingar, ESP8266-program, ESP32:s navigation/renderare, webbspegeln, fixtures/golden-tester och publicerade byggresultat.

Inte verifierat här: Bennys verkliga koppling, nätets multicastförhållanden, LCD-teckenuppsättning, kontaktstuds under lång användning, strömförsörjning, aktuell minnesfragmentering eller uthållighetstest på fysiska kort. Tidigare användarrapport visar att automatisk anslutning fungerar; det är inte samma sak som ett fullständigt test av alla trafikflöden.

| Mått | Uppmätt i kod/testdata |
| --- | ---: |
| V1 interaktionslägen | 8 |
| V1 linjetillstånd | 4: free, requested, reserved, occupied |
| V1 trafiklägen | 2: clearance och direct |
| V1 motstationsplatser per panel | Högst 4, A–D |
| Maximal numerisk tågnummerlängd i båda klienterna | 5 siffror |
| ESP32 skärmtyper | 19 |
| Tidigare skärmkatalog | 15 exempel, 14 olika skärmtyper |
| Kompletterad ESP32-katalog | 20 exempel, alla 19 skärmtyper |
| ESP32 renderingsgeometrier | 4 |
| Ursprungliga golden-renderingar | 15 × 4 = 60 |
| Kompletterade renderingskontroller | 20 × 4 = 80 |
| Befintliga tekniska ESP32-tangentsekvenser | 12, mot frysta snapshots |
| Ny granskad ESP32-funktionsguide | 13 funktionsområden |
| V1 verkliga motorflöden i katalogen | 8 flöden, 40 accepterade steg |
| Kompletterad ESP8266-skärmkatalog | 8 motorlägen + 18 firmwareexempel = 26 |
| V2 serverhandlingar | 13, varav 1 teknisk configkvittens |
| Av de 12 operatörshandlingarna som har ESP32-navigation | 10; cancel och publish saknar tangent |

En siffra för antal tester mäter inte ensam funktionstäckning. De tidigare 12 sekvenserna tar inte emot serverkvittens mellan stegen och kan därför dölja ett trasigt helhetsflöde.

### Minnes- och storleksmätning

Exempel ur den publicerade [byggkörningen för TMBox 0.4.6](https://github.com/beahead-ab/trainmeet-tmbox/actions/runs/35452161354):

| Byggvariant | Program/flash | Statisk RAM |
| --- | ---: | ---: |
| ESP8266 PlatformIO, produktionsvariant | 446 351 / 1 044 464 byte, 42,7 % | 36 676 / 81 920 byte, 44,8 % |
| ESP32 Classic PlatformIO | 976 917 / 1 310 720 byte, 74,5 % | 50 176 / 327 680 byte, 15,3 % |
| ESP32-S3 PlatformIO | 942 749 / 3 342 336 byte, 28,2 % | 50 216 / 327 680 byte, 15,3 % |

Debug- och Arduino-byggen har andra storlekar. Flashgränsen ovan gäller vald **applikationspartition**, inte all flash på kortet. Statisk RAM är inte högsta verkliga RAM-användning: nätverk, JSON, buffertar och fragmentering tillkommer.

ESP8266 har alltså utrymme för dagens tunna klient, men siffrorna bevisar inte att den klarar ESP32:s fulla stationssnapshot. Mät ledig heap och största sammanhängande block vid anslutning, uppdatering och maximal träff. Begränsa meddelandestorlek och skicka bara aktuell sida/ärende till små enheter.

## 3. Funktionsmatris: nuläge, inte målbild

"Ja" betyder att kodvägen finns, inte att samtliga kort har provats fysiskt. "Delvis" betyder att en komponent finns men att det efterfrågade helhetsflödet avviker eller har en lucka.

| Nr | Krav | ESP8266/V1 | ESP32/V2 |
| --- | --- | --- | --- |
| 1 | Upptäck lokal server automatiskt | Ja | Ja |
| 2 | Admin tilldelar station | Ja, via V1-panel | Ja, via assignment/config |
| 3 | Siffror lokalt före sändning | Ja, # skickar hela numret | Ja, A söker idag |
| 4 | Begär → klart → faktisk avgång | Ja | Delvis: prioriteringslucka efter klart |
| 5 | Neka en begäran | Ja | Ja |
| 6 | Direkttrafik utan mottagarens klartecken | Ja | Saknar komplett tangentflöde |
| 7 | Återta väntande begäran | Ja | API finns, tangent saknas |
| 8 | Återta reservation efter klart, före avgång | Ja | Saknas i detta V2-flöde |
| 9 | Ta emot utan ett Närmar sig-steg | Ja | Delvis: Ankommit finns, A väljer först Närmar sig |
| 10 | Ta emot och välja faktiskt spår atomärt | Saknas | Saknas |
| 11 | Klockan alltid synlig | Nej | Nej |
| 12 | Vem som helst startar virtuell box; admin tilldelar | Inte dagens V1-webbtestmodell | Inte dagens admin-testklient |
| 13 | Alla fyra displayformat | Nej, fast 16×2 | Renderarstöd finns |

Med exakt dessa 13 kriterier har V1 9 Ja och 4 Nej, V2 5 Ja, 2 Delvis och 6 Nej. **Det är ingen kvalitetsprocent**, utan ett sätt att synliggöra varför nyare protokoll inte automatiskt betyder mer komplett trafikfunktion.

V1:s motor arbetar med linjeärenden. V2 arbetar med tidtabellsrörelser, uppställning, förare, spår och separat klarering. Dessa objekt måste mappas uttryckligen, inte bara döpas om. V1:s fyra anslutningar och V2:s riktade kanaler/dubbelspår är också olika modeller.

## 4. ESP8266: samtliga trafiklägen och flöden

### Skärmlägen

| Läge | Syfte | Tillåtna operatörsval |
| --- | --- | --- |
| idle | Motstationer och status | A–D väljer tilldelad anslutning |
| enter_train | Ange tågnummer | Siffror lokalt, # bekräftar, * lämnar |
| awaiting_permission | Väntar på mottagaren | * återtar begäran direkt |
| incoming_request | Inkommande begäran | A klart, B neka, * lämnar obesvarad |
| ready_departure | Klar/reserverad men inte avgången | A öppnar avgångsbekräftelse, * öppnar återtagning |
| confirm_departure | Bekräfta fysisk avgång | A avgått, B eller * tillbaka utan avgång |
| confirm_cancel | Bekräfta återtagning | # återtar, * tillbaka |
| incoming_arrival | Avgånget tåg på väg in | A ankommit, B eller * lämnar utan ankomst |

Viktigt: * betyder inte alltid bara Tillbaka i V1. Det kan återta en väntande begäran. Detta får inte döljas i en gemensam manual.

### F1. Skicka med klartecken och ta emot, nio steg

1. Avsändaren trycker motstationens A–D-tangent.
2. Skriver tågnummer lokalt och trycker #. En begäran skapas, linjen blir requested.
3. Mottagaren öppnar motsvarande anslutning.
4. Mottagaren trycker A för klart. Linjen blir reserved, tåget har inte avgått.
5. Avsändaren öppnar ärendet igen.
6. A öppnar frågan om faktisk avgång.
7. A bekräftar Avgått; linjen blir occupied.
8. Mottagaren öppnar inkommande tåg.
9. A bekräftar Ankommit; linjen blir free.

### F2. Skicka utan klartecken, sju steg

1. Välj motstation.
2. Skriv tågnummer och #. Serverns direct-inställning reserverar fri linje direkt.
3. Öppna reservationen.
4. A öppnar avgångsbekräftelsen.
5. A bekräftar faktisk avgång.
6. Mottagaren öppnar inkommande tåg.
7. A bekräftar ankomst.

Direct betyder inget mottagarbeslut, inte att linjebeläggning ignoreras eller att tåget automatiskt har avgått.

### F3. Neka, fyra steg

Välj motstation → tågnummer/# → mottagaren öppnar begäran → B nekar. Ärendet avslutas och linjen frigörs. En ny begäran kräver nytt operatörsbeslut.

### F4. Återta före klartecken, fyra steg

Välj motstation → tågnummer/# → avsändaren öppnar väntande begäran → * återtar direkt. Ingen avgång eller ankomst registreras.

### F5. Återta reserverat tåg, fem steg

Välj motstation → tågnummer/# → öppna reservation → * öppnar återtagningsfråga → # bekräftar. Detta fungerar för reservation före avgång. Det får inte användas för ett tåg som redan lämnat stationen.

### F6. Avbryt inmatning

Välj fri anslutning → skriv eventuella siffror → *. Den lokala bufferten kastas och ingen begäran skapas. Katalogens två serversteg visar öppna/lämna; de lokala siffrorna är inte nätverkskommandon.

### F7. Lämna en begäran obesvarad

Avsändaren skapar begäran → mottagaren öppnar → *. Begäran ligger kvar; detta är inte B=neka. Det är en viktig skillnad mot * i avsändarens vänteläge.

### F8. Backa från avgångsbekräftelse

Reservera → öppna ärendet → A öppnar avgångsfrågan → B eller *. Reservationen behålls, tåget registreras inte som avgånget.

### Övriga grenar och spärrar

- Mottagaren kan lämna ankomstvyn med B/* utan att frigöra linjen.
- Tomt nummer, fler än fem siffror, fel anslutning eller upptagen linje ger inte en lyckad ny begäran.
- Fel session, gammal revision eller utgånget kommando avvisas. Dubblettidentifiering skyddar mot samma kommando igen.
- En annan klients pågående panelinmatning får inte blandas med den lokala bufferten.
- Kvittens saknas: visa osäkerhet/hämta nytt läge, inte gissa att tåget skickats.
- Avgånget/occupied tåg återtas inte av det normala återtagningsflödet; mottagaren måste hantera ankomst.
- Klockan ligger i vilobilden om D-slotten inte används. Den försvinner i arbetsdialoger. "Alltid klocka" är därför inte uppfyllt.

### System- och installationsskärmar

Katalogen kompletteras med 18 separata firmwareexempel: start-ID; Wi-Fi-installation; fel serveradress; sparfel; V1-panel saknas; väntar på admin; hämtar panel; kommando nekat; server borta; flera servrar; söker server; ansluter; inget serversvar; nät saknas; display saknas; knappsats saknas; diagnostikens hårdvarutest; diagnostikens tangenttest. De två sista hör till separat diagnostikbygge, inte normal trafik. Exempel-ID är inte riktiga anslutningskoder.

## 5. ESP32: alla 19 skärmtyper

| Grupp | Skärmtyp | Funktion/utgång |
| --- | --- | --- |
| Livscykel | Identity | Visar enhetsidentitet |
| Livscykel | NoNetwork | Nät saknas; återförsök |
| Livscykel | SetupPortal | Wi-Fi-installation och accesspunktsnamn |
| Livscykel | SeekingServer | Söker lokal server |
| Livscykel | ServerGone | Serverkontakt förlorad |
| Livscykel | AwaitingAssignment | Väntar på admins stationstilldelning |
| Livscykel | LoadingStation | Hämtar stationskonfiguration/läge |
| Livscykel | ResettingNetwork | Pågående nätåterställning |
| Arbete | StationOverview | C öppnar rörelser, # öppnar prioriterad korg, siffror startar sökning |
| Arbete | MovementDetail | A primärhandling, B spårval när tillåtet, C nästa, * tillbaka |
| Arbete | TrackPicker | C väljer spår, A skickar, * tillbaka |
| Arbete | ConnectionPicker | C väljer motstation, A begär, * tillbaka |
| Arbete | TrainLookup | Siffror, B suddar, A söker, * lämnar |
| Arbete | LookupResults | C nästa resultat, # väljer, * lämnar |
| Arbete | ClearanceInbox | C nästa ärende, A klart, B neka, * lämnar |
| Arbete | LineInbox | C nästa, A kvitterar läsning, * lämnar |
| Kvittens | Sending | Kommando skickas, inte bevis för accepterad åtgärd |
| Kvittens | CommandAccepted | Servern accepterade |
| Kvittens | CommandRejected | Servern nekade; anledning visas |

En skärmtyp kan ha flera data-/textvarianter. 19 typer är inte 19 möjliga bildrutor. De 20 katalogexemplen omfattar både avgångs- och ankomströrelse.

### ESP32:s funktionella steg

1. **Start och anslutning:** Wi-Fi → discovery → fast enhets-ID → admin tilldelar → config/snapshot → stationsöversikt. HTTP-webbport och MQTT-port är separata transportfunktioner, inte samma inställning.
2. **Sök tåg:** siffror lokalt → A söker → en träff öppnas direkt när den finns i snapshot; flera/övriga resultat visas i resultatvyn → C och # väljer. Inga siffror ska gå som enskilda trafikbeslut.
3. **Bläddra:** C från översikt → C nästa rörelse → * tillbaka. Ingen särskild Tåg ut/Tåg in-växling via D finns idag.
4. **Uppställning:** avgång med status none → A skickar train.position.set → serverns kvittens/nya läge.
5. **Förare redo:** positioned → A skickar train.crew_ready.set → ready härleds på servern. Detta är separat från klart från motstationen.
6. **Begär:** ready → A öppnar anslutningar → C väljer → A skickar clearance.request. Upptagen kanal ska neka ny begäran.
7. **Svar:** mottagaren # → eventuell C → A klart eller B neka. Bara rätt mottagare får svara och gammalt ärende ska inte kunna avgöras igen.
8. **Avgång efter klart:** här finns en integrationslucka. `_allowed_actions` fortsätter erbjuda clearance.request och train.departed för ready. Klientens `PRIMARY_ORDER` väljer request först. Koden filtrerar inte bort request utifrån godkänt ärende här. Ett isolerat train.departed-test bevisar inte att användaren kan nå åtgärden med A.
9. **Direkttrafik:** V2-request skapar väntande ärende utan motsvarande direct-gren. Det finns därför inte ett verifierat, likvärdigt operatörsflöde.
10. **Återta:** serverns clearance.cancel kräver waiting; navigationen skickar aldrig handlingen. Godkänd men ej avgången reservation omfattas inte heller av denna cancel-gren. * är lokal navigation.
11. **Ankomst:** på en ren ankomst i none erbjuder servern approaching och arrived; A-prioriteten tar approaching först. Nästa A kan ta arrived. Det är inte önskat binärt Tåg in/Ankommit.
12. **Spårbyte:** B öppnar spårväljaren om train.track.change tillåts → C väljer → A skickar. Servern kontrollerar spår och beläggning; spårbyte kan ogiltigförklara klarering. En ren ankomst erbjuds inte detta spårbyte, och train.arrived använder inte commandots track_id. Därför saknas efterfrågad atomär Ankommit på valt spår.
13. **Linjemeddelande:** # öppnar korgen om klareringskorgen inte har prioritet → C väljer → A kvitterar läsning. Inte körklarhet eller frigivning. Publicera linjemeddelande finns som API men inte som tangentval.

### V2:s 13 serverhandlingar och faktisk räckvidd

| Handling | ESP32-nåbarhet | Bedömning |
| --- | --- | --- |
| train.position.set | A på none-avgång | Finns, föreslås valfri/sammanslagen |
| train.crew_ready.set | A på positioned | Finns, föreslås valfri/sammanslagen |
| clearance.request | A och motstationsval | Finns; prioritering/direct behöver rättas |
| clearance.response | A/B i klareringskorg | Finns |
| clearance.cancel | Ingen tangent | Ofullständigt operatörsflöde |
| train.departed | A om högre prioriterat request inte erbjuds | Helhetslucka med normala ready-data |
| train.approaching | Första A på ny ankomst | Finns, ska döljas/deprecieras i boxprofilen |
| train.arrived | A efter approaching | Finns; faktiskt spår måste göras atomärt |
| train.track.change | B → C/A | Finns för tillåtna rörelser, inte alla ankomster |
| train.lookup | A i sökning | Finns |
| line.available.publish | Ingen tangent | API utan komplett boxflöde |
| line.available.acknowledge | A i linjekorg | Finns, inte ett trafikbeslut |
| device.config.ack | Teknisk klientkvittens | Ingen operatörsåtgärd |

Dessa är inte 13 kompletta användarflöden. En handling kan kräva flera skärmar och ett flöde kan använda flera handlingar.

## 6. Felvägar som måste stå i både manual och tester

| Situation | Nuvarande mekanism / behov i gemensamt kontrakt |
| --- | --- |
| Ingen Wi-Fi eller server | Tydlig offlinebild, återförsök, inga nya beslut baserat på gammal data |
| Flera servrar upptäcks | Explicit val/bindning; inte slumpmässig stationstilldelning |
| Enhet hittad men ej tilldelad | Visa ID och Väntar på admin; inte egna stationsrättigheter |
| V1-panel saknas | Skilj fungerande transport från saknad administrativ panelkoppling |
| Saknad LCD/knappsats | Diagnostik; webbtest får visa status utan att låtsas att fysisk komponent finns |
| Ogiltigt tåg/ingen sökträff | Bevara/återställ inmatning begripligt, välj inte automatiskt ett annat tåg |
| Flera rörelser med samma nummer | Välj movement/sträcka/dag, inte bara första numret |
| Upptagen linje/spår | Avvisa, visa orsak och aktuellt läge |
| Nekat klartecken/timeout/återtagen begäran | Skilj dessa utfall från varandra, ingen tyst avgång |
| Revision/config/generation har ändrats | Avvisa gammalt kommando, hämta nytt läge, kräv nytt beslut |
| Dubbelt tryck eller tappad kvittens | Kommando-ID och idempotens; återförsök får inte skapa två händelser |
| Serveromstart | Återskapa accepterat tillstånd; inte automatiskt frigöra upptagen sträcka |
| Byte av station eller träff | Gamla buffertar/ärenden slutar vara giltiga i den nya kontexten |
| Display mindre än texten | Anpassad layout/sidor, inte klippta säkerhetskritiska etiketter |

ESP32 har 500 ms inmatningslås efter skärmbyte. ESP8266 har separat avstudsning och skydd för kvittenser/färsk snapshot. Dessa mekanismer löser olika problem och ska inte ersätta serverns idempotens och tillståndskontroll.

## 7. Föreslaget gemensamt kontrakt

```text
Cloud: träff, stationer, sträckor, tidtabell, publicerad config
                      ↓
TrainMeet Server: en vald träff, behörighet och stationstilldelning
                      ↓
Gemensam EU-trafikmotor: begär / klart / neka / återta / avgått / ankommit
                ↙                         ↘
Legacy-adapter för V1              Kapacitetsstyrt nytt boxkontrakt
ESP8266 16×2                      ESP32 / virtuell box / framtida tunn 8266
```

US:s dispatcher-/warrantlogik ska fortsatt vara separat. Gemensam infrastruktur innebär inte att EU:s linjeklarering automatiskt blir US-behörighet. Dagens V2-boxservice kräver vald EU-publication; den är inte en allmän US-dispatcherklient.

### Trafiktillstånd och tillåtna åtgärder

Normal kedja: fri → begärd (om klartecken krävs) → reserverad → avgången/på väg in → ankommen/fri. Avslag eller återtagning före avgång frigör berört ärende. Avgången får inte återtas som om den aldrig skett.

Servern ska returnera ett explicit meny-/handlingsunderlag för aktuell operatör, exempelvis Begär, Avgått eller Ta emot. Klienten ska inte välja affärsregel genom en hårdkodad prioritetslista över konkurrerande handlingar.

Varje ändrande kommando behöver åtminstone:

- Unikt kommando-ID, enhetsidentitet och giltig serverutfärdad behörighet.
- Träffgeneration, stationsassignment och förväntad ärende-/rörelserevision.
- Semantisk handling, movement/ärende/anslutning och vid behov faktiskt spår.
- Tydlig kvittens med accepterat/avvisat/orsak och nytt auktoritativt läge.

Exempel: `arrive(movement_id, actual_track_id, expected_revision)` ska validera mottagarstation, faktisk avgång, spår och beläggning samt registrera ankomst och spår tillsammans. Om något misslyckas ska ingendera delen sparas. Planerat spår ligger kvar som planering.

### Tangentprinciper, utan att överraska gamla boxar

Föreslagen ny profil: siffror buffras lokalt; # bekräftar data; * tillbaka/töm inmatning; A positivt trafikbeslut; B tydligt märkt alternativ/nej; C nästa; D exempelvis Tåg ut/Tåg in. Återtagning ska ha ett uttryckligt menyval och bekräftelse.

Detta är ett förslag, inte dagens gemensamma beteende. V1:s A–D väljer motstationer och * kan återta direkt; de får inte byta betydelse tyst i en uppdatering. Behåll en versionsmärkt legacy-profil under övergången och ge admin ett kontrollerat profilbyte med instruktion.

### Display och klocka

- 16×2 har 32 tecken totalt; 20×4 har 80, alltså 2,5 gånger textytan.
- 20×2 har 40 och 16×4 har 64 tecken. Skillnaden är presentation, inte trafikregler.
- För små skärmar krävs korta, entydiga etiketter och sidindelning. Reservera t.ex. en fast tidsyta där den inte kan förväxlas med tågnummer.
- Klocka och trafiktid kommer från servern. Vid frånkoppling markeras stale/offline; en lokalt tickande gammal tid får inte se bekräftat synkroniserad ut.
- Admin väljer bara format enheten faktiskt rapporterar stöd för. En fysisk 16×2-panel kan inte bli 20×4 genom ett serverval.
- Gör en läsbarhetsgranskning med riktiga operatörer innan "alltid klocka" låses till en layout som skymmer beslut.

### Virtuell TMBox

Vem som helst på avsett lokalt nät kan öppna en separat startadress och skapa en **väntande** virtuell enhet, utan att få administratörsbehörighet. Servern utfärdar en egen hemlig enhetscredential och ett offentligt visnings-ID. Admin ser ID:t och tilldelar station/display. Enheten kan inte välja en annan fysisk boxs ID och överta den.

Webbens knappar går lokalt till samma klientadapter; semantiska kommandon går till samma motor som fysisk hårdvara. Ny webbläsare/rensad lagring ska bli en ny väntande identitet, inte ärva stationsrättigheter. Begränsa registreringstakt/antal och ge admin återkallning/städning. ID är identifiering, inte lösenord.

Dagens admin-testklient med manuellt enhets-/stationsval är ett utvecklingsverktyg. Den ska inte beskrivas som färdig öppen virtuell TMBox. Webbtestet på ESP8266 testar i sin tur en verklig ansluten box via dess egen webbsida; det är ännu en annan funktion.

## 8. Vad som bör tas bort, döljas eller behållas

| Del | Föreslagen åtgärd | Skäl / skydd |
| --- | --- | --- |
| Närmar sig i boxflödet | Dölj i ny profil; depreciera separat handling efter konsumentinventering | Ingen mätning/NFC finns som stöder steget idag |
| Obligatoriskt Uppställt och Förare redo | Gör valfritt eller slå samman till uttrycklig förberedelse | Behåll serverdata/API om TKL fortfarande använder dem |
| Dubbla affärstillståndsmaskiner | Ersätt successivt med en kärna och protokolladaptrar | Ett beslut måste betyda samma sak i alla klienter |
| Hårdkodad A-prioritet i klient | Ersätt med serverstyrt handlingsunderlag | Förebygger Begär/Avgått-luckan |
| line.available.publish i normal boxmeny | Lägg inte till utan operatörsbehov; utvärdera senare | API finns, men inte färdig användarfunktion |
| Fritt stationsval i publik testklient | Ersätt med väntande unik enhet + adminassignment | Samma modell som riktig TMBox |
| V1/V2 som produktnamn efter skärmstorlek | Byt till TMBox + hårdvara/displayprofil | Behåll protokollversion tekniskt för kompatibilitet |
| Negativa svar, återtagning, revisionskontroller, logg | Behåll | Viktiga normala funktioner, inte onödiga särfall |
| Legacy V1 direkt efter migration | Behåll adapter tills full paritet bevisats | Fungerande äldre kort ska inte stängas ute |

Ta inte bort lagrade historiska approaching-värden eller TKL/andra klienters API i samma ändring som knapplayouten. Inventera konsumenter och introducera deprecieringsperiod först. NFC-utvecklingen är fortfarande uppskjuten enligt användarens önskemål.

## 9. Implementationsplan med mätbara leveranser

Följande är en **planeringsuppskattning**, inte uppmätt arbetstid eller en leveransgaranti. Förutsätter tillgång till ESP8266 och ESP32 samt minst 16×2 och 20×4 fysisk display.

| Fas | Leverans och klart-kriterium | Uppskattning |
| --- | --- | --- |
| A | Frys kontrakt/tillstånd; dokumentera nuvarande paritetsluckor; regression för ESP32 request/avgång | 1–2 utvecklardagar |
| B | Gemensam serverkärna/adaptrar, direkttrafik, avslag och återtagning före avgång | 4–7 dagar |
| C | Atomär ankomst med faktiskt spår; bortkopplat Närmar sig i ny boxprofil | 2–3 dagar |
| D | Tunna profiler för båda enheterna; lokal inmatning, fyra geometrier och klockyta | 3–5 dagar |
| E | Säker publik registrering av virtuell enhet + adminassignment/återkallning | 2–3 dagar |
| F | Blandade klienter, hårdvaruprov, nätfel, omstart, dokumentation och kontrollerad release | 3–5 dagar |
| Totalt | Före eventuell extra US- eller NFC-funktionalitet | 15–25 utvecklardagar |

Prioritera en smal lodrät leverans: **begär → klart → avgång → ankomst**, körd mellan en ESP8266, en ESP32 och en virtuell box mot samma motor. Lägg inte veckor på alla skärmvarianter innan denna kedja fungerar.

### Föreslagen verifieringsmatris

Sex presentationer: ESP8266 16×2, ESP32 i fyra geometrier, virtuell webbenhet. Detta är sex profiler, inte sex nya produktversioner.

Tolv basscenarier per profil: med klartecken; direkttrafik; neka; återta väntande; återta godkänd reservation; avbryt inmatning; backa från avgång; ta emot planerat spår; ta emot avvikande spår; neka belagt spår atomärt; dubblerat kommando; gammal kontext efter assignment-/träffbyte. **6 × 12 = 72 profilscenariofall.**

Tre representativa klientfamiljer (8266, 32, webb), nio riktade avsändare/mottagare-kombinationer inklusive samma familj. Kör tre kärnflöden i varje: klart/avgång/ankomst, neka, återta. **9 × 3 = 27 integrationsfall.**

Fyra felinjektioner per sex profiler: Wi-Fi försvinner, servern startar om, kvittens tappas, config ändras mitt i ett steg. **6 × 4 = 24 återhämtningsfall.** Tillsammans minst **123 definierade scenariofall**, utöver enhetstester, rendering och uthållighet. Detta är föreslagna acceptanstester, inte genomförda tester.

Godkänn endast om: inga dubbla trafikbeslut, inga frigivna upptagna linjer efter omstart, inga kommandon från fel station, ingen ankomst utan korrekt spårtransaktion och ingen borttappad fungerande V1-funktion. Mät även heap under minst en hel simulerad träffdag och faktisk knapp-/displayläsbarhet.

## 10. Vad som uppdateras i denna leverans

- Flöden/Skärmkatalog får separat dokumentationsval **ESP8266/V1** och **ESP32/V2**. Det ändrar inte den riktiga boxens station, profil eller drift.
- V1:s åtta motorlägen och åtta flöden genereras genom riktiga accepterade kommandon i isolerad servermotor. Den levererade filen jämförs automatiskt mot generatorn.
- ESP8266:s ytterligare 18 firmwareexempel märks uttryckligen som dokumenterade texter, inte hårdvaruverifierade skärmar.
- ESP32:s saknade fem livscykelskärmar läggs till. Alla 19 typer finns nu, med 20 exempel och fyra geometrier.
- Tretton granskade funktionella områden skiljer fullständiga funktioner från kända luckor. De 12 äldre tangentsekvenserna behålls som tekniska tester och påstås inte längre bevisa hela trafikflöden.
- Missvisande referens om B alltid betyder EJ, * alltid avbryter trafik och framtida D=Närmar sig ersätts med faktisk profilberoende knappmodell.
- Ingen ny NFC-funktion, publik virtuell enhet eller sammanslagen trafikmotor påstås vara färdig genom dokumentationsändringen.

## 11. Källor och spårbarhet

Serverbas: [1.8.0-källan](https://github.com/beahead-ab/trainmeet-server/tree/9b59e218ba78647f809e9c061947acffb8e8ff64).

- `src/tmbox_gateway/models.py`: InteractionMode, ConnectionState, DispatchMode.
- `engine.py`: `_validate_command`, `_handle_key`, reservation, svar, återtagning, avgång och ankomst.
- `display.py`: V1:s riktiga LCD-rader och klockans villkor.
- `protocol_v2.py`: `_allowed_actions`, `_apply`, `_clearance`, lookup och linjemeddelanden.
- `operations.py`: beständiga rörelser/klareringar/linjekanaler.
- `web/tmbox-nav.js`, `tmbox-render.js`, `tmbox-fixtures.js`: webbspegel och testfixturer.
- `scripts/tmbox_legacy_catalog.py`, `tests/test_tmbox_catalog.py`: genererad V1-katalog och aktualitetskontroll.
- `tests/test_tmbox_fixtures.py`, `tests/test_tmbox_flow_notes.py`, `tests/js/tmbox-catalog.test.cjs`: rendering, prosegranskning och katalogtäckning.

Firmwarebas: [TMBox 0.4.6-källan](https://github.com/beahead-ab/trainmeet-tmbox/tree/94a993509c2d1864774d6d03e5099e847440c506), [release och nedladdningar](https://github.com/beahead-ab/trainmeet-tmbox/releases/tag/v0.4.6).

- `firmware/esp8266/TrainMeetTambox8266/`: huvudskiss, input_state, web_test, manual_server, hardware_profile.
- `firmware/esp32/lib/tmbox_core/`: navigation, renderer, model, attention, geometry, command och meet_scope.
- `platformio.ini`, byggprofiler och releasekörningens faktiska compiler-utskrifter.

Rapportens kodfynd är knutna till ovanstående versioner. När trafikmotorn ändras ska den funktionella matrisen och genererade katalogen uppdateras i samma PR, inte först efter nästa demonstration.
