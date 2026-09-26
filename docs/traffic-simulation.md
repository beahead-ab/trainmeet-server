# Trafikspelssimulator på TrainMeet Server

## Användning

Administratören öppnar **hamburgermenyn → Simulering**. Simulatorn använder
serverns valda EU-träff, publicerade config och trafikdag. Ingen ny träff skapas.
Startdialogen bekräftar att simulatorn tar över. Du behöver inte stoppa den interna
träffklockan eller avsluta klareringar först: det vanliga spelet pausas, och dess
tåg och klareringar bevaras separat. Stoppa fysiska tåg på banan innan du bekräftar.
En extern träffklocka måste däremot vara läsbar och stoppad; simulatorn styr den inte.

1. Välj **Starta simulering**, ange starttid, klockhastighet och störningsnivå och
   bekräfta med **Pausa spelet och starta simulering**. Avbryt/kryss ändrar inget.
2. Obemannade stationer klarerar automatiskt. Tilldelade TMBoxar/TKL-terminaler
   arbetar mot samma simulerade trafik och tar över sin station när de ansluter.
   Redan anslutna operatörer behåller stationen direkt, före första automatiska
   trafiksteget. Ingen box behöver kopplas bort, startas om eller tilldelas på nytt.
3. **Pausa/Fortsätt** styr spelklockan. Paus stoppar även manuella trafikändringar.
4. **Återställ vid aktuell tid** skapar ett nytt tidtabellsenligt läge vid tiden
   som gäller när återställningen bekräftas. Klockan och stationstilldelningarna
   behålls; körningen lämnas pausad. Tidigare störningar/beslut återspelas inte.
5. **Avsluta simulering** återgår till vanlig drift med dess sparade trafikläge
   och stoppade klocka. Om en extern klocka har startats under tiden måste den
   stoppas först; simulatorn skickar aldrig kontrollkommandon till den.

Start, återställning och avslut använder gemensamma modaler med kryss,
Avbryt och bekräftelse. Aktuell körning, stationsbemanning och väntande tåg
visas tillsammans. Knapparnas fokus bevaras vid oförändrade bakgrundsuppdateringar.

**Alla tilldelade klienter på servern deltar i samma simulering.** Kör inte
samtidigt fysiska tågrörelser på banan. Simulatorn och vanlig drift är alternativa
körlägen, inte parallella trafikspel. Den fristående TMBox-provbanken och TKL-demo
är fortfarande separata och påverkar inte simulatorn.

## Bemanning

- Enhetens ID identifierar klienten; exempelvis `CDA-TKL` ger inte i sig någon rättighet.
- Admin tilldelar station enligt det befintliga anslutningsflödet.
- Den första levande, tilldelade klienten får kontroll över stationen.
- Flera klienter kan vara anslutna, men bara en primär operatör får ändra trafiken.
- Förlorad kontakt efter 45 verkliga sekunder ger **Kontakt saknas – väntar**.
  Automatiken tar inte över på grund av ett nätavbrott.
- Admin kan bekräfta **Lämna till simulatorn**, eller låta en annan ansluten
  klient ta över. Fråntagna klienters hjärtslag återtar inte kontrollen.
- MQTT-meddelanden sparade av brokern räknas inte som aktuell närvaro.
- Serveromstart återöppnar en pågående simulering pausad; manuella stationer
  väntar på sina operatörer eller ett uttryckligt överlämningsbeslut.

## Trafik och förseningar

Automatiken använder samma serverkommandon som TMBox och TKL: placera tåg,
begär klartecken, ge klart, rapportera avgång och rapportera ankomst. Befintliga
regler för direktklarering, linje- och spårbeläggning gäller fortfarande.

| Händelse | Villkor |
| --- | --- |
| Begär avgång | Tidigast två spelminuter före beräknad färdigställning; tåget måste finnas vid stationen. |
| Ge klart | Automatisk mottagare och ledigt planerat mottagningsspår. |
| Avgång | Klartecken/direktklarering och färdigställningstid har uppnåtts. |
| Ankomst | Faktisk avgång plus tidtabellens restid har passerat; spåret måste vara ledigt. |
| Fortsatt färd | Faktisk ankomst plus planerat uppehåll och eventuellt extra stationsarbete. |
| Nekad begäran | Upprepas inte automatiskt; kräver operatörsåtgärd. |
| Uteblivet svar | Begäran löper ut efter fem spelminuter, inte fem verkliga minuter. |

Tre profiler finns i första versionen:

| Profil | Extra stationsarbete per avgång |
| --- | --- |
| Tidtabellstrogen | Ingen tillagd störning; trafikberoenden kan ändå försena. |
| Normal trafik | 25 % sannolikhet för 1–4 spelminuter extra. |
| Störd trafik | 65 % sannolikhet för 1–12 spelminuter extra. |

