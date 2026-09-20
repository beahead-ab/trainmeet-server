# Serverstyrd TMBox 16×2 – isolerad provbänk

Datum: 2026-09-20. Isolerad pilot för testservern, inte produktionsintegrerad eller publicerad firmware.

## Vad vi provar

Samma server bestämmer trafikregler, tågets mottagare, två färdiga displayrader
och knapparnas betydelse. Webbläsaren har ingen egen trafiklogik. Den visar
serverns bild och buffrar siffror lokalt tills operatören trycker `#`.
`A–D` är funktionsknappar, aldrig destinationsadresser.

Provbänken använder befintlig `TrafficEngine.perform` och den befintliga
ruttupplösningen. Den skapar inte en tredje trafikmotor och använder inte de
ofullständiga V2-övergångarna. Presentationen är en ny, serverägd adapter.

## Testdata och handgrepp

Tre virtuella boxar är förhandstilldelade teststationer: Munkeröd, Charlottendal
och Vagnsta. Detta är exempeldata, inte den importerade träffens tidtabell.

Under varje testbox visas en skrivskyddad tidtabell med tydliga tågnummer,
planerad ankomst/avgång och från-/tillstation. Den byggs av servern från samma
testdata som terminalen och sorteras efter stationens tider. Hela tidtabellen
ligger kvar även när tåg har körts; listan är en referens, inte trafikknappar.

1. Charlottendal: `39 #` väljer tåget, sedan ett separat `#` för begäran till Vagnsta.
2. Vagnsta: `39 #` väljer tåget, sedan `#` för klartecken. `*` öppnar ”Neka?” och `#` bekräftar.
3. Charlottendal: `#` rapporterar faktisk avgång.
4. Vagnsta: `#` tar emot på planerat spår 1. `B`, `D`, `#` tar emot på spår 2.

Tåg 17 går västerut från Charlottendal till Munkeröd. Tåg 93 går från
Munkeröd till Charlottendal. Siffror börjar direkt skriva ett nytt tågnummer,
även från en detaljvy. `#` söker hela numret, utan att samtidigt ändra trafiken.
Utan inmatade siffror bekräftar `#` den åtgärd som visas på skärmen.

`C`/`D` bläddrar bakåt/framåt i stationens kommande ankomster och avgångar.
Listan sorteras efter stationens planerade tid, med hänsyn till dygnsoffset.
Rad 1 visar tågnummer, **ANK/AVG** och planerad tid; rad 2 visar handgrepp och
aktuell klocka. I bläddringsvyn växlar `A` mellan alla tåg, ankomster och
avgångar. `#` väljer tåget; en ytterligare bekräftelse krävs för trafikåtgärden.
Planerade ankomster syns även före begäran men kan inte tas emot före avgång.
Försenade, ännu inte rapporterade tåg ligger kvar. Avgångar försvinner från
avsändarens lista först vid faktisk avgång; ankomster först när de tas emot.
Ett genomgående tåg kan inte skickas vidare innan föregående ankomst registrerats.

`#` är primär bekräftelse och `*` är primär avbryt/nej/tillbaka-knapp.
Betydelsen bestäms av servern och visas på displayen och i knappförklaringen:

| Läge | `*` gör | Bekräftelse |
| --- | --- | --- |
| Sifferinmatning | Tömmer numret lokalt, återgår till aktuell serverbild | Ingen trafikändring |
| Bläddring eller vanlig detaljvy | Tillbaka till översikten | Ingen trafikändring |
| Egen begäran eller eget klartecken före avgång | Öppnar ”Återta?” | `#` återtar, `*` behåller |
| Inkommande begäran | Öppnar ”Neka?” | `#` nekar, `*` behåller |
| Val av annat ankomstspår | Tillbaka utan att registrera ankomst | Ingen trafikändring |
| Tåget har avgått | Tillbaka, aldrig återtagning | Mottagaren behöver ta emot tåget |

När `*` öppnar återtagning/nekande visar `A` översikten utan att ändra trafiken.
`B` nekar inte längre direkt. Om en annan box ändrar tillståndet medan en
bekräftelsefråga visas spärras den gamla bekräftelsen. En fråga som inte längre
är giltig visar ”LÄGET ÄNDRAT”. Inga trafikregler har flyttats till webbläsaren.

