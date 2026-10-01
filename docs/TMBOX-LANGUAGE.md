# Språk på TMBox
Uppdaterat 2026-09-20.

> **Från TrainMeet Server 1.17.5:** boxar med firmware 0.7.x (profilen
> server-16x2) har ingen egen språkmeny. Administratören väljer varje box
> språk under Drift → Klienter → **Språk**; startbilden visar `Nr# A:Kö`.
> Resten av dokumentet beskriver de äldre protokollen.

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
- 16×2-box (MQTT): språket följer med i varje bild (`frame.language`). När
  språket ändras skickar servern boxen en ny bild direkt. Persistent källa är
  Server-databasen. (`preferences/set` på `tambox/v1` och `tmbox/v2` är
  borttaget sedan Server 2.0.0.)
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

