# Bekräftad lokal tågnummerinmatning (MQTT v1)

ESP8266-klienten får hålla redigering av tågnumret lokalt. Servern äger fortsatt
stationstilldelning, ägande av panelinteraktionen och alla trafikbeslut.

## Kompatibel utökning

Panelens snapshot annonserar `interaction.local_train_entry: true` tillsammans
med befintliga `mode`, `selected_slot`, `owner_client_id` och `train_number`.
En ny klient använder lokal inmatning endast i `enter_train`, för sin egen
ägda interaktion. A–D väljer sträcka enligt befintligt tangentflöde.

Siffror ändrar endast boxens inmatningsbuffert och LCD/webbdisplay. Vid `#`
skickas ett enda befintligt `key_press`-kommando med tilläggsfältet:

```json
{"action":"key_press","key":"#","train_number":"00421"}
```

Ovan är endast nyttolastens relevanta fält, inte ett komplett kommando.
Protokollversion, klient-ID, panel, träffsession, revision och kommando-ID
är fortfarande obligatoriska. Servern accepterar 1–5 ASCII-siffror, inklusive
inledande nollor. Den kontrollerar hela kommandot under samma lås som den
befintliga trafikövergången; ingen tangentsekvens spelas upp. Det blir en
revision och en kvittens, inte en revision per siffra. Dubbletter är fortsatt
idempotenta och stations-/ägandekontrollen är oförändrad.

`*` avbryter den påbörjade panelinteraktionen utan att skicka buffertens nummer.
`#` öppnar en begäran eller reservation enligt sträckans trafikläge, men
registrerar **inte** avgång. Faktisk avgång och ankomst ligger fortsatt på
skärmens uttryckliga A/B-val.

Gamla klienter kan fortfarande skicka enskilda tangenter till den nya servern.
En ny ESP8266-klient mot äldre server visar uppdateringsbehov och skickar inte
siffror som reservlösning. Därför ska **serveruppdateringen installeras före
den nya firmwaren**. Inga port-, discovery- eller stationstilldelningsändringar
krävs för denna utökning.

Telefonens webbtest håller siffrorna i webbläsaren och skickar hela numret till
Arduino-kortet vid `#`. Kortet validerar inmatningskontext och använder samma
atomiska MQTT-kommando som den fysiska knappsatsen. `*` avbryter utan siffrorna.

## Tilldelning och kontaktkontroll är olika saker

ESP8266 skickar `device/{id}/hello` vid anslutning/återhämtning, inte var tionde
sekund. Servern svarar med tilldelning och, om tilldelad, en aktuell skärmbild.
Adminändringar skickas direkt via `publish_device_assignment`; även borttagning
häver gamla behörigheter. En klient utan station väntar efter bekräftad registrering.

Den kompatibla v1-utökningen använder befintlig `client/{id}/presence`:

- `status: online`, ett nytt `request_id`, aktuell `panel_id` och valfri `state_token`.
- Varje klientsnapshot får `state_token`: SHA-256 av hela servervyn, inklusive
  session, revision, inmatningsägare och klocka. Det är **inte en behörighetsnyckel**.
- Om tilldelning och innehåll stämmer svarar servern endast på `client/{id}/state`
  med `status: current`, samma `request_id`, `panel_id` och `state_token`.
- Om innehållet ändrats, eller klienten uttryckligen saknar giltigt snapshot,
  skickas aktuell skärmbild. Ingen ny tilldelning behövs för ett vanligt trafikkommando.
- En otilldelad box får en kort `waiting_for_assignment`-kvittens. Om dess panel
  inte längre motsvarar adminvalet skickas gällande tilldelning som återhämtning.
- Tilldelningen kontrolleras före innehållsjämförelsen; en borttagen box får inte
  behålla åtkomst genom en gammal men matchande fingerprint.
- Statussvaret är aldrig retained. Sparade kontaktkontroller besvaras inte.
  Boxen accepterar endast senaste begäran med exakt aktuell panel/fingerprint.
  Kontaktkontroll kan inte kvittera ett trafikkommando eller återuppliva en utgången vy.

Kontaktkontrollen körs efter tio sekunder utan aktuellt svar. Efter trettio
sekunder utan giltigt serversvar spärras gammal inmatning och boxen återansluter.
En obesvarad registrerings-/statusbegäran försöks igen efter fem sekunder;
trafikkommandon skickas aldrig automatiskt igen. Klockan jämförs även när
trafikrevisionen är oförändrad, så en separat klockändring fortfarande syns.

Gamla v1-klienter som bara skickar `status: online` får fortsatt hela skärmbilden.
Ny firmware mot äldre server får också hela skärmbilden via samma presence-topic,
men ingen periodisk tilldelning. Ingen ändring av MQTT-prefix, portar eller ESP32
krävs. Uppdatera server och firmware för hela optimeringen.

## Verifiering

- `test_buffered_train_entry.py`: komplett nummer, begäran/direktreservation,
  dubblett, tomt/felaktigt nummer, fel ägare, fel session, gammal revision,
  för gammalt kommando och avbrott.
- `test_mqtt_commands.py`: oförändrade svar utan omtilldelning, klockändring utan
  ny trafikrevision, revokering, missad adminändring och äldre klienter.
- `test_mqtt_integration.py`: riktig MQTT 3.1.1-klient mot lokal testbroker,
  direkt adminpush, sex kontaktkontroller utan extra tilldelning/skärmbild,
  uttrycklig skärmuppdatering och efterföljande trafikkommando.
- Firmwareprojektets `esp8266_input_test.cpp`: maxlängd, bevarad redigering vid
  återkommande snapshot, kontextbyte, avsaknad av serverstöd och rensning;
  simulerad minut utan periodisk tilldelning, svarsbortfall, återanslutning,
  tidsräknarens omslag och spärr mot att kontaktkontroll kvitterar trafik.

Ingen fysisk ESP8266 har provats i denna ändring. Detta dokument gäller
MQTT v1 och ska inte läsas som en beskrivning av v2:s trafikflöden eller
webbklienternas separata registreringsflöde.
