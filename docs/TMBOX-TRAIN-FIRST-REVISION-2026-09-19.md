# TMBox – samma trafikarbete, tågnummer först

Datum: 19 september 2026. Status: **godkänd målbild, implementation påbörjad**. Den gemensamma trafikfunktionen är ännu inte aktiverad i klienterna.

## Implementationsstatus efter klartecken

De senaste kommentarerna gäller framför tidigare förslag: stationskoder i stället
för A–D-destinationer, klocka alltid till höger på sista raden, `?` för väntan,
öppen pil för klartecken och fylld pil först efter rapporterad faktisk avgång.
A–D är enbart funktionsknappar i den nya profilen. Befintliga profiler ändras
inte tyst innan server och klienter kan uppdateras tillsammans.

Tillägg 20 september 2026: **operatören väljer boxens språk direkt i boxens
meny**. Kravet och acceptansen beskrivs i F11 nedan. Det är ännu inte
implementerat eller driftsatt; webbgränssnittets befintliga språkval är inte
likvärdigt med språk på den fysiska boxens display.

Infört lokalt:

- `train_routes.py` identifierar planerad delsträcka genom service, trafikdag,
  stationsbesök och exakt avsändande/mottagande rörelse. Tjugo tester täcker
  bland annat återbesök, samma nummer i olika turer, midnatt, flera vägar och
  saknade/motstridiga underlag. Den väljer aldrig en godtycklig motstation.
- V2:s befintliga `train.lookup` får den extra läsuppgiften `departure_route`.
  `resolved` betyder **identifierad plan**, inte klartecken eller position.
  `unresolved` anger orsaken; tåget finns fortfarande kvar i uppslaget.
- [Extern FastClock](FASTCLOCK.md) hämtas centralt av TrainMeet Server, inte av
  Cloud eller varje klient. Inställningen sparas lokalt per träff.

Återstår före aktivering av den nya gemensamma TMBox-profilen:

1. Samordna V1:s linjetillstånd och V2:s ärenden/rörelser i en gemensam skrivväg,
   med ankomst, faktiskt spår, exakt frigivning och kommando-ID i samma beständiga
   transaktion. Dagens separata efterhandsobservatör får inte vara säkerhetsgräns.
2. Koppla behörighet, progression, revisionskontroll och de sex trafikåtgärderna
   till den identifierade delsträckan. Ett läsuppslag får inte återanvändas som
   ett gammalt körtillstånd efter configbyte.
3. Uppdatera virtuell box och båda firmwarebyggena tillsammans; testa blandade
   sändare/mottagare. Ingen Närmar sig- eller readiness-plikt i den nya profilen.
4. Koppla flöden/skärmkatalog till dessa körbara beteenden, inklusive hela
   tågnummers- och stationskodstexter, sidbyte när de inte ryms samt LCD-glyfer.

NFC-utvecklingen och Cloud #22 är fortsatt uppskjutna. EU:s stationsregler ska
inte ersätta US:s Track Warrant-regler. Inget i detta dokument innebär att den
nya profilen redan är installerad på Bennys box.

## 1. Rekommendation

**Utgå från ESP8266-boxens fungerande trafikarbete. Låt ESP32 göra samma saker, med bättre presentation – inte med fler obligatoriska arbetsmoment.**

Operatören anger tågnumret. Servern känner till boxens tilldelade station och använder träffens publicerade tidtabell, tågturens ordning och registrerade trafikläge för att identifiera rätt utgående sträcka. Normalt behövs inget motstationsval.

Exempel från önskemålet: boxen är tilldelad Charlottendal, operatören anger 93 och servern identifierar Vagnhärad som nästa mottagande station. Det är ett **illustrativt exempel**, inte en verifiering av träffens verkliga tidtabell.

Fyra principer:

1. Admin tilldelar station åt både fysisk och virtuell box. Operatören väljer tåg, inte sin behörighet.
2. Servern bestämmer nästa operativa sträcka. Boxen visar den tydligt före beslutet.
3. Begäran/reservation är inte avgång. Avgång och ankomst rapporteras fortfarande uttryckligen.
4. Tidtabellen beskriver planen. Utan detektering vet servern bara det trafikläge som människor faktiskt har rapporterat.

Detta förslag gäller det stationsbaserade EU-trafikarbetet. US:s dispatcher-/Track Warrant-regler ska inte ersättas med samma klareringsflöde. NFC ligger fortsatt utanför arbetet.