Översiktens första rad är helt tom när ingen trafik pågår. Lediga anslutningar
visas inte med stationskod eller streck och tar ingen plats, oavsett antal.
Endast aktuella begäranden och tågrörelser visas till vänster/höger med
motstationens kod. `?` betyder begärt, `<`/`>` klart, `◀`/`▶` faktiskt avgånget.
När trafiken återtas, nekas eller tas emot försvinner den från översikten.
För långa identiteter visas en i taget med `B` i översikten, aldrig ett avklippt tågnummer.
Detaljvyn fokuserar på det valda tåget. På andra raden finns aktuellt handgrepp
till vänster och serverns klocka till höger, även under sifferinmatning.

Knapparna ”Nytt test” återställer bara provbänken och kan välja trafik med
eller utan mottagarens klartecken. Direkttrafik har fortfarande reservation
och en separat rapportering av faktisk avgång.

**Nollställ alla enheter** ovanför boxarna tömmer alla begäranden, klartecken,
pågående/avslutade tågrörelser, spårbeläggning och testlogg. Samtliga boxar
återgår till översikten och lokal sifferinmatning rensas, även i andra öppna
flikar via serveruppdateringen. Stationstilldelningar, tidtabell, testläge och
den löpande klockan behålls. Gamla kommandon kan inte återskapa rörelser efter
nollställningen. Detta påverkar enbart provbänken, aldrig den riktiga träffen.

## Starta lokalt

Från serverprojektets rot, med dess Python-miljö:

```sh
PYTHONPATH=src python -m tmbox_gateway.terminal16_demo --port 0
```

Öppna exakt den `http://127.0.0.1:PORT/`-adress som skrivs ut. `--port 0`
väljer en ledig port. Endast loopback används; det är inte en mobilserver
på Wi-Fi. Webbsidan anpassar sig dock till telefonbredd.

## Verifiering

- 63 Python-tester: grundflöden, direkttrafik, neka/återta, ankomstspår,
  dubbla kommandon, gamla vyer, ruttkontroll, 16 tecken och HTTP-isolering;
  dessutom kronologisk bläddring, filter, dygnsskifte, försenade tåg,
  genomgående tåg, separata val/bekräftelser och inaktuell tågmarkering;
  dessutom `*` i alla lägen, avbrutna bekräftelser, samtidiga trafikändringar
  och nollställning i alla trafiklägen med bevarad konfiguration/klocka.
- 8 ytterligare Python-tester: originalets färgpalett, specialtecken, normalisering,
  teckenbudget och rundtur från Unicode till LCD-byte och tillbaka.
- 10 JavaScript-tester: lokal sifferbuffert, `#`, `*`, radering,
  siffergräns, bevarad inmatning vid klockuppdatering, specialtecken och återställning.
- 18 befintliga trafikmotortester används som regressionstest.
- 16 tester för publicerad provbänk: sessionsisolering, egen nollställning,
  utgång, resursgränser, sessionscookie, HTTPS-origin och begränsade HTTP-rutter.
- Webbläsarprov: hela klarteckenskedjan och mottagning på avvikande spår;
  endast fyra trafikåtgärder registreras. Telefonlayout kontrollerad vid 390 px.
  Även öppna/avbryt återtagning med `*` och neka med `*` följt av `#` är
  provade; enbart öppnade frågor registrerar ingen trafikåtgärd och bekräftat
  nekande visas hos både mottagare och avsändare.

Uppdateringar skickas när bilden ändras. En transport-heartbeat betyder inte
att boxen hämtar ett nytt uppdrag. Siffertryck skickas inte till servern.

## Avgränsning och nästa steg

Detta testar operatörsflöde och presentation, inte hela framtida leveransen.
Ingen trafikroute, MQTT-kanal, Cloud-config eller fysisk box ändras.
En separat webbroute `/tmbox-lab/` används på testservern.
Inga uppgifter överlever omstart av provbänken. Trafikmotorn kör i minnet;
ankomst och faktiskt spår är skyddade av samma lås i testet, inte av en
beständig databastransaktion.

