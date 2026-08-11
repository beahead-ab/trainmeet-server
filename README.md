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

## Samma paket på Mac, Raspberry Pi och Kubernetes

Servern publiceras som en versionsmärkt OCI-image för både `linux/amd64` och
`linux/arm64`. Det är samma program och datamodell i alla miljöer.

### Mac eller annan dator med Docker

```sh
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
docker compose up --build --detach
```

Öppna `http://127.0.0.1:8787`. Compose startar TrainMeet Server och Mosquitto
som två tjänster med beständiga volymer. Stoppa utan att ta bort data med
`docker compose down`; lägg till `--volumes` endast när även den lokala
konfigurationen och trafikhistoriken ska raderas.

Homebrew installerar på vissa Macar kommandot som `docker-compose`; det kan
användas på exakt samma sätt om `docker compose` inte hittas.

Om den vanliga Mac-servern redan använder portarna kan containerversionen
provas parallellt:

```sh
TRAINMEET_HTTP_PORT=18787 TRAINMEET_MQTT_PORT=11883 \
  docker compose up --build --detach
```

Öppna då `http://127.0.0.1:18787`.

På en Raspberry Pi med Docker används exakt samma kommando. GitHub-imagen är
byggd för ARM64. Det vanliga installationsskriptet ovan finns kvar för den som
inte vill installera en container-runtime på sin Pi.

### Centrera eller annan Kubernetesmiljö

```sh
helm upgrade --install trainmeet \
  ./deploy/helm/trainmeet-server \
  --namespace trainmeet \
  --create-namespace
```

Helm-chartet skapar ett StatefulSet med exakt en auktoritativ server, en
Mosquitto-sidecar och två PersistentVolumeClaims. För en K3s-installation på
det lokala nätet kan HTTP och MQTT exponeras med:

```sh
helm upgrade --install trainmeet ./deploy/helm/trainmeet-server \
  --namespace trainmeet --create-namespace \
  --set service.type=LoadBalancer
```

För ett centralt kluster bör bara webbgränssnittet exponeras via Ingress. Den
lösenordsfria MQTT-porten är avsedd för träffens lokala nät, inte internet.

Så länge GitHub-repot och GHCR-paketet är privata behöver driftmiljön ett
GitHub registry pull secret. När paketet görs publikt behövs ingen sådan
inloggning.

Automatisk mDNS/Bonjour-upptäckt går inte genom ett vanligt container- eller
molnnät. Vid Compose-test på Mac anges därför datorns lokala IP-adress i den
fysiska boxens Wi-Fi-portal. På en lokal K3s-nod kan `server.hostNetwork=true`
användas när nätmiljön kräver direkt åtkomst till nodens portar.

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