Scenarionyckeln ger reproducerbara störningar för samma config/rörelse-ID.
Förseningar fortplantas genom faktiska ankomsttider, uppehåll och upptagna
linjer/spår; nästa delsträcka kan inte börja innan tåget ankommit. Det är en
avsiktligt enkel första modell, inte empiriskt kalibrerad växling eller vagnhantering.

Vid automatisk slutstation frigörs trafikspåret efter simulerad undanställning
(standard fem spelminuter, valbart 1–60). Tåget står därefter i en virtuell bangård;
ingen påhittad linjeavgång skapas. På manuella stationer görs inte denna frigöring
automatiskt. Separat manuellt rangeringsflöde ingår inte i denna version.

## Klocka och återställning

Restider, stationsarbete och begärans livslängd räknas i **spelminuter**.
Nätverksnärvaro mäts separat i verklig tid. Simulatorn har en intern klocka även
om vanlig drift använder FastClock. Klockans löpande sekundtal bevarar dygnsskifte.
Vanliga klockknappar kan pausa/fortsätta/ändra hastighet; godtyckliga tidshopp
under en aktiv simulering nekas. För annan starttid: avsluta och starta nytt.

Start/återställning återskapar tidtabellens tidigare avgångar och ankomster med
samma trafikvalidering. Tåg vars restid fortfarande pågår placeras på linjen.
Motstridiga spår-/linjetillstånd eller ofullständiga tågrutter stoppar skapandet
med felmeddelande. Simulatorn gissar inte bort sådana planeringsproblem.
Startläget valideras innan det vanliga spelet pausas. Ett senare fel vid själva
bytet lämnar vanliga spelet pausat med bevarad trafik, inte en halvstartad simulering.

## Implementation och skydd

- `simulation.py`: trafikplan, automatoperatörer, störningar, bemanning och körval.
- `protocol_v2.py`: gemensam kommandoport, körtids-/bemanningskontroll och
  faktisk spårbeläggning i simulering. Vanlig drift behåller sina regler.
- `operations.py`: stabilt lagerobjekt som alla adaptrar delar. Under samma
  befintliga kommandolås väljer simulatorn en separat SQLite-anslutning. Den
  normala driftanslutningen förblir öppen och dess trafikdata ändras inte.
- `simulations/<run-id>.sqlite3`: separat trafikläge, händelsetider och checkpoint.
  Byte/reset behåller tidigare körfiler; de raderas inte automatiskt.
- `<databasnamn>.simulation-control.sqlite3`: beständigt val av aktiv körning.
- Byte/reset/avslut ökar träffgenerationen och ogiltigförklarar gamla
  vy-/kommandokontexter. Gamla kvittenser får inte utföra trafik i nästa körning.
- Avgångs-/ankomsttider skrivs i samma transaktion som trafikändringen.
  Misslyckade sammansatta TKL-ändringar lämnar inga falska händelsetider kvar.
- Cloud får hämta och lägga nya publiceringar i vänteläge men inte aktivera dem
  under simulering. Träffens trafikdag, klockkälla, återställning från backup och
  fabriksåterställning är spärrade tills simuleringen avslutats.
- De vanliga backupfilerna är inte en export av simulatorns separat lagrade körningar.
  Bevara även kontrollfilen och `simulations/` vid flytt av hela serverns tillstånd.

Admin-API: `GET /v1/simulation`, `POST /v1/simulation`. Alla ändringar kräver
aktuell `meet_generation`; efter start också `run_id`. Återställning, avslut
och överlämning kräver `confirmed: true`. Även start kräver uttryckligen
`confirmed: true`; äldre klienter kan inte oavsiktligt pausa spelet.

## Omfattning och verifiering

Första versionen gäller **EU-stationsdrift med kompletta publicerade tåglopp**.
US-dispatcher/Conductor, detaljerad vagnväxling, slumpade linjefel och optimerad
omledning mellan spår ingår inte. Ingen särskild simulatorlogik läggs i firmware.
Serverstyrda 16×2-boxar får ordinarie trafikvyer; webbklienter visar dessutom
simuleringsmärkning. En permanent simuleringssymbol på fysisk LCD är inte införd.

Automatiska tester täcker bland annat normaldriftens isolering, automatisk och
manuell klarering, direktklarering, förseningskedja över tre stationer, paus,
dygnsskifte, återställning mitt under resa, gammalt kommando efter byte,
brokerns sparade närvaro, förlorad operatörskontakt, överlämning, omstart,
Cloud-spärrar, extern klocka och HTTP-behörighet. Fysisk ESP8266/ESP32 och en
fullskalig träff behöver fortfarande provköras före skarp användning.