Efter godkänd layout behöver vi:

1. Integrera i serverns riktiga enhetsregistrering och adminstyrda tilldelning.
2. Knyta ankomst, faktiskt spår och händelselogg till beständig gemensam transaktion.
3. Göra tunna ESP8266- och ESP32-adaptrar som konsumerar samma serverkontrakt;
   mappa pilar och nationella tecken till verkliga LCD-tecken och prova hårdvara.
4. Koppla språkbyte och adminstyrt språk till serverns ordinarie språkhantering.
5. Verifiera återanslutning, flerstationsrutter, flera samtidiga ärenden åt
   samma håll och alla verkliga stations-/tågnummer innan produktionsaktivering.

Utökningar till 20 tecken, fyra rader och ESP32-specifika funktioner väntar.

## Uppdatering: originalutseende och språkens tecken

Provbänken återanvänder originalklientens skära hölje (#b76f80), blå LCD
(#0c4fe5), ljusa tecken (#b9d8ff) och mörka knappsats.
Svenska rader använder TÅG, SPÅR, ÅTER, Sök och Avgått, inte ASCII-ersättningar.
Alla fem språk kan granskas i den skrivskyddade sektionen **Teckentest**.
Det är ett teckentest, inte ett språkbyte för hela pilotens trafikflöden.

`terminal16_glyphs.py` normaliserar Unicode till NFC och räknar teckenceller,
inte UTF-8-byte. Serverns `lcd`-fält innehåller 2 × 16 cellbyte samt endast
de 5 × 8-pixelmönster som aktuell bild behöver. Inmatningsmallen har ett eget
sådant fält. Bankens slot 0 är giltig: en framtida firmwareadapter måste
använda längdstyrd byteutmatning, inte nollterminerad `print`.

Svenska ÅÄÖ/åäö, danska/norska ÆØÅ/æøå, tyska ÄÖÜẞ/äöüß och fyllda pilar
har serverägda mönster. Engelska grundtexter använder den vanliga teckentabellen.
HD44780 har åtta egna 5 × 8-tecken samtidigt enligt
[tillverkarens datablad](https://cdn-shop.adafruit.com/datasheets/HD44780.pdf),
sidan 2 och 13. Banken delas av hela displayen, inte en bank per rad.
Fler än åtta unika egna tecken i samma bild eller ett ostött tecken ger ett
uttryckligt fel i piloten. Ingenting ersätts tyst med frågetecken eller kapas.
Produktionsrenderaren måste vid behov välja en annan sidindelning.

Den granskade äldre ESP8266-koden ersätter svenska tecken med ASCII i `lcdLine`;
ESP32:s `transliterate` gör motsvarande. Dessa firmwarevägar är **inte ändrade**
av piloten. De nya pixelmönstren är verifierade strukturellt och genom byte-
rundtur, inte visuellt på riktig LCD. Firmwareanslutning och hårdvarutest återstår.
Vid bankbyte behöver klienten dölja displayen, ladda banken, skriva hela bilden
och visa den igen för att inte tillfälligt visa fel bokstav i en gammal cell.

## Åtkomst på testservern – inte Cloud

Provbänken publiceras på **https://server.trainmeet.app/tmbox-lab/**.
Inget konto krävs. Varje webbläsarprofil får en egen tillfällig testsession;
flikar i samma profil delar test, medan en annan profil eller ett privat fönster
får ett separat test. Nollställning påverkar bara den egna sessionen.

Sessionen finns högst fyra timmar, eller 30 minuter utan aktivitet, och
försvinner vid omstart av provtjänsten. Ladda om sidan för ett nytt test.
Den privata sessionscookien skickas enbart till provbänkens sökväg.

Provbänken kör separat från serverns riktiga träff med egna minnes-, CPU-,
sessions- och anslutningsgränser. Cloud används inte och ändras inte.
Den riktiga webb-TMBoxen behöver fortfarande administratörens stationstilldelning.
Det här är en provmiljö för gemensamma flöden, inte en firmwareuppdatering.
Installation och verifiering beskrivs i `deploy/terminal16/README.md`.
