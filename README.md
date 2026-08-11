# TrainMeet Server

TrainMeet Server är den lokala, självständiga driftsmiljön för en TrainMeet-träff. Den körs på en Raspberry Pi och fortsätter fungera utan internet. Servern äger stationer, spårförbindelser, körsätt, aktiv tidtabell, trafiktillstånd och alla anslutna Tamboxar.

Den centrala [TrainMeet-applikationen](https://github.com/beahead-ab/trainmeet) används längre fram för att bygga och publicera konfigurationer och bearbeta importerade tidtabeller. Själva träffen körs lokalt här.

## Installera på en ren Raspberry Pi

När repot är publikt kan hela installationen startas med en rad:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sudo sh
```

Under den privata utvecklingsfasen klonar man repot med ett GitHub-konto som har åtkomst och kör:

```sh
sudo ./scripts/install-raspberry-pi.sh
```

Installationen lägger in Python, Mosquitto och mDNS/Bonjour, skapar systemtjänsten `trainmeet-server` och lagrar driftsdata i `/var/lib/trainmeet-server`. Efter omstart startar allt automatiskt.

Öppna därefter `http://trainmeet.local:8787` eller den IP-adress som installationsprogrammet skriver ut.

## Två tydligt separerade webbdelar

- **TrainMeet Server** är administrationen. Här definieras träffen, stationernas ordning, enkel- och dubbelspår, körsätt, paneler A–D, boxkopplingar, aktiv tidtabell och lokal klocka.
- **Tambox-simulering** använder samma serverstyrda logik, 16×2-display och tangentbord som de fysiska och nativa klienterna.

Ändringar sparas först som ett utkast och aktiveras uttryckligen. Om topologin ändras krävs serveromstart, så en pågående körning inte ändras tyst. Administrationsvyn har en knapp för kontrollerad omstart.

## Arkitektur

```text
TrainMeet centralt (konfiguration och import, valfritt)
                         |
                         v
Raspberry Pi: TrainMeet Server + SQLite + Mosquitto
             |                 |                 |
             v                 v                 v
       fysisk ESP32       Swift-klient      webbsimulering
```

Raspberry Pi:n är alltid auktoritativ. MQTT används som transport med QoS 1, retained snapshots och idempotenta kommandon. En klient som tappar nätet återansluter, presenterar sig igen och får hela det aktuella läget. Klienterna avgör aldrig själva om ett tåg får skickas.

MQTT är avsiktligt lösenordsfritt på träffens lokala nät. Servern ska inte exponeras direkt mot internet.

## Kör på Mac under utveckling

Installera Mosquitto en gång och starta servern:

```sh
brew install mosquitto
./scripts/start-mac.command
```

Öppna `http://127.0.0.1:8787`. För automatisk start vid inloggning kör man `./scripts/install-mac.command` en gång.

## Lokal konfiguration och tidtabell

Servern kan skapa och aktivera en träff helt lokalt. Den kan också installera ett normaliserat, versionsmärkt runtime-paket från centrala TrainMeet. PDF-import, tolkning och manuell kontroll ligger kvar centralt; den färdiga tidtabellen körs lokalt i SQLite.

Viktiga API:er:

- `GET/POST /v1/local-configuration`
- `POST /v1/local-configuration/activate`
- `POST /v1/server/restart`
- `POST /v1/runtime/install`
- `POST /v1/runtime/sync`
- `GET /v1/runtime`
- `GET /v1/timetable?station_id=...`

## Utveckling och test

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[mqtt]'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Den fysiska boxens firmware finns i [trainmeet-tambox](https://github.com/beahead-ab/trainmeet-tambox). Den nativa appen finns separat i [trainmeet-iphone](https://github.com/beahead-ab/trainmeet-iphone).

