# ESP8266, ESP32 och TKL: samma trafik på samma server

## Ansvar

- TrainMeet Server äger klareringar, tågläge, spår och tillåtna trafikåtgärder.
- ESP8266- och ESP32-boxar (firmware 0.7 eller senare) talar 16×2-profilen
  över MQTT ([protocol/terminal16](protocol/terminal16/README.md)): servern
  ritar varje bild och boxen skickar tangenter. Bilden äger inte
  trafikärendet.
- Webbläsarboxarna och simulatorn skickar kompletta kommandon över HTTP
  (`/v1/tmbox-v2/*`, [protocol/v2](protocol/v2/README.md)).
- Sedan Server 2.0.0 tar servern inte emot `tambox/v1` eller `tmbox/v2` över
  MQTT. En box med firmware äldre än 0.7.0 behöver flashas om.
- TKL skickar samma trafikåtgärder via HTTP, efter behörighets- och passkontroll.
- Cloud behövs inte när träffen körs.

`TMBoxStationService` är stationsservicen för alla tre, trots det historiska
modulnamnet `protocol_v2.py`. `SharedPanelTraffic` kopplar den äldre
A–D-motorn till samma lager.
`TrafficEngine.connections` är då en läsvy av det gemensamma lagret, aldrig
ett parallellt tillstånd att fatta beslut mot. Engine utan runtime-lager
finns kvar för fristående kontrakts-/16×2-tester; normal serverstart kopplar
alltid in stationsservicen innan HTTP/MQTT börjar ta emot kommandon.

## Det som ska fungera likadant

1. Begär klarering: ett beständigt ärende skapas, knappsatsmenyn kan lämnas.
2. Mottagaren svarar från valfri tilldelad klient. Båda stationerna uppdateras.
3. Avgång kan registreras först med beviljad klarering.
4. Ankomst frigör sträckan och uppdaterar samma tågrörelse som TKL visar.
5. Vid direktklarering godkänner servern automatiskt samma slags ärende.
6. Dubbeltryck/återsändning får inte skapa ett andra ärende.
7. Omstart återläser ärendena. Tillfällig inmatning och bekräftelser återställs.

Enkelspår använder en gemensam kanal. Dubbelspår har en kanal per riktning.
16×2 har bara en rad/port för sträckan: en inkommande begäran eller ankomst
prioriteras, därefter utgående ärende. ESP32 kan visa båda samtidigt.

8266 anger tågnummer, inte rörelse-id. Saknas rörelsen i den aktiva dagens
tidtabell avvisas begäran. Finns flera passande besök gissar servern inte:
välj den avsedda rörelsen i TKL:s rörelsebaserade gränssnitt eller v2.
Detta är inte stöd för tidtabellslösa extratåg.

## Uppgradering och provkörning

Den första övergången från gamla, separata trafikmotorer ska ske med
**avslutade äldre klareringsärenden**. Servern vägrar annars byta motor:
inget raderas eller märks fritt. Kör den tidigare versionen, avsluta
ärendena och uppgradera igen. Ta säkerhetskopia först. Efter övergången
kan gemensamma pågående ärenden återläsas normalt vid omstart.

Programtester:

```sh
python -m unittest discover -s tests -v
```

`test_shared_traffic.py` provar blandad riktning, HTTP TKL, återstart,
dubbletter, samtidiga anrop, fel vid lagring, tvetydiga tågnummer och att
publicering sker efter databasens commit. `test_mqtt_transport.py` kör
16×2-transporten mot en riktig, tillfällig lokal Mosquitto-broker och visar
att gamla sparade `tambox/v1`- och `tmbox/v2`-meddelanden rensas. Mosquitto
och Python-paketet paho-mqtt behövs för brokertestet.

Det ersätter inte fysisk verifiering. Före skarp blanddrift behöver vi
prova en NodeMCU med PCF8574/16×2 och en ESP32 med sin riktiga profil:

- Begär från vardera generationen, svara från den andra samt från TKL.
- Prova avslag, återkallning, avgång och ankomst.
- Hantera flera sträckor utan att låsa boxen till ett enda tåg.
- Bryt nätet och strömmen; ingen knapp får skickas i efterhand som ny trafik.
- Starta om servern med ett pågående gemensamt ärende.
- Kontrollera knappmatris, displayadress och säker I²C-spänningsnivå.

## Enkel installation — separat nästa leverans

Målet är förbyggd firmware och en HTTPS-sida där användaren väljer
hårdvaruprofil, ansluter USB, installerar, anger Wi-Fi och väljer Server
och station. ESP Web Tools/Improv och versionsmärkta installationspaket
är ännu inte del av denna serverändring. Befintliga PlatformIO-/Arduino-
anvisningar gäller tills den leveransen är testad. Inget kort flashas
automatiskt och ingen befintlig installation uppdateras av denna text.
