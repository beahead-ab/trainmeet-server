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
känner igen felet.

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
