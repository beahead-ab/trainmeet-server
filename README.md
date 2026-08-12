# TrainMeet Server

TrainMeet Server är den lokala, självständiga driftsmiljön för en TrainMeet-träff. Den körs på en Raspberry Pi och fortsätter fungera utan internet. Servern äger stationer, spårförbindelser, körsätt, aktiv tidtabell, trafiktillstånd och alla anslutna Tamboxar.

Den centrala [TrainMeet-applikationen](https://github.com/beahead-ab/trainmeet) används längre fram för att bygga och publicera konfigurationer och bearbeta importerade tidtabeller. Själva träffen körs lokalt här.

## Välj installation

| Plattform | Rekommenderad metod | När den passar |
| --- | --- | --- |
| Raspberry Pi OS 64-bit | Enradaren nedan | Normal lokal drift på en träff |
| Ubuntu/Debian-server | Enradaren nedan | DigitalOcean, VPS eller annan fristående Linuxserver |
| Windows 10/11-PC | Docker Desktop | Lokal testserver på en vanlig PC |
| Mac | Docker med Colima | Lokal utveckling och test med samma containerupplägg som i drift |
| Linux med Docker | Docker Compose | Server eller dator där Docker redan finns |
| Raspberry Pi med Docker | Docker Compose | När all lokal drift ska vara containerbaserad |
| Kubernetes/K3s | Helm | Centrera eller annan klustermiljö |
| Mac utan Docker | Native reservinstallation | Felsökning och äldre lokala installationer |

Servern använder port `8787` för webben och `1883` för MQTT. Driftsdata är
beständig och ska överleva både uppdateringar och omstarter.

## Snabbstart för första installationen

TrainMeet består av två installationer. **TrainMeet Server** körs på PC, Mac,
Raspberry Pi eller Linuxserver. **TrainMeet Tambox** installeras på varje
ESP32/Arduino-enhet. Installera servern först.

> **Första inloggningen på en ny server**
>
> Användarnamn: **`admin`**
>
> Tillfälligt lösenord: **`TrainMeet2026!`**
>
> Lösenordet måste bytas direkt. En uppdatering ersätter aldrig ett redan valt
> lösenord.

### Windows-PC

1. Installera [Git för Windows](https://git-scm.com/download/win) och
   [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Starta Docker Desktop. Öppna sedan **Start**, skriv `PowerShell` och starta
   Windows PowerShell.
3. Kör ett kommando i taget:

```powershell
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
docker compose up --detach
```

4. Öppna `http://127.0.0.1:8787` och logga in med uppgifterna ovan.
5. Uppdatera senare från samma mapp:

```powershell
git pull
docker compose pull
docker compose up --detach
```

Visa status med `docker compose ps`. Stoppa utan att radera data med
`docker compose down`. Använd inte `--volumes` om data ska behållas.

### Mac

1. Öppna **Terminal** via Spotlight (`⌘` + mellanslag, skriv `Terminal`).
2. Installera [Homebrew](https://brew.sh/) om det saknas.
3. Kör ett kommando i taget:

```sh
brew install colima docker docker-compose git
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
./scripts/install-docker-mac.command
```

4. Öppna `http://127.0.0.1:8787`. Uppdatera senare från samma mapp:

```sh
git pull
./scripts/install-docker-mac.command
```

### Raspberry Pi

1. Installera Raspberry Pi OS 64-bit och anslut Pi:n till nätverket.
2. Öppna Terminal på Pi:n. Från Mac, Linux eller Windows PowerShell kan du i
   stället ansluta med SSH:

```sh
ssh pi@RASPBERRY-PI-IP
```

   Ersätt adressen, exempelvis med `192.168.1.50`. Svara `yes` första gången
   och skriv sedan Pi-användarens lösenord.
3. Installera servern:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sudo sh
```

4. Öppna `http://trainmeet.local:8787` eller adressen som installationen visar.
5. Uppdatera genom SSH genom att köra exakt samma installationskommando igen.
   Data i `/var/lib/trainmeet-server` bevaras.
6. Kontrollera med `systemctl status trainmeet-server`, tryck `q` och avsluta
   SSH med `exit`.

### DigitalOcean eller annan Linuxserver via SSH

Öppna Terminal på Mac/Linux eller PowerShell i Windows och kör:

```sh
ssh -i SÖKVÄG-TILL-NYCKEL root@SERVERNS-IP
```

Exempel:

```sh
ssh -i ~/.ssh/trainmeet_digitalocean root@157.230.109.13
```

Svara `yes` första gången. När prompten visar exempelvis
`root@trainmeet-server:~#` är du inne. Installera eller uppdatera med:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sh
systemctl status trainmeet-server
```

Tryck `q` och avsluta med `exit`. På en publik server ska HTTPS/reverse proxy
användas och MQTT-port `1883` får inte exponeras mot internet.

### ESP32/Arduino Tambox

Firmware finns i [trainmeet-tambox](https://github.com/beahead-ab/trainmeet-tambox).
En Arduino Uno räcker inte eftersom boxen behöver Wi-Fi; lösningen är byggd för
ESP32 och ESP32-S3.

1. Installera [Visual Studio Code](https://code.visualstudio.com/) och tillägget
   **PlatformIO IDE** på PC eller Mac.
2. Hämta firmware:

```sh
git clone https://github.com/beahead-ab/trainmeet-tambox.git
cd trainmeet-tambox/firmware/esp32
```

3. Anslut ESP32 med USB och kontrollera först
   [kopplingsguiden](https://github.com/beahead-ab/trainmeet-tambox/blob/main/firmware/esp32/WIRING.md).
4. För Bennys befintliga box, bygg och ladda:

```sh
pio run -e esp32-benny
pio run -e esp32-benny -t upload
```

   För ny kabeldragning används `esp32-classic-safe`; för ESP32-S3 används
   `esp32-s3`. Kontrollera alltid kort, displayadress och kablage innan en
   befintlig box programmeras.
5. Vid första start visar displayen boxkoden. Om Wi-Fi saknas skapas nätverket
   `TrainMeet-XXXX`. Anslut telefonen, välj träffens Wi-Fi och ange vid behov
   serverns lokala IP-adress.
6. Tilldela boxkoden till rätt station och panel A–D i serverns webbadmin.
   Tamboxen behöver inget eget lösenord.
7. Uppdatera firmware med `git pull` och kör sedan samma upload-kommando igen.
   Boxens permanenta hårdvaru-id ändras inte.

## Raspberry Pi OS 64-bit

Det här är den rekommenderade installationen för en fysisk TrainMeet-server på
träffens lokala nätverk. Börja med en ren Raspberry Pi OS 64-bit-installation,
anslut Pi:n till nätverket och kör i terminalen:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sudo sh
```

Samma kommando kan köras igen för att uppdatera installationen till aktuell
version. Befintlig träffkonfiguration och trafikhistorik ligger kvar.

Installationen lägger in Python, Mosquitto och mDNS/Bonjour, skapar
systemtjänsten `trainmeet-server` och lagrar driftsdata i
`/var/lib/trainmeet-server`. Allt startar automatiskt efter en omstart.

Öppna därefter `http://trainmeet.local:8787` eller den IP-adress som
installationsprogrammet skriver ut. Installationsprogrammet visar även serverns
sexsiffriga anslutningskod.

## Ubuntu, Debian, DigitalOcean eller annan VPS

Samma enradare fungerar på en ren Ubuntu- eller Debianbaserad server:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sudo sh
```

Installationen frågar efter ditt vanliga `sudo`-lösenord när det behövs. På en
molnserver där du redan är inloggad som `root` kan `sudo` utelämnas:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sh
```

Öppna sedan `http://SERVERNS-IP:8787`. På en molnserver ska webbport `8787`
tillåtas i leverantörens brandvägg. Exponera inte MQTT-port `1883` publikt;
den lösenordsfria MQTT-trafiken är gjord för ett betrott lokalt nät.

Manuell installation från en klon används främst vid utveckling:

```sh
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
sudo ./scripts/install-raspberry-pi.sh
```

## Mac med Docker och Colima

Det här är den rekommenderade Mac-miljön. Installera först Homebrew om det inte
redan finns. Klona sedan repot och kör:

```sh
brew install colima docker docker-compose
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
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

## Linux eller Raspberry Pi med Docker

När Docker Engine och Docker Compose redan finns:

```sh
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
docker compose up --detach
```

Compose hämtar den publicerade TrainMeet-imagen och Mosquitto, exponerar
webben på `8787` och MQTT på `1883` samt skapar beständiga volymer. Om en
publicerad image ännu inte finns för den aktuella versionen kan den byggas från
källkoden med:

```sh
docker compose up --build --detach
```

Visa status med `docker compose ps`. Uppdatera med `git pull` följt av samma
startkommando. Stoppa utan att radera data med `docker compose down`. Använd
inte `--volumes` om träffkonfigurationen ska sparas.

## Kubernetes, K3s eller Centrera

Klustret behöver Kubernetes, `kubectl` och Helm. Klona repot på den dator som
har åtkomst till klustret och installera chartet:

```sh
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
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

Automatisk mDNS/Bonjour-upptäckt går inte genom ett vanligt container- eller
molnnät. Vid Compose-test på Mac anges därför datorns lokala IP-adress i den
fysiska boxens Wi-Fi-portal. På en lokal K3s-nod kan `server.hostNetwork=true`
användas när nätmiljön kräver direkt åtkomst till nodens portar.

## Mac native – reserv utan Docker

Docker är huvudmiljön på Mac. För felsökning utan container-runtime:

```sh
brew install python mosquitto
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
./scripts/install-mac.command
```

Installationen skapar en LaunchAgent och startar servern automatiskt vid
inloggning. Öppna `http://127.0.0.1:8787`. Från en iPhone på samma Wi-Fi används
Macens lokala IP-adress, exempelvis `http://192.168.1.20:8787`.

Kör inte native-versionen och Docker samtidigt på standardportarna.
`install-docker-mac.command` stänger därför av den native LaunchAgent som
skriptet känner till.

## Klienter och fysisk Tambox

Serverinstallationen ovan innehåller webbadmin, skärmvyer och
Tambox-simulering. De andra delarna installeras separat:

- Den fysiska ESP32/Arduino-boxens firmware finns i
  [trainmeet-tambox](https://github.com/beahead-ab/trainmeet-tambox).
- Den nativa iPhone-appen finns i
  [trainmeet-iphone](https://github.com/beahead-ab/trainmeet-iphone).
- [TrainMeet TKL](https://github.com/beahead-ab/trainmeet-tkl) är den separata
  stationsapplikationen. Samma UI ingår även under `/tkl/` på servern.

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
och ett lösenord på minst åtta tecken. En ny installation har kontot `admin`
med det tillfälliga lösenordet `TrainMeet2026!` och kräver omedelbart byte vid
första inloggningen. Lösenordet lagras saltat och hashat;
externa webbläsare får en tidsbegränsad HttpOnly-session efter inloggning.

Bakom en reverse proxy eller Kubernetes Ingress ska servern startas med
`--force-external-auth` eller `TRAINMEET_FORCE_EXTERNAL_AUTH=true`. Annars ser
servern proxyhoppets privata adress i stället för slutanvändarens externa adress.
Helm-chartet gör detta automatiskt när `ingress.enabled=true`.

## Uppdatera från webbadmin

På Raspberry Pi, Ubuntu och Debian kan en administratör öppna
**Administration → Uppdatera TrainMeet Server**. Servern kan kontrollera två
kanaler:

- **Stabil** hämtar den senaste märkta GitHub-releasen och är avsedd för träffar.
- **Test** hämtar senaste versionen från `main` och är avsedd för testservrar.

Före installationen säkerhetskopieras SQLite-databasen till
`/var/lib/trainmeet-server/backups`. Uppdateringen körs av en separat root-ägd
systemd-tjänst; webbservern har ingen generell sudo-behörighet. Efter
installationen startar TrainMeet Server om och webbsidan ansluter automatiskt
igen. Docker- och Kubernetesinstallationer uppdateras i stället genom ny image
respektive Helm-deployment.

## Arkitektur

```text
TrainMeet centralt (konfiguration och import, valfritt)
                         |
                         v
Raspberry Pi: TrainMeet Server + SQLite + Mosquitto
        |              |              |              |
        v              v              v              v
  fysisk ESP32    Swift-klient   webbsimulering   TKL-terminal/webb
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
- `GET /v1/tkl/context?station_id=...`
- `POST /v1/tkl/shift/start`
- `POST /v1/tkl/shift/finish`
- `POST /v1/tkl/movement`
- `POST /v1/tkl/line`

TKL-terminalen kopplas en gång med en lokal sexsiffrig kod, eller använder
adminsessionen när `/tkl/` öppnas i en extern webbläsare. Före varje körning tar
en namngiven operatör stationen i tjänst. Pågående trafikärenden överlever
överlämning, terminalbyte och serveromstart. Tågklarering, avgång och ankomst går
via samma auktoritativa trafikmotor som de fysiska Tamboxarna.

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
