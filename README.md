# TrainMeet Server

TrainMeet Server är den lokala, självständiga driftsmiljön för en TrainMeet-träff. Den körs på en Raspberry Pi och fortsätter fungera utan internet. Servern äger stationer, spårförbindelser, körsätt, aktiv tidtabell, trafiktillstånd och alla anslutna Tamboxar.

Den centrala [TrainMeet-applikationen](https://github.com/beahead-ab/trainmeet) används längre fram för att bygga och publicera konfigurationer och bearbeta importerade tidtabeller. Själva träffen körs lokalt här.

## Installera på en ren Raspberry Pi eller Ubuntu-server

På en ny Raspberry Pi med Raspberry Pi OS (64-bit) eller en Ubuntu-server kan
hela installationen startas med en rad:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sudo sh
```

Samma kommando kan köras igen för att uppdatera installationen till aktuell
version. Befintlig träffkonfiguration och trafikhistorik ligger kvar.

Installationen frågar efter ditt vanliga `sudo`-lösenord när det behövs. På en
molnserver där du redan är inloggad som `root` kan `sudo` utelämnas:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sh
```

Så länge repot är privat klonar man det i stället med ett GitHub-konto som har
åtkomst och kör:

```sh
sudo ./scripts/install-raspberry-pi.sh
```

Installationen lägger in Python, Mosquitto och mDNS/Bonjour, skapar systemtjänsten `trainmeet-server` och lagrar driftsdata i `/var/lib/trainmeet-server`. Efter omstart startar allt automatiskt.

Öppna därefter `http://trainmeet.local:8787` eller den IP-adress som installationsprogrammet skriver ut.

## Samma paket på Mac, Raspberry Pi och Kubernetes

Servern publiceras som en versionsmärkt OCI-image för både `linux/amd64` och
`linux/arm64`. Det är samma program och datamodell i alla miljöer.

### Mac eller annan dator med Docker

På Mac är Docker/Colima den rekommenderade miljön, eftersom samma image sedan
kan köras på Raspberry Pi och Kubernetes. Installera verktygen en gång och kör
installationskommandot:

```sh
brew install colima docker docker-compose
./scripts/install-docker-mac.command
```

Öppna `http://127.0.0.1:8787`. Compose startar TrainMeet Server och Mosquitto
som två tjänster med beständiga volymer. Colima startas automatiskt vid
inloggning och containrarna använder `restart: unless-stopped`.

För en manuell start används `docker compose up --build --detach`, eller
`docker-compose up --build --detach` när Homebrew har installerat det fristående
kommandot. Stoppa utan att ta bort data med `docker compose down`; lägg till
`--volumes` endast när även den lokala konfigurationen och trafikhistoriken ska
raderas.

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
När Ingress aktiveras slår Helm-chartet automatiskt på externt inloggningsläge,
så att proxyns interna IP-adress aldrig ger automatisk adminbehörighet. Konfigurera
först användarnamn och lösenord via lokal åtkomst eller `kubectl port-forward`,
och aktivera sedan Ingress. Använd TLS för all extern trafik.

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

## Lokal och extern adminåtkomst

Webbadmin öppnas direkt när anropet kommer från datorn eller Raspberry Pi:n
som kör servern. En annan telefon eller dator, även på träffens Wi-Fi, får
inloggningsvyn. Vid en helt ny installation tillåts den första uppsättningen
från det privata nätet tills ett lösenord har valts. Under
**Extern admininloggning** väljer den lokala administratören ett användarnamn
och ett lösenord på minst åtta tecken. Lösenordet lagras saltat och hashat;
externa webbläsare får en tidsbegränsad HttpOnly-session efter inloggning.

Bakom en reverse proxy eller Kubernetes Ingress ska servern startas med
`--force-external-auth` eller `TRAINMEET_FORCE_EXTERNAL_AUTH=true`. Annars ser
servern proxyhoppets privata adress i stället för slutanvändarens externa adress.
Helm-chartet gör detta automatiskt när `ingress.enabled=true`.

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

Ett trafikärende tillhör sträckan på servern, inte Tamboxens aktuella skärm.
När en operatör har begärt ett tåg återgår panelen därför direkt till sin
A–D-översikt och kan hantera nästa tåg. Väntande, inkommande och godkända
ärenden markeras i respektive A–D-position och öppnas med samma riktningsknapp.
Ett svar från en annan station avbryter aldrig en tågnummerinmatning som redan
pågår. Den tillfälliga panelinteraktionen rensas vid serveromstart, medan
begäran, reservationer och belagda sträckor återställs från SQLite.

MQTT är avsiktligt lösenordsfritt på träffens lokala nät. Servern ska inte exponeras direkt mot internet.

## Native Mac-installation som reserv

Docker är huvudmiljön på Mac. Den tidigare native-installationen finns kvar som
en enkel reserv för felsökning utan container-runtime:

```sh
brew install mosquitto
./scripts/start-mac.command
```

Kör aldrig native-versionen och Docker samtidigt på standardportarna. Skriptet
`install-docker-mac.command` stänger därför av native LaunchAgent automatiskt.

## Lokal konfiguration och tidtabell

Servern kan skapa och aktivera en träff helt lokalt. Den kan också installera ett normaliserat, versionsmärkt runtime-paket från centrala TrainMeet. PDF-import, tolkning och manuell kontroll ligger kvar centralt; den färdiga tidtabellen körs lokalt i SQLite.

Under den nuvarande utvecklingsfasen stöds endast runtime-schema 2. Vi håller inte
ett kompatibilitetslager för äldre testformat innan den första externa releasen;
det gör att stations-, tidtabells- och skärmmodellen kan utvecklas utan onödig
komplexitet. Före en publik release införs dokumenterade migreringar mellan
stabila schemaversioner.

Viktiga API:er:

- `GET/POST /v1/local-configuration`
- `POST /v1/local-configuration/activate`
- `POST /v1/server/restart`
- `POST /v1/runtime/install`
- `POST /v1/runtime/sync`
- `GET/POST /v1/runtime/update`
- `POST /v1/runtime/activate`
- `GET /v1/runtime`
- `GET /v1/timetable?station_id=...`
- `GET /v1/display`
- `POST /v1/clock`

Den första sexsiffriga synkkoden kopplar servern permanent till träffen. Därefter
kan admin söka efter en ny central publicering och hämta den till ett lokalt
vänteläge. Den aktiva körningen ändras först när administratören väljer
**Aktivera hämtad version**. Internetavbrott påverkar därför aldrig en redan
aktiv träff.

Under fliken **Skärmar** finns lokala helskärmsvyer för banöversikt, tågdiagram,
träffklocka och en kombinerad översikt. De hämtar lägesbilden från Raspberry
Pi:n, inte från molnet, och återansluter automatiskt efter nätavbrott. Där finns
också klockstyrning för starttid, hastighet och stopporsak. Samtliga elva
analoga TrainMeet-klockor samt den digitala klockan kan användas. Färger,
typografi, kort, fullskärmsuttryck och Tamboxens särskilda proportioner beskrivs
i [den grafiska identiteten](docs/GRAPHIC_IDENTITY.md).

## Utveckling och test

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[mqtt]'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Den fysiska boxens firmware finns i [trainmeet-tambox](https://github.com/beahead-ab/trainmeet-tambox). Den nativa appen finns separat i [trainmeet-iphone](https://github.com/beahead-ab/trainmeet-iphone).