## 2. Vad skiljer plattformarna faktiskt åt?

Granskat serverträd: `f68b9b2` ovanpå Server 1.8.1, med pågående lokala ändringar för offentliga arbetsytor. Firmwaregranskning: lokalt `trainmeet-tambox` vid `a7022f1`; den granskade ESP32-navigationen överensstämmer även med lokalt känd `origin/main` vid `8895447`. Ingen ny kontroll av installerad firmware eller produktion ingår.

| Funktion | ESP8266:s nuvarande väg | ESP32:s nuvarande väg | Föreslagen gemensam modell |
| --- | --- | --- | --- |
| Välj tåg/sträcka | Välj A–D-motstation, skriv nummer, # | Sök nummer med A; välj rörelse och sedan motstation | Nummer + #, servern identifierar rörelse och sträcka |
| Siffror medan man skriver | Lokal buffert i uppdaterad firmware | Lokal buffert | Lokalt, ett komplett uppslag vid # |
| Uppställt och förare redo | Inte obligatoriska boxsteg | Två separata steg före normal begäran | Inga extra boxsteg som standard |
| Begär, klart och neka | Finns | Finns, men avvikande flöde | Samma regler och besked |
| Direkttrafik | Reserverar utan mottagarens klartecken | Saknar likvärdig komplett knappkedja | Serverns inställning styr; avgång bekräftas alltid |
| Återta före avgång | Väntande begäran och reservation | Väntande cancel-API, ingen normal tangent; godkänd reservation omfattas inte | Uttrycklig återtagning i båda lägena |
| Faktisk avgång | Kräver reserverad sträcka | Tillståndsändring i annan serverväg | Samma kontrollerade övergång för alla klienter |
| Ta emot | Direkt Ankommit | Närmar sig prioriteras före Ankommit | På väg in → Ankommit, inget Närmar sig-steg |
| Avvikande ankomstspår | Inte sammanhållet flöde | Separat spårfunktion, inte atomär ankomst med valt spår | Ankommit + faktiskt spår sparas tillsammans |
| Presentation | Fast 16×2 | Renderarstöd för 16×2, 20×2, 16×4, 20×4 | Samma funktioner, anpassad text/sidindelning |

Det är alltså inte en ren skillnad i displaystorlek. Det finns två trafikvägar som behöver förenas. Ett gemensamt produktversionsnummer räcker inte för att uppnå samma beteende.

### Mätbara skillnader

- Gamla servermotorn har **6 centrala trafikåtgärder**: begär, klart, neka, återta, avgång och ankomst.
- Den nuvarande V2-tjänsten har **12 operatörshandlingar**, plus teknisk configkvittens. Fler API-handlingar betyder inte fler färdiga operatörsfunktioner.
- ESP32 har **19 skärmtyper**, inklusive start-, nätverks- och kvittensbilder. Dessa ska inte räknas som 19 trafikfunktioner.
- Förslaget tar bort **4 obligatoriska val/steg där de förekommer**: motstationsval, Uppställt, Förare redo och Närmar sig. Uppslag och tydliga trafikbeslut behålls.
- 16×2 ger **32 tecken**, 20×2 ger **40**, 16×4 ger **64**, 20×4 ger **80**. ESP32:s största format ger 2,5 gånger textytan, inte 2,5 gånger fler trafikregler.

## 3. Viktiga kodfynd – mer än en gränssnittsändring

Fyra små prov kördes mot ny, isolerad testdatabas och befintliga testfixturer. Inget skickades till den riktiga servern eller en fysisk box.

| Prov | Resultat i granskad kod |
| --- | --- |
| ESP32/V2: skicka `train.departed` för giltig egen stationsrörelse utan tidigare reservation | Accepterat |
| ESP32/V2: begär sträckan CDA–KUN när fixturens tågtur går CDA–VST | Accepterat; mottagaren blev KUN |
| ESP32/V2: registrera ankomst utan föregående utskick | Accepterat |
| Gamla motorn: avgång utan reservation | Nekat: `departure_not_reserved` |

Detta beskriver direkta kommandoprov med behörig testenhet. Det betyder inte att vanliga knappar alltid erbjuder dessa vägar. Poängen är att servern måste kontrollera dem även när en klient skickar fel kommando.

Dessutom:

- V2:s handlingslista erbjuder både Begär och Avgått för ett redo tåg. Klientens fasta prioritetslista väljer Begär först. Det kan hindra den normala övergången till Avgått efter klart.
- V2:s ankomstfrigivning söker godkända ärenden utifrån **tågnummer och mottagarstation**, inte en exakt länk mellan avsändande och mottagande rörelse. Samma nummer i flera körningar blir därför ett problem som måste lösas.
- V1:s positionslagring har tågnummer som primärnyckel. Den behöver utökas innan samma nummer kan representera flera samtidiga körningstillfällen säkert.
- Befintlig TKL-hjälpfunktion hittar grannar efter nummer och första stationsförekomst. Den är användbar för enkel visning, men ska inte återanvändas oförändrad som auktoritativ vägvalsalgoritm.

**Slutsats:** ta inte bara bort `ConnectionPicker` och välj första grannen i listan. Inför först gemensam identifiering och validering på servern.

## 4. Hur servern hittar rätt mottagare

Driftpaketet innehåller redan `services`, tågradens `service_id`, ordnade stopp och uppgifter om trafikdag/tid. Vi behöver använda och stärka dessa kopplingar, inte uppfinna en ny tidtabell.

Vid inmatning av exempelvis 93:

1. Läs station och rättigheter ur aktuell enhetstilldelning, aldrig ur ett fritt stationsfält från klienten.
2. Begränsa till vald träff, körningsomgång och trafikdag.
3. Hitta relevanta tågturer och stationsrörelser för numret. Bevara numret som text; inledande nollor får inte tappas.
4. Identifiera rätt besök på stationen och rätt riktning med tågturens stoppordning och redan registrerade händelser.
5. Hitta nästa **operativa mottagare** och den sträcka/kanal som förbinder dem. Det är inte nödvändigtvis tågets slutdestination eller nästa ort som har en tryckt tid.
6. Kontrollera att tåget inte redan har skickats, att föregående relevanta rörelse är förenlig med läget och att sträckan får användas.
7. Returnera exakt rörelse, mottagare, sträcka, trafikläge och tillåtna handlingar till boxen.

Tre möjliga svar:

- **Entydigt:** visa rätt tåg och mottagare direkt.
- **Flera möjliga körningar:** visa en kort lista med exempelvis tid, riktning och destination. Operatören väljer körning, inte en godtycklig mottagarstation.
- **Saknas eller är oförenligt:** visa vad som saknas och blockera utskick. Gissa inte närmaste avgång eller första granne.

Internt behövs en stabil identitet ungefär enligt `körningsomgång + trafikdag + service_id + stationsbesök + delsträcka`. Tågnumret är vad människan skriver; det är inte hela den tekniska identiteten. Publiceringsversion och träffgeneration skyddar mot gamla konfigurationer.

Varje ärende ska explicit länka avsändande rörelse, mottagande rörelse och reserverad kanal. Ankomst frigör **bara det ärendet**, inte alla ärenden med samma tågnummer.

## 5. Föreslaget knappflöde

Rekommenderad ny profil, gemensam för båda hårdvarorna och den virtuella boxen:

- **0–9:** lokal tågnummerinmatning.
- **#:** bekräfta data/slå upp tåget; inte registrera fysisk avgång eller ankomst.
- **A:** tydligt namngiven positiv handling på skärmen: Begär, Klart, Avgått eller Ankommit.
- **B:** tydligt namngivet alternativ/nej; under inmatning sudda sista siffran.
- **C:** bläddra i tåg, ärenden eller tillåtna spår.
- **D:** visa fler relevanta val, exempelvis Byt spår eller Återta. Ingen dold trafikändring av bara D.
- **\*:** tillbaka/avbryt lokal redigering. Återtagning av trafik kräver eget uttryckligt val och bekräftelse.

Etiketterna måste beskriva den aktuella handlingen. Dagens V1-beteende med A–D som motstationer och * som direkt återtagning får inte bytas tyst. Gamla firmwareversioner behåller sin dokumenterade profil tills uppdaterad firmware och serverstöd aktiveras tillsammans.

### Exempel: skicka 93

`9 → 3 → # → ”93 till Vagnhärad” → A Begär`

Motstationen visas men väljs inte. Om klartecken krävs visas Väntar på klart. När det kommit visar boxen Klar för avgång. Operatören rapporterar Avgått först när tåget verkligen lämnar stationen.

