# TMBox: gemensam serverstyrd 16×2-profil

Gäller från TrainMeet Server 1.10.0 tillsammans med TMBox firmware 0.7.0.
Uppdatera servern först. ESP8266 och ESP32 använder samma profil och samma
versionsnummer. En större fysisk display visar tills vidare 16×2-profilen.

## Tre sätt att köra

- `/tmbox/`: riktig webbklient till den server som sidan öppnas från. Den får
  ett eget ID och väntar på administratörens stationstilldelning.
- Fysisk ESP8266/ESP32: upptäcker lokal server och visar serverns bild via
  `tmbox/terminal/device/<id>/...`. Ingen lokal trafik- eller språklogik.
- `/tmbox-lab/`: isolerad provbänk med demodata per webbläsarsession. Samma
  skärm- och tangentlogik, men ingen åtkomst till träffens trafik eller databas.

På en internetexponerad server är publik klientregistrering avstängd som
standard. `TRAINMEET_PUBLIC_CLIENT_ORIGIN=https://server.trainmeet.app`
aktiverar den enbart för exakt angiven HTTPS-origin bakom betrodd lokal proxy.
Administrationen kräver fortfarande inloggning. Exponera aldrig den lokala
MQTT-brokern oskyddad mot internet; använd webbklienten där.

## Minsta möjliga antal knapptryckningar

1. Skriv tågnummer direkt; siffrorna stannar lokalt tills `#`. `B` suddar,
   `*` avbryter lokal inmatning. Alternativt bläddra avgångar med `C`/`D`.
2. `#` utför den primära åtgärd som visas, exempelvis begär eller avgå.
   Servern väljer nästa station ur tidtabellen. A–D är inte destinationer.
3. Inkommande förfrågan öppnas automatiskt när terminalen är ledig. `#`
   godkänner, `*` nekar; kön visar antal och `A` återvänder till kön.
4. Klart är inte avgång. Avsändaren bekräftar verklig avgång separat med `#`.
   Återtag är möjligt före avgång, aldrig när tåget lämnat stationen.
5. Mottagaren bekräftar ankomst med `#`; avvikande spår väljs vid mottagandet.
   Ankomst, valt spår och frigivning av sträckan sparas i samma transaktion.
6. Avsändaren ser en kort mottagningsbekräftelse som försvinner automatiskt.
   Avslutat tåg lämnar översikten. Inga extra kvitteringar behövs.

Förfrågningar avbryter inte pågående sifferinmatning. Klockan ligger till
höger på rad två. En ledig översta rad är tom. På översikten öppnar `*`
språkval, `C`/`D` väljer och `#` sparar. Admin kan också ändra boxens språk.
Endast aktuella texter och nödvändiga LCD-specialtecken skickas till enheten.

## Drift och kompatibilitet

SQLite-stationstjänsten är ensam ägare till trafikärenden för äldre boxar,
nya terminaler och TKL. Äldre protokoll behålls för befintlig firmware, men
deras lokala navigeringsspecifikationer gäller inte den nya 16×2-profilen.
Avsluta aktiva äldre klareringar innan första uppgraderingen till gemensam
trafiklogik. Servern vägrar annars övergången; den raderar inte trafiken.

Kommandon är bundna till enhet, station, träff, skärmversion och unikt ID.
Återanslutning hämtar färskt läge och återspelar inte trafikkommandon.
MQTT skickar nya bilder vid förändring; liveness-kvitton är inte upprepade
stationstilldelningar. Webben hämtar bildstatus utan att skicka varje siffra.

903 Python-tester och 59 JavaScript-/webbläsartester passerade inför release.
Firmware måste även kontrolleras på fysisk hårdvara; mjukvarutester ersätter
inte prov av display, kabeldragning, specialtecken och knappsats.
