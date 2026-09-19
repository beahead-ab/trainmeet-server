# TMBox: Cloud-mappning och reviderad displayprincip

Datum: 2026-09-19. Lokala ändringar; inte publicerat eller driftsatt.

## Beslut om gränssnittet

Destinationer ska inte heta A, B, C eller D för operatören. Bokstäverna är
funktionsknappar. Operatören anger tågnumret och servern härleder nästa station
från tågets aktuella rörelse vid den tilldelade stationen. Bara administratören
kan tilldela en fysisk eller virtuell box dess station.

- Rad 1: tågnummer och grannstationens kod. Vänster/höger följer Clouds placering.
- Väntande begäran visas med frågetecken. Godkänt klartecken ersätter det med en öppen pil (`<`/`>`). Först efter bekräftad avgång används fylld pil (`◀`/`▶`).
- Pilriktningen visar till/från stationen; placeringen visar vilken sida grannen ligger på.
- Rad 2: relevanta funktioner till vänster; träffklockan till höger.
- A–D får aldrig samtidigt betyda destination och funktion.
- Färdigskrivet tågnummer bekräftas med `#`; siffrorna stannar lokalt tills dess.
- Cloud bestämmer placeringen. Ingen lokal överstyrning läggs till.

Exempel med illustrativa koder, inte nya inställningar i den riktiga träffen:

```text
MUN?17     39?VA
A:VAL      12:34
```

För ett valt tåg som har fått klartecken mot höger visas exempelvis `39>VA`.
Mot vänster skrivs destinationen först, exempelvis `MUN<17`. Inkommande tågs
pilar vänds: `MUN>17` respektive `39<VA`. Efter faktisk avgång används
`MUN◀17`/`39▶VA` för utgående respektive `MUN▶17`/`39◀VA` för inkommande.
Två ärenden visas på samma första rad, vänster- och högerjusterade med minst
ett blanksteg mellan. Om hela identiteterna inte ryms krävs sidindelning, inte
kapade stationskoder eller tågnummer. Fyllda pilar behöver egna LCD-tecken.

**Klartecken är inte avgång.** Funktionen för att bekräfta TÅG UT måste finnas
kvar. Efter avgång får begäran inte kunna avbrytas som om tåget stod kvar.
Mottagaren bekräftar TÅG IN och kan då korrigera ankomstspåret. Rad 2 måste
tydligt skilja väntar, klart att skicka och avgått, även om rad 1 använder pilar.

På 16×2 får inte alla fyra funktionsförklaringar plats samtidigt med klockan.
Visa därför de funktioner som gäller för det valda ärendet och en tydlig väg
till nästa ärende. Vid flera tåg/grannar på samma sida behövs bläddring och en
synlig kömarkering; ingen status får försvinna genom automatisk växling medan
operatören skriver. Långa tågnummer/stationskoder får inte kapas så att två tåg
eller stationer ser likadana ut.

För fyra rader är förslaget samma trafikmodell: fler synliga ärenden, en extra
rad med valt tågs status/spår och nederst funktioner plus klocka. Det är mer
visningsyta, inte en annan trafiklogik.

## Vad som hittades i befintlig lösning

1. Den riktiga serverns inställningssida visade att automatisk configuppdatering
   var pausad. Det har inte fastställts om inställningen saknas i den äldre
   databasen eller uttryckligen är satt till paus.
2. Länkade äldre EU-installationer utan den nya inställningen behandlades som
   pausade. En verklig, uttrycklig paus måste däremot respekteras.
3. Cloud ritar A/B på vänster sida och C/D på höger. Legacy/Lovable-renderingen
   använder A/C på vänster sida och B/D på höger. Utan uttrycklig layoutinformation
   kan samma sträckmappning därför ritas på fel sida.
4. Serverns ESP32-protokoll skickade en alfabetiskt sorterad lista av sträckor
   men inte Clouds portplacering. Radnumren räknades dessutom före filtrering
   till den aktuella stationen.
5. Den virtuella boxen cachade konfiguration per stations-id. En ny publicering
   för samma station hämtades inte in i den öppna klienten.
6. Att spara i Cloud ändrar ett utkast. Servern hämtar publiceringar, inte utkast.

## Synkfixen som är implementerad lokalt

Cloud publicerar `panels[].slot_layout = "columns"` tillsammans med oförändrade
`slots`. Servern validerar och bevarar detta. Äldre paket utan fältet behåller
`rows` (A/B överst, C/D underst); gamla paket skrivs inte om i efterhand.