Jag rekommenderar mottagargranskningen även när servern är säker. Då upptäcker operatören felslaget tågnummer eller felaktigt underlag innan begäran skickas.

Detta är en avsiktlig kompromiss: för ett nummer med n siffror använder gamla inledningen n+2 tryck (motstation + nummer + #). Förslaget använder n+3 (nummer + # + A). För 93 blir det **4 mot 5 tryck**, men inget manuellt vägval. Vi ska inte marknadsföra det som färre tangenttryck; vinsten är färre beslut och lägre risk för fel mottagare. Ingen avgång ingår i dessa siffror.

## 6. Reviderad flödeskatalog

Följande är målbild, inte en beskrivning av redan levererad firmware.

### F1 – Starta box och tilldelas station

Anslut/upptäck server → visa unikt ID → vänta på admin → admin tilldelar station och kompatibel displayprofil → hämta aktuellt läge → visa station och träffklocka. Samma princip för fysisk och virtuell box. Att ansluta ger inte automatiskt rätt att styra trafik.

### F2 – Identifiera tåg

Skriv lokalt → # skickar komplett nummer → servern identifierar körning → visa tåg och relevant handling. Vid flera kandidater: C bläddrar, # väljer körning. Vid fel: bevara inmatningen så den kan rättas. Inga trafikändringar görs av uppslaget.

### F3 – Skicka med klartecken

Identifiera tåg → granska mottagare → A Begär → mottagaren öppnar begäran → A Klart → avsändaren ser klartecknet → explicit avgångsbekräftelse → A Avgått → mottagaren ser Tåg på väg in. Väntan, klartecken och faktisk avgång är olika tillstånd.

### F4 – Skicka utan klartecken

Identifiera tåg → granska mottagare och Direkttrafik → A Reservera → servern reserverar tillåten fri sträcka → explicit avgångsbekräftelse → A Avgått. Ingen fråga till mottagaren, men samma beläggnings- och tillståndskontroller. Boxen får inte själv välja direkttrafik.

### F5 – Neka eller lämna obesvarat

Öppna inkommande begäran → B Neka avslutar just begäran; avsändaren ser Nekat. * lämnar däremot vyn utan svar. Ett nytt försök kräver operatörens beslut, inte automatisk omsändning som ny begäran.

### F6 – Återta innan tåget avgått

Öppna egen väntande begäran eller klar reservation → D Fler → Återta → visa tåg och sträcka → A bekräftar. Servern kontrollerar att avgång inte hunnit ske, återtar och informerar båda sidor. Efter avgång finns ingen vanlig Återta-funktion.

### F7 – Ta emot på planerat spår

Öppna inkommande ärende, från listan eller via tågnummer → visa avsändare och planerat spår → när hela tåget kommit in och den berörda sträckan är fri: A Ankommit → servern registrerar ankomst/spår och frigör exakt rätt ärende tillsammans. Inget Närmar sig-steg.

### F8 – Ta emot på annat spår

Öppna inkommande ärende → D Fler → Byt ankomstspår → C väljer bland stationens tillåtna spår, # bekräftar valet lokalt → visa slutlig ankomstbild → A Ankommit. Ankomst och faktiskt spår sparas i samma transaktion; planerat spår bevaras som plan. Vid konflikt sparas ingendera delen och valet finns kvar för rättning.

### F9 – Avbryta redigering och hantera samtidighet

* innan beslut lämnar utan trafikändring. Bekräftad begäran ligger kvar även om operatören går hem i boxen. Ändras ärendet från en annan box ska ett gammalt beslut avvisas, färskt läge visas och den nya handlingen kräva ett nytt tryck.

### F10 – Slutstation, genomgående tåg och återanslutning

Slutstation: efter ankomst finns inget utgående steg utan nästa körning. Genomgående tåg: behandla in- och utgående delsträcka separat; ingen automatisk avgång bara för att ankomst rapporterats. Nätfel/omstart: återläs serverns beslut, visa osäker status tills synk är klar och återanvänd kommando-ID vid kontroll av tappad kvittens. Skapa inte ett nytt utskick för att svaret försvann.

### F11 – Operatören väljer språk på sin TMBox

**Meny → Inställningar → Språk → välj → Spara.** Detta är en
operatörsinställning utan administratörsinloggning. På knappsatsen nås menyn
genom den nya profilens synliga Fler/Meny-funktion, inte genom en dold
tangentkombination eller genom att återanvända en tangent som just nu ger
klartecken, avgång eller ankomst. Det exakta menyinträdet ska ingå i den nya
profilens navigerings- och referensbildstester innan det aktiveras.

Språken visas med sina egna namn: **Svenska, Dansk, Norsk (bokmål), English,
Deutsch**. Valet gäller menyer, funktionsetiketter, trafikstatus, frågor och
felmeddelanden. Tågnummer, stationskoder, namn i träffunderlaget och klockans
värde ändras inte.

- Valet tillhör **enheten**, inte stationen, servern, Cloud-kontot eller den
  gemensamma webbläsarens språk. Två boxar på samma station får välja olika språk.
- Operatörens uttryckliga val sparas beständigt och överlever omstart,
  återanslutning, firmwareuppdatering utan dataradering och omtilldelning till
  en annan station. Nollställning av boxens data kan däremot radera valet.
- En box utan tidigare val använder träffens standard: engelska för US,
  svenska för EU om ingen annan standard har angetts. Innan träffen är känd
  används en dokumenterad reservinställning. Ett uttryckligt operatörsval
  skrivs aldrig över av Cloud-synk eller ny stationstilldelning.
- Den fysiska boxens webbtestvy speglar samma språkval som boxen. En separat
  virtuell box har ett eget val kopplat till sitt eget enhets-ID.
- Spara verkställer valet och ritar om aktuell vy utan att ändra trafikläge.
  Avbryt, tillbaka eller kryss i webbmenyn lämnar språk och trafik oförändrade.
  Inmatat tågnummer och valt ärende får inte tappas eller skickas av språkbytet.
- Språkval ger inte rätt att välja station, roll, displaygeometri, sträcka eller
  klarteckesregel. Dessa rättigheter ligger fortsatt hos serverns administratör.
- Servern skickar stabila status-/felkoder och strukturerade värden. Språket
  får aldrig påverka kommando-ID, protokollnamn, ruttidentifiering eller regler.

**Implementation för båda hårdvarorna:** ESP8266:s serverrenderade texter
behöver enhetens valda språk; ESP32:s lokala renderare behöver samma
meddelandekatalog och betydelser. Gemensamma meddelande-ID:n med kompakta,
granskade LCD-texter ska återanvändas av virtuell box och skärmkatalog.
Webbgränssnittets allmänna språkval får inte användas som ersättning.

Lagring och protokoll ska validera de fem språkkoderna och endast låta klienten
ändra sin egen språkpreferens. En gammal klient utan språkuppgift fortsätter
fungera med sin dokumenterade standard. Vid nätfel ska sparstatus vara ärlig;
ingen gammal trafikåtgärd får köas eller upprepas för att ett språkval synkas.

För LCD krävs verifierade korttexter och teckenhantering. Diakritiska tecken
återges där hårdvaran stöder dem och translittereras konsekvent annars. Långa
texter får inte tränga undan klockan eller göra två trafikbesked identiska.
US:s etablerade trafiktermer ska behålla sin betydelse i översättningarna.

## 7. Saker vi annars riskerar att missa

| Situation | Föreslagen hantering |
| --- | --- |
| 93 kör flera gånger, flera dagar eller återkommer till samma station | Identifiera körning och stationsbesök, inte bara nummer/stationsnamn. Fråga vid verklig tvetydighet. |
| Klockan flyttas, tåget är försenat eller körningen passerar midnatt | Tid hjälper sorteringen men väljer inte ensam tåg. Använd trafikdag, dygnsoffset och registrerad progression. |
| Servern har ingen registrerad position vid träffstart | Ett första planerat utskick kan vara explicit startdeklaration om inget motsäger det. Märk att läget är operatörsrapporterat, inte detekterat. |
| Tåget är redan på väg in till stationen | Visa inkommande ärende. Erbjud inte ett nytt utskick som om det redan vore inne. |
| Samma nummer finns redan på en annan aktiv sträcka | Kontrollera körningsidentiteten och blockera dubbelt utskick av samma delsträcka. |
| Ingen tågrad eller ofullständig rutt | Ingen automatisk mottagare. Visa fel; rätta/publicera underlaget i Cloud. |
| Extra tåg, omledning eller ändrat nummer | Kräver uttryckligt underlag/beslut, inte dold fri stationsväljare. Börja med Cloud-publicerad extra tågtur. Eventuellt akut driftundantag blir ett separat senare beslut, inte återinfört byggläge. |
| Två vägar till samma station, enkel-/dubbelspår eller vändning | Ange rätt operativa delsträcka/kanal i underlaget. Välj inte första eller kortaste vägen automatiskt. |
| Nästa tidtabellsort är obemannad eller flera driftplatser delar station | Skilj tågets stopp från klareringens ansvarspunkter. Hoppa inte över nödvändig sträckbevakning bara för att ingen box finns där. |
| Två boxar arbetar vid samma station | Ärendet och revisionen ägs av servern. Den ena får inte skriva över den andras nyare beslut. |
| Cloud uppdateras medan ett tåg är ute | Behåll gällande ärendets exakta sträcka. Använd befintlig säker uppdatering/väntan vid konflikt; omtolka inte ett avgånget tåg mot ny rutt. |
| Någon trycker Avgått eller Ankommit av misstag | Ingen tyst vanlig Ångra som frigör fel linje. Separat behörig, loggad korrigering behövs. |
| Faktiskt spår avviker eller tillåter flera fordon | Följ träffens definierade beläggnings-/kapacitetsregel. Utgå inte från att varje spår alltid rymmer exakt ett tåg. |
| Träffklockan är stoppad | Behåll alla ärenden och beläggningar. Klockstopp får varken skapa avgång eller radera väntande trafik. |

Den viktigaste distinktionen är **planerad plats kontra rapporterad plats**. Automatisk ruttidentifiering är bra; påstådd automatisk fysisk lokalisering utan sensorer är det inte.

## 8. Gemensam logik utan att förstöra den gamla boxen

Servern blir enda platsen för ruttidentifiering, sex centrala trafikåtgärder, behörighet, beläggning och spårtransaktion. ESP8266, ESP32, virtuell box och live-TKL ska anropa samma regler genom sina respektive protokolladaptrar.

Behåll separata firmwarebyggen för processorernas nätverks- och hårdvarudrivrutiner. Dela protokolltester, enkla inmatningsregler och beteendekontrakt där det är lämpligt. Kräv inte att ESP8266 håller ESP32:s fulla stationsmodell i minnet; servern kan leverera en liten aktuell vy till den.

Behåll sex handlingar som kärna. Uppslag är en läsning. Faktiskt spår är data i ankomsthandlingen, inte två separata sparningar. Klocka, listor och detaljvisning är presentation.

Uppställt/Förare redo kan fortsatt användas av TKL och rangeringsfunktioner där de har ett verkligt syfte. När boxprofilen inte kräver dessa moment ska servern **inte** skapa falska rapporter om att en förare eller rangerare har kvitterat. Borttaget knappsteg och fabricerad fackmässig status är inte samma sak.

Avveckla i den nya boxprofilen: fritt motstationsval i normalfallet, obligatoriska readiness-steg, Närmar sig och klientens fasta prioriteringslista över konkurrerande trafikhandlingar. Behåll historik och gamla API:er under kontrollerad migration; andra konsumenter kan fortfarande behöva dem.

## 9. Införandeordning och acceptans

1. **Kontrakt och skydd:** skriv regressionstester för fel rutt, avgång utan reservation, ankomst utan avgång och exakt ärendefrigivning. De påvisade luckorna ska stängas på servern.
2. **Ruttuppslag:** återanvänd `service_id` och stoppordning; inför exakt koppling till stationsbesök/delsträcka. Validera driftpaket utan att ändra pågående trafik.
3. **Gemensam trafikmotor:** ta V1:s fungerande övergångar som utgångspunkt. Samordna med rörelselagring och kanaler i stället för att låta två sanningar fortsätta parallellt. Kontrollera även dubbelspår, där modellerna skiljer sig.
4. **Prova i virtuell TMBox:** hela kedjan med klartecken, direkttrafik, neka, återta och ankomstspår. Använd testträff och blandade klienter.
5. **Klientuppdatering:** samma nya arbetsprofil för ESP32 och ESP8266. Servern annonserar stöd; gammal firmware fungerar genom kompatibilitetsadapter tills profilbyte görs.
6. **Flöden och skärmkatalog:** separera ”Nuvarande/legacy” från ”Ny gemensam profil”. Generera bilder och steg ur körbara flöden i samma ändring som implementationen. Målförslaget får inte presenteras som redan befintliga knappar.
7. **Hårdvaruprov och release:** fysisk 16×2/ESP8266 och ESP32, blandade sändare/mottagare, nätavbrott, dubbeltryck, omstart och återanslutning. Serverkompatibilitet verifieras före firmwareutrullning.

### Kvantifierad föreslagen testmatris

- **18 serverfall:** entydig rutt, okänt nummer, fel station, upprepad körning, återbesök, midnatt, obemannad punkt, förgrening, flera kanaler, slutstation, redan avgånget tåg, saknad rapporterad ankomst, första avgång, gammal tilldelning, gammal config, dubbelt kommando, tappad kvittens och avvikande ankomstspår.
- **6 presentationer × 8 operatörsfall = 48 fall:** ESP8266 16×2, ESP32:s fyra format och virtuell box; prova klarteckenskedja, direkttrafik, neka, återta väntande, återta reserverat, planerat ankomstspår, avvikande ankomstspår och avbruten inmatning.
- **3 klientfamiljer × 3 mottagarfamiljer × 3 flöden = 27 integrationsfall:** ESP8266, ESP32 och virtuell; prova komplett resa, neka och återta.
- **4 live-TKL-fall:** box→TKL, TKL→box, samtidiga kommandon och återanslutning mot samma ärende. Den offentliga TKL-demon ska fortsatt vara helt isolerad.

Totalt **97 föreslagna acceptansfall**, delvis överlappande på olika testnivåer. Detta är en plan, inte genomförd testtäckning. Lägg till läsbarhet, långtidstest och resurstoppar på fysisk hårdvara.

Språktillägget F11 kompletterar denna matris med **5 språk × 6 presentationer
= 30 språk-/layoutkombinationer**, vardera med meny, trafikbesked och felvy.
Därtill verifieras beständighet vid omstart/omtilldelning, olika språk på två
boxar vid samma station, fysisk box och webbtestvy i synk, avbryt utan ändring,
bevarad lokal inmatning, oförändrade trafikkommandon, ogiltig språkkod,
gammal klient och nätavbrott. Detta är nya planerade kontroller, inte redan
passerade tester eller en ändring av de 97 trafikacceptansfallen ovan.

## 10. Vad som är verifierat i denna genomgång

- **73 befintliga tester passerade** för gamla motorn, lokal inmatning, V2, katalog och flödesnotiser.
- **4 isolerade diagnostiska prov** gav resultaten i avsnitt 3. Godkända befintliga tester innebär således inte att den föreslagna gemensamma modellen redan är uppfylld.
- Ingen produktion, verklig träff, installerad firmware eller fysisk maskin har ändrats för denna analys.
- De separata pågående ändringarna för offentlig arbetsytesväljare och admin-tilldelad virtuell box är inte samma sak som att trafikmotorerna har förenats.

### Kodkällor

| Fil/funktion | Underlag för slutsatsen |
| --- | --- |
| `engine.py`: `perform`, `_handle_key`, `open_case`, `withdraw_case`, `depart_case`, `arrive_case` | Sex grundhandlingar, gammal knappmodell och tillståndsspärrar |
| `protocol_v2.py`: `_apply`, `_clearance`, `_lookup`, `_release_on_arrival`, `_allowed_actions` | Ny trafikväg, rutt-/tillståndsluckor, uppslag och frigivning |
| `runtime.py`: publiceringsvalidering och snapshot | Befintliga services, service_id, routes och stoppordning |
| `operations.py`: `train_positions`, `record_engine_transition` | Rapporterad position och nuvarande nummernyckel |
| `http_server.py`: `tkl_clearance_action`, `station_service` | TKL går mot gamla motorn; V2 använder separat stationstjänst |
| TMBox `firmware/esp32/lib/tmbox_core/navigation.cpp` | Motstationsväljare, A-uppslag och fast primärprioritering |
| Server `web/tmbox-nav.js`, `web/tmbox-guide.js` | Webbspegel och nuvarande dokumenterade flöden |
| TKL `src/runtime.ts`: `routesForTrain`, `routeNeighbors` | Enkel visningsbaserad grannidentifiering, inte tillräcklig beslutsmotor |

Föregående breda hårdvaru-/funktionsinventering finns i `TMBOX-FUNCTION-AUDIT-2026-09-19.md`. Detta dokument reviderar framför allt interaktionen och ruttansvaret: **samma TMBox-arbete på alla enheter, med servern som ansvarig för nästa sträcka.**
