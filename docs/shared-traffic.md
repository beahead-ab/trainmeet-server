# ESP8266, ESP32 och TKL: samma trafik på samma server

## Ansvar

- TrainMeet Server äger klareringar, tågläge, spår och tillåtna trafikåtgärder.
- ESP8266 visar 16×2-displayen och skickar tangenter. Servern håller dess
  A–D-meny och tillfälliga inmatning, men menyn äger inte trafikärendet.
- ESP32 visar sin rikare vy och skickar kompletta kommandon. Cache,
  knappetiketter, ljud och ljus är klientfunktioner, inte egna trafikbeslut.
- TKL skickar samma trafikåtgärder via HTTP, efter behörighets- och passkontroll.
- Cloud behövs inte när träffen körs.

`TMBoxStationService` är stationsservicen för alla tre, trots det historiska
modulnamnet `protocol_v2.py`. `SharedPanelTraffic` är v1:s serveradapter.
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
publicering sker efter databasens commit. `test_mixed_mqtt.py` skickar
v1/8266-tangenter över MQTT 3.1.1 och v2/ESP32-kommandon via en riktig,
tillfällig lokal Mosquitto-broker. Mosquitto och Python-paketet paho-mqtt
behövs för brokertestet.

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