ESP8266 får serverrenderade rader och snapshots med uttrycklig `side`/`row` per
port. ESP32 får hela panelmappningen, tomma portar inkluderade, samt
`connections[].panel_slots` och `display_side`. Sträcklistan följer portordningen
och har sammanhängande radnummer. Om flera paneler på samma station motsäger
varandras sida anges ingen påhittad sida. ESP32 och webbklienten använder
stationskoden på den angivna sidan i den befintliga sträckväljaren.

Den virtuella klienten hämtar om config när station, träffgeneration, publicering
eller configversion ändras. Gamla val rensas. Misslyckad hämtning eller blandade
versioner lämnar klienten i laddningsläge, inte med gamla aktiva val.

Äldre länkade servrar utan explicit inställning får automatisk hämtning som
standard. En uttrycklig paus bevaras. Inställningar → Cloud har nu ett gemensamt
modalfönster för automatiska uppdateringar: Spara, Avbryt och kryss. Öppna ändringar
skrivs inte över av bakgrundsuppdatering; sparfel bevarar formuläret.

Aktivering använder befintliga trafikspärrar. Ny config väntar vid pågående
trafik, och när den säkert aktiveras skickas den omedelbart till båda boxprotokollen.
Identitet och stationstilldelning finns kvar. Ingen periodisk omtilldelning införs.

## Kvar för det nya funktionsknappsflödet

Synkfixen är en förutsättning, **inte hela omskrivningen av operatörsflödet**.
Legacy-motorn använder fortfarande A–D för riktningsval, och ESP32 har fortfarande
sin befintliga sträckväljare. De får inte bara döpas om utan ändrad serverlogik.

Nästa separata implementation behöver:

1. Gemensam serverfunktion för tågnummer + station + trafikdag → aktuell rörelse
   och nästa station, med tydligt fel för saknat/otydligt underlag.
2. Samma funktionssteg för ESP8266, ESP32 och virtuell box: skicka, invänta svar,
   godkänna/neka, bekräfta avgång, bekräfta ankomst/byta spår samt avbryta före avgång.
3. Ingen destination som väljs lokalt och inget läge för ”närmar sig”.
4. Display enligt principen ovan, inklusive klockan även under inmatning och frågor.
5. Flöden, skärmkatalog och gemensamma bild-/beteendetester uppdateras samtidigt.
6. Fysisk provning på Bennys ESP8266 och ESP32 efter att en testrelease byggts.

## Leveransordning för synkfixen

1. Testa och leverera Server samt ESP32-klienten som förstår den uttryckliga placeringen.
2. Leverera Cloud och publicera träffens granskade portmappning på nytt.
3. Kontrollera att automatisk synk är aktiv, eller välj Sök configuppdatering.
4. Servern väntar vid konflikt och aktiverar sedan mappningen lokalt; ingen box
   behöver en ny stationstilldelning för samma träff.
5. Kontrollera rätt stationskod och sida på fysisk box och virtuell klient.

Ingen publicering, trafikåtgärd eller inställningsändring har utförts på den riktiga
träffservern under felsökningen.

## Verifiering av de lokala ändringarna

- Server: 697 tester godkända, inklusive säker aktivering och omedelbar MQTT-
  leverans av ändrad mappning till båda protokollen.
- Cloud: 445 tester godkända; utkast väcker inte servern men publicering gör det.
- Webbklient: tre nya tester för samma stations nya config, fel/race under
  hämtning och stationskodens placering för 16×2, 20×2, 16×4 och 20×4.
- Administrationsgränssnitt: isolerad webbläsarkontroll godkänd, inklusive den
  nya synkdialogen, sparfel och modallayout på smal och bred skärm.
- Offentliga klienter: separat webbläsarkontroll mot tillfällig lokal server
  godkänd för registrering, admintilldelning, återkallelse och lokal inmatning.
- ESP32-kärnan: 511 kontroller godkända samt oförändrade befintliga referensbilder,
  navigeringsförlopp och signaltester.
- Firmware kompilerad för `esp32-s3` och `esp32-benny`.
- Inte testat på fysisk hårdvara. ESP8266-firmware ändrades inte i synkfixen;
  dess display skickas från servern.

Den föreslagna nya funktionsknappsmodellen är inte införd och ska inte räknas
som verifierad genom ovanstående tester.
