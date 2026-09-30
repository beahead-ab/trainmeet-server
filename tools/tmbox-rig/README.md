# TMBox-riggen

Den riktiga servern, en separat mosquitto som på Pi:n, och låtsasboxar som
beter sig som firmware 0.7.1 (`firmware/common/server_terminal.h`): samma
klient-id, clean session, keepalive 10 s, `hello` vid anslutning, `presence`
var femte sekund, femton sekunders tålamod och ny `boot` vid återanslutning.
Två låtsaswebbläsare frågar samma adresser i samma takt som i en verklig journal.

Riggen mäter tiden från varje `presence` till dess `alive`, hur många
sessioner som dör, och svarstiden per HTTP-adress.

```
export RIG_DIR=/tmp/rig
PYTHONPATH=src:tests python3 tools/tmbox-rig/seed.py $RIG_DIR/state
tools/tmbox-rig/start.sh timed
cd tools/tmbox-rig && SIM_SPEED=1 python3 rig.py 5 60     # 5 boxar, 60 s
tools/tmbox-rig/stop.sh
```

`STALL_AT=25 STALL_SECONDS=20 tools/tmbox-rig/start.sh timed` lägger in en
konstgjord spärr på arbetstråden med trafiklåset taget, för att se att riggen
känner igen felet. `SLOW_MS=900` gör varje meddelande så mycket långsammare,
som en långsam databasskrivning skulle göra.

Låtsasboxarna sparar pingar från sessioner de redan gett upp. Kommer svaret
ändå räknas det som ett sent svar - servern svarade, men för sent.

## Uppmätt 2026-09-30

| Läge | Besvarade pingar | Döda sessioner | presence→alive |
|---|---|---|---|
| 5 boxar, ingen last | 30/30 | 0 | p50 44 ms, max 46 ms |
| 5 boxar, simulering, två webbläsare | 60/60 | 0 | p50 44 ms, max 48 ms |
| Samma, 20 s spärr på arbetstråden | 60/70 | 5 — alla inom 0,8 s | max 4,1 s efter spärren |

Serverns eget arbete per `presence` är cirka 4 ms. Resten av de 44 ms är
TCP-fördröjning i klienterna och spelar ingen roll för felet.

Under spärren fryser `/v1/display` lika länge (19,9 s), eftersom den tar samma
lås. Övriga adresser svarar som vanligt. En journal där `/v1/display` svarar
utan avbrott medan boxarna tappar kontakten utesluter alltså att trafiklåset
är orsaken.

### Långsam server plus en enda störning

Varje meddelande görs långsamt (`SLOW_MS`) och en enda 17 s spärr läggs in
efter 40 s (`STALL_AT=40 STALL_SECONDS=17`). 5 boxar, 90 s.

| ms per meddelande | Efter spärren | Sena svar |
|---|---|---|
| 150 | varje box dör en gång, sedan normalt | 15, 9-17 s sena |
| 450 | varje box dör en gång, sedan normalt | 15, 12-19 s sena |
| 900 | **fastnar: varje ny session dör efter exakt två pingar** | 30, 16-21 s sena |
| 1300 | **fastnar på samma sätt** | 29, 28-41 s sena |

Utan spärren klarade även 1300 ms sig utan en enda död session: svaren kom upp
till 27 s sent, men de kom var femte sekund, och boxen mäter tiden mellan svar,
inte hur gamla de är. Det ger långa knapptryck, inte avbrott.

Över tröskeln håller boxarna själva igång kollapsen. Pingar från döda sessioner
ligger kvar i kön och kostar lika mycket att hantera, och varje återanslutning
lägger till en ny `hello`. Servern svarar hela tiden, loggar inga fel, och
`/v1/display` svarar - långsamt, men utan att frysa. Det är samma bild som
fältet gav 2026-09-30.

### Samma scenario efter att livstecknet flyttats till kanten

`Terminal16Gateway` besvarar nu `presence` innan kön, och arbetaren hoppar över
det som hunnit bli inaktuellt. Samma rigg, samma spärr, båda versionerna:

| ms per meddelande | 1.17.0 | Efter |
|---|---|---|
| 900 | 15 döda sessioner, 10 av dem efter exakt två pingar; 25 svar 17-21 s för sent | **0 döda**, alive högst 50 ms |
| 1300 | 15 döda sessioner, 10 av dem efter exakt två pingar; 26 svar 29-41 s för sent | **0 döda**, alive högst 51 ms |

Det här tar bort kollapsen, inte långsamheten. Vid 1300 ms per meddelande
väntar arbetet fortfarande 3-5 s i kön, och ett knapptryck väntar lika länge.
Firmware 0.7.1 ger upp om ett kommando efter 5 s. Riggens boxar trycker inte på
knappar, så den gränsen är inte uppmätt här.

Journalen säger nu själv vad som händer:

```
TMBox esp8266-308398b55263 ansluten (session e5419c4d028b9980-1)
TMBox-kön ligger efter: 22.5 s väntetid (värst 22.5 s), 20 i kön, 1 inaktuella hoppades över
TMBox-kön ligger efter: 3.2 s väntetid (värst 18.3 s), 2 i kön, 20 inaktuella hoppades över
```
