# Språk på TMBox
Uppdaterat 2026-09-20.

## Användning
- ESP32: tryck D från översikten (eller när boxen väntar på station).
- ESP8266: tryck # från viloläget. A–D behåller den äldre trafikfunktionen.
- Fysisk box: C bläddrar, # sparar, * avbryter. Klockan visas till höger.
- Virtuell TMBox: Språk / Language öppnar en dialog med Spara, Avbryt och kryss.
- ESP8266 via telefon: aktivera webbtest, välj Språk / Language, använd samma virtuella tangenter.
- Admin: Inställningar → TMBoxar → Språk för vald box → Spara och skicka.

Svenska, danska, norska (bokmål), engelska och tyska stöds. Valet sparas för
enhetens ID på Server, inte för stationen, träffen eller administratörens
webbläsare. Två boxar på samma station kan ha olika språk. Administratören
kan ändra valet även när boxen är frånkopplad; den får senaste sparade valet
vid nästa anslutning. Operatören kan sedan byta igen.

## Ansvar och överföring
Server äger trafikbeslut och språktexter. ESP8266 får färdiga trafikrader.
ESP32 och webbklienten ritar fortfarande skärmar lokalt och håller numerisk
inmatning lokalt. Det är alltså inte korrekt att all visningskod redan körs
på servern.

Server skickar bara **det valda språkets** ordlista, plus fem språknamn.
ESP8266 får bara de korta lokala system-/inmatnings-/menytexterna eftersom
trafikraderna redan översätts på servern. ESP32 får det aktuella språkets
skärmkatalog. Boxarna cachar endast aktuellt paket; övriga språk lagras inte.
En liten svensk reservtext finns i firmware för första start/gammal server.

Det krävs en engångsuppdatering av Server och firmware för det nya språkvalet.
Därefter kräver språkbyten och uppdaterade befintliga texter ingen ny
kompilering. Nya skärmtyper eller ändrat protokoll kan fortfarande kräva
firmwareuppdatering. Virtualiserad TMBox uppdateras tillsammans med Server.

## Tekniskt kontrakt
- Språkkoder: sv, da, nb, en, de. ui.version = 1.
- MQTT: tambox/v1/device/{id}/preferences/set respektive
  tmbox/v2/device/{id}/preferences/set, med language och request_id.
- Svar på motsvarande /preferences: status, request_id och ui.
- Adminpush/anslutning skickar ui utan request_id. Meddelanden sparas inte
  som retained i brokern; persistent källa är Server-databasen.
- Virtuell box: GET/POST /v1/tmbox/preferences med boxens egen identitet.
- Admin: POST /v1/devices/language med device_id och language.
- Ett språkpaket innehåller version, language, languages (kod/namn), messages.
  Stationskoder, tågnummer, spår och protokollvärden översätts aldrig.
- Språkbyte ökar inte trafikrevision eller konfigurationsversion, tilldelar
  ingen station och går inte genom trafikkommandokanalen.
- Misslyckad hämtning behåller föregående språk. Fysisk sparning väntar på
  svar och visar fel efter fem sekunder, med möjlighet att försöka igen.
- Administratörens sparbekräftelse betyder att Server har sparat valet,
  inte en fysisk leveranskvittens från en offline-box.

## Verifiering
Automatiska tester täcker fem kataloger, begränsad ESP8266-paketstorlek,
beständighet, enhetsisolering, val utan station, oförändrad trafik,
behörighet för adminpush, offline-köat val, borttagen box, ogiltiga språk,
retained-meddelanden, menyavbrott, omslag av millis och kvittens-timeout.
Fysisk LCD/knappsats och verkligt nätavbrott måste även provas på hårdvara.

