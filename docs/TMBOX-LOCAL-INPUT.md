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

Telefonens webbtest skickar fortfarande en tangent till Arduino-kortet; kortet
är då den lokala inmatningsenheten. Dessa HTTP-anrop är inte trafikkommandon
till TrainMeet Server. Både fysisk och virtuell knappsats använder samma buffert.

## Verifiering

- `test_buffered_train_entry.py`: komplett nummer, begäran/direktreservation,
  dubblett, tomt/felaktigt nummer, fel ägare, fel session, gammal revision,
  för gammalt kommando och avbrott.
- `test_mqtt_commands.py`: kompatibel äldre meddelandehantering.
- Firmwareprojektets `esp8266_input_test.cpp`: maxlängd, bevarad redigering vid
  återkommande snapshot, kontextbyte, avsaknad av serverstöd och rensning.

Ingen fysisk ESP8266 har provats i denna ändring. V2-protokollets större
trafikflödesrevision och den öppna virtuella klientregistreringen är separata
planerade arbeten, inte införda av denna v1-utökning.
