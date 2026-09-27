# Till TMBox/firmware: Server-kontrakt och verifiering

Datum: 2026-09-27. Underlag: lokalt granskad TMBox-källkod, commit `10852ae`,
`VERSION` 0.7.0, särskilt `firmware/common/server_terminal.h` och båda ESP-sketcherna.
Detta säger inte vilken version Bennys faktiska enhet har installerad.

## Slutsats: inget nytt firmware-API behövs för denna ändring

Den granskade gemensamma adaptern för ESP8266 och ESP32 använder redan
`profile: "server-16x2"`. Server räknar ut sida, text, tillgängliga handlingar,
språk och LCD-glyfer. Firmware renderar serverns bild och skickar generiska
tangentkommandon. Lokal vänster/höger-inställning på Server ändrar bildraderna;
firmware ska inte införa egen placerings-, destinations- eller trafiklogik.

För boxar som redan använder denna profil räcker därför Server-uppdateringen.
En äldre firmware med fast A–D-destinationslogik måste uppgraderas till den
serverstyrda profilen; config kan inte lägga till protokollkod som saknas.
Ingen fysisk firmware har ändrats eller flashats i Server-arbetet.

## Behåll dessa kontrakt

- Samma basprofil på ESP8266/ESP32: 16 tecken × 2 rader. Större fysisk display
  är inte ett krav på en ny lokal trafikmotor; fler profilkapabiliteter avtalas
  separat. Serverns klocka ska alltid synas enligt bildraderna.
- Befintliga MQTT-ämnen under `tmbox/terminal/device/<id>/…` och befintlig
  hello/boot-, frame-, ack- och alive-hantering är oförändrade. Ändra inte
  serverupptäckt/Wi-Fi-flöde för att stödja denna presentation.
- Rendera `lcd.cells` (2×16) och `lcd.glyphs` från Server. Högst åtta samtidiga
  CGRAM-glyfer. En klient ska inte försöka tolka `ÅÄÖ` som en ASCII-byte var.
  Servern levererar redan cellkoder och pixelmönster.
- A–D är generiska funktioner enligt aktuell `keys`, aldrig stationsadresser.
  `#` är primär bekräftelse. Siffror buffras lokalt; bara avslutad inmatning
  skickas. `*` tömmer lokal inmatning, B suddar, A kan lämna den för kön enligt
  serverns `entry`-instruktioner. Utan inmatning gäller serverns tangentbetydelse.
- En ny bild kan ha samma trafik-`revision` men högre `view_revision`.
  Server höjer den senare när placeringen ändras och ger ett nytt `view_token`.
  Samma `entry.context` betyder att oskickade siffror ska behållas.
- Vid ny station/träff/återställd provbänk ändras inmatningskontexten; gammal
  inmatning och gamla kommandon ska inte kunna användas i det nya läget.
- Återanslutning hämtar färskt läge utan att återspela trafikkommandon.
  Närvarokvitton är inte återkommande stationstilldelningar. Serverns admin
  bestämmer station/roller; enhetens operatör väljer däremot eget språk.

Detta stöds redan i den granskade adaptern. Kontrollera faktisk installerad
firmware före slutsatsen att en fysisk box inte behöver flashas.

## Acceptansprov på fysisk hårdvara (återstår)

### Ny Server-navigering: aktiva tåg

Server tilldelar B ”Aktiva tåg (antal)” på översikten. C/D växlar direkt mellan
aktiva rörelser, med räknare 1/2 på displayen. # gäller den visade rörelsen.
Efter avgång ligger samma tåg kvar valt men utan avgångsknapp. A förblir kön för
obesvarade förfrågningar. Lokalt B-sudd under sifferinmatning är oförändrat.
Valfritt frame-fält `active: {count, position, movement_id}` tillkommer för
diagnostik, men behöver inte tolkas för navigering. Befintliga rader/keys räcker.

Prova särskilt två klarerade utgående tåg, även på samma sida: B → C/D → #.
Ett dubbelt # får inte skicka nästa tåg. Prova också återtag från en annan box,
gamla token och nätavbrott. Detta ska fungera lika på ESP8266 och ESP32 med den
serverstyrda profilen; ingen firmwareändring har gjorts i denna chat.

### Gemensamma hårdvaruprov

Kör samma prov på ESP8266 och ESP32:

1. Anslut automatiskt, tilldela station på Server, kontrollera serverstyrd 16×2.
2. Skriv två siffror utan `#`. Ändra stationens vänster/höger som admin.
   Siffrorna ska finnas kvar; inget tågkommando ska ha skickats.
3. Bekräfta tågvalet. Ändra sida under en aktiv begäran. Stationskod, nummer,
   `?`/pil och justering ska ändras korrekt utan tappad begäran eller destination.
4. Ge klart → faktisk avgång → ankomst, även med avvikande ankomstspår.
   Kontrollera kort mottagningsbesked hos avsändaren och att avslutat tåg försvinner.
5. Två väntande förfrågningar: synlig kö, A hittar tillbaka, C/D bläddrar i kön.
6. Svenska ÅÄÖ/åäö, danska/norska ÆØ/æø och tyska Ü/ü/ß samt båda pilarna.
   Byt språk på enheten och från admin. Gamla CGRAM-tecken får inte bli kvar.
7. Bryt nätet under inmatning och vid bekräftelse. Återanslut och kontrollera
   färskt läge, spärrade gamla bilder och ingen dubblerad trafikåtgärd.

Webbprov och Python-tester verifierar kontraktet men ersätter inte LCD-,
knappsats-, kabeldragnings- eller minnesprov på riktiga boxar.

## Simulatorn implementeras i Server, inte i firmware eller Cloud

- Riktig virtuell box: `/tmbox/`, admin tilldelar station. Ingen operatörsstyrd
  placeringseditor visas där.
- Isolerad provbänk: `/tmbox-lab/`, knapp **Testa vänster/höger** med dialog,
  kryss, Spara och Avbryt. Samma serverägda sidberäkning som den riktiga klienten.
- Testplacering är privat för provbänkssessionen och ändrar inte träffens data.
  ”Nollställ alla enheter” behåller placeringen. ”Nytt test” återgår till standard.
- Presentation uppdateras utan nya destinationer, trafikkommandon eller förlorad
  lokal sifferinmatning. Hårdvaruliknande rosa skal och blå/vit display behålls.

Serverändringarna är lokala tills de pushats och driftsatts separat.
