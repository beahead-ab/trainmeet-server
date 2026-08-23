# TrainMeet Server

TrainMeet Server är den lokala, självständiga driftsmiljön för en TrainMeet-träff. Den körs på en Raspberry Pi och fortsätter fungera utan internet. Servern äger stationer, spårförbindelser, körsätt, aktiv tidtabell, trafiktillstånd och alla anslutna TMBoxar.

[TrainMeet Cloud](https://github.com/beahead-ab/trainmeet-cloud) bygger, validerar och publicerar konfigurationer och tolkar importerade tidtabeller. Själva träffen körs lokalt här. Flödet går bara åt ett håll: Cloud publicerar, den här servern hämtar. Ingenting synkas tillbaka.

## Välj installation

| Plattform | Rekommenderad metod | När den passar |
| --- | --- | --- |
| Raspberry Pi OS 64-bit | Enradaren nedan | Normal lokal drift på en träff |
| Ubuntu/Debian-server | Enradaren nedan | DigitalOcean, VPS eller annan fristående Linuxserver |
| Mac | Enradaren nedan | Lokal server på en Mac, utan Docker |
| Windows 10/11-PC | Docker Desktop | Lokal testserver på en vanlig PC |
| Mac med Docker | Docker med Colima | Utveckling och test med samma containerupplägg som i drift |
| Linux med Docker | Docker Compose | Server eller dator där Docker redan finns |
| Raspberry Pi med Docker | Docker Compose | När all lokal drift ska vara containerbaserad |
| Kubernetes/K3s | Helm | Centrera eller annan klustermiljö |

Servern använder port `8787` för webben och `1883` för MQTT. Driftsdata är
beständig och ska överleva både uppdateringar och omstarter.

Enradarna installerar servern direkt på maskinen, och bara de installationerna
får knappen **Uppdatera TrainMeet Server** i webbgränssnittet. Docker- och
Kubernetesinstallationer uppdateras genom ny image respektive Helm-deployment.

## Snabbstart för första installationen

TrainMeet består av två installationer. **TrainMeet Server** körs på PC, Mac,
Raspberry Pi eller Linuxserver. **TrainMeet TMBox** installeras på varje
ESP32/Arduino-enhet. Installera servern först.

> **En ny server är helt tom.** Den innehåller inga exempelstationer, ingen
> demoträff och inget förvalt administratörskonto. Vid första öppningen leder en
> installationsguide genom administratör, servernamn, konfigurationsserver,
> sexsiffrig träffkod och trafikdag.

### Windows-PC

1. Installera [Git för Windows](https://git-scm.com/download/win) och
   [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Starta Docker Desktop. Öppna sedan **Start**, skriv `PowerShell` och starta
   Windows PowerShell.
3. Kör ett kommando i taget:

```powershell
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
```

> **Skriv adressen till en fil innan servern startas.** Containern ser bara
> sitt eget interna nätverk, inte datorns riktiga IP-adress, så
> anslutningsraden som visas för TMBox-parkoppling behöver få veta den.
> Kör `ipconfig` och leta upp `IPv4-adress` under din Wi-Fi- eller
> Ethernet-adapter, skapa sedan filen `.env` i `trainmeet-server`-mappen med
> den adressen:
>
> ```
> TRAINMEET_ADVERTISED_HOST=192.168.1.50
> ```

```powershell
docker compose up --detach
```

4. Öppna `http://127.0.0.1:8787` och följ installationsguiden.
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
2. Kör installationen. Använd **inte** `sudo` — servern installeras för din
   användare och startar automatiskt när du loggar in:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sh
```

   Saknas Mosquitto installeras det automatiskt med [Homebrew](https://brew.sh/).
   Servern och driftsdata hamnar i
   `~/Library/Application Support/TrainMeet Server`.
3. Öppna `http://127.0.0.1:8787`. Installationen skriver också ut Macens
   IP-adress och serverns sexsiffriga anslutningskod.
4. Uppdatera senare med **Uppdatera TrainMeet Server** under
   **⚙ Inställningar → Programuppdatering**. Databasen säkerhetskopieras först, och
   misslyckas uppdateringen återställs föregående version automatiskt.

### Mac med Docker

Samma containerupplägg som i drift, men utan uppdateringsknapp.

1. Installera [Homebrew](https://brew.sh/) om det saknas.
2. Kör ett kommando i taget:

```sh
brew install colima docker docker-compose git
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
```

> **Skriv adressen till en fil innan servern startas.** Containern ser bara
> sitt eget interna nätverk, inte Macens riktiga IP-adress, så
> anslutningsraden som visas för TMBox-parkoppling behöver få veta den. Kör
> `ipconfig getifaddr en0` för att hitta adressen, skapa sedan filen `.env` i
> `trainmeet-server`-mappen med den:
>
> ```
> TRAINMEET_ADVERTISED_HOST=192.168.1.50
> ```

```sh
./scripts/install-docker-mac.command
```

3. Öppna `http://127.0.0.1:8787`. Uppdatera senare från samma mapp:

```sh
git pull
./scripts/install-docker-mac.command
```

### Raspberry Pi

1. Installera Raspberry Pi OS 64-bit och anslut Pi:n till nätverket. Välj
   **Raspberry Pi OS med skrivbord** om Pi:ns egen skärm automatiskt ska visa
   TrainMeet Server. Lite-versionen passar en skärmlös server.
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

   `install.sh` är den publika installeraren: den hämtar först hela paketet och
   kör därefter Raspberry Pi-installationen. Filer under `scripts/` behöver inte
   anropas direkt. Servern startar direkt — du kan öppna den innan du startar om.

4. Starta om Pi:n när installationen är klar:

```sh
sudo reboot
```

   Omstarten behövs bara på Raspberry Pi OS Desktop, för att autologin och
   Chromium ska starta i skärmläge. På Lite körs servern redan och omstarten kan
   hoppas över. Gör omstarten som ett eget steg — att kedja den direkt efter
   installationen kan få avstängningen att hänga på startbilden, eftersom
   installationen precis hunnit ändra skrivbordets uppstartsläge.

5. Öppna `http://trainmeet.local:8787` eller adressen som installationen visar.
   På Raspberry Pi OS Desktop öppnas TrainMeet Server automatiskt i Chromium
   efter omstart. Webbläsaren väntar på servern och återstartas om den stängs.
   Genvägen **Starta TrainMeet Server** läggs också på skrivbordet.
6. Uppdatera i fortsättningen med **Uppdatera TrainMeet Server** under
   **⚙ Inställningar → Programuppdatering** i webbgränssnittet. Data i
   `/var/lib/trainmeet-server` bevaras.
7. Kontrollera med `systemctl status trainmeet-server`, tryck `q` och avsluta
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

### ESP32/Arduino TMBox

Firmware finns i [trainmeet-tmbox](https://github.com/beahead-ab/trainmeet-tmbox).
En Arduino Uno räcker inte eftersom boxen behöver Wi-Fi; lösningen är byggd för
ESP32 och ESP32-S3.

1. Installera [Visual Studio Code](https://code.visualstudio.com/) och tillägget
   **PlatformIO IDE** på PC eller Mac.
2. Hämta firmware:

```sh
git clone https://github.com/beahead-ab/trainmeet-tmbox.git
cd trainmeet-tmbox/firmware/esp32
```

3. Anslut ESP32 med USB och kontrollera först
   [kopplingsguiden](https://github.com/beahead-ab/trainmeet-tmbox/blob/main/firmware/esp32/WIRING.md).
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
   TMBoxen behöver inget eget lösenord.
7. Uppdatera firmware med `git pull` och kör sedan samma upload-kommando igen.
   Boxens permanenta hårdvaru-id ändras inte.

## Raspberry Pi OS 64-bit

Det här är den rekommenderade installationen för en fysisk TrainMeet-server på
träffens lokala nätverk. Börja med en ren Raspberry Pi OS 64-bit-installation,
anslut Pi:n till nätverket och kör i terminalen:

```sh
curl -fsSL https://raw.githubusercontent.com/beahead-ab/trainmeet-server/main/install.sh | sudo sh
```

Servern startar direkt. Kör därefter `sudo reboot` som ett eget kommando om
Pi:n har skrivbord och ska visa TrainMeet Server på sin egen skärm; på Lite
behövs ingen omstart. Uppdateringar görs sedan under
**⚙ Inställningar → Programuppdatering**; befintlig träffkonfiguration och
trafikhistorik ligger kvar.

Installationen lägger in Python, Mosquitto och mDNS/Bonjour, skapar
systemtjänsten `trainmeet-server` och lagrar driftsdata i
`/var/lib/trainmeet-server`. Allt startar automatiskt efter en omstart.

På Raspberry Pi OS Desktop konfigurerar installationen dessutom automatisk
inloggning och startar Chromium med `http://127.0.0.1:8787/` som en maximerad
serverapplikation. Den lokala sidan öppnas direkt utan extern admininloggning.
På Raspberry Pi OS Lite görs ingen skrivbords- eller webbläsarinstallation.

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

Containern ser bara sitt eget interna nätverk, inte Macens riktiga
IP-adress, så anslutningsraden som visas för TMBox-parkoppling behöver få
veta den explicit. Kör `ipconfig getifaddr en0` för att hitta adressen och
lägg den i en `.env`-fil i `trainmeet-server`-mappen innan `docker compose
up`:

```
TRAINMEET_ADVERTISED_HOST=192.168.1.50
```

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

Containern ser bara sitt eget interna nätverk, inte värdens riktiga
IP-adress, så anslutningsraden som visas för TMBox-parkoppling behöver få
veta den explicit. Lägg värdens IP i en `.env`-fil i `trainmeet-server`-mappen
innan `docker compose up`:

```
TRAINMEET_ADVERTISED_HOST=192.168.1.50
```

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
fysiska boxens Wi-Fi-portal. Podden ser av samma skäl bara klustrets interna
nätverk, inte adressen andra enheter faktiskt når klustret på, så
anslutningsraden som visas för TMBox-parkoppling behöver få veta den
explicit:

```sh
helm upgrade --install trainmeet ./deploy/helm/trainmeet-server \
  --namespace trainmeet --create-namespace \
  --set server.advertisedHost=192.168.1.50
```

Sätt den till Ingress-adressen eller LoadBalancer-adressen beroende på hur
klustret nås. På en lokal K3s-nod kan `server.hostNetwork=true` användas i
stället när nätmiljön kräver direkt åtkomst till nodens portar — då hittar
servern sin egen adress precis som på en vanlig installation, utan att
`advertisedHost` behöver sättas.

## Mac – installationen i detalj

Enradaren under [Mac](#mac) gör allt det här åt dig. Beskrivningen är för den
som vill veta vad som installeras, eller köra installationen från en klon:

```sh
brew install python mosquitto
git clone https://github.com/beahead-ab/trainmeet-server.git
cd trainmeet-server
./scripts/install-mac.command
```

Installationen lägger servern i `~/Library/Application Support/TrainMeet Server`,
skapar en LaunchAgent som startar den automatiskt vid inloggning, och lägger dit
uppdateraren som knappen **Uppdatera TrainMeet Server** använder. Allt ägs av din
egen användare; ingenting installeras som root. Öppna `http://127.0.0.1:8787`.
Från en iPhone på samma Wi-Fi används Macens lokala IP-adress, exempelvis
`http://192.168.1.20:8787`.

Kör inte den här installationen och Docker samtidigt på standardportarna.
`install-docker-mac.command` stänger därför av den LaunchAgent som skriptet
känner till.

## Klienter och fysisk TMBox

Serverinstallationen ovan innehåller webbadmin, skärmvyer och
TMBox-simulering. De andra delarna installeras separat:

- Den fysiska ESP32/Arduino-boxens firmware finns i
  [trainmeet-tmbox](https://github.com/beahead-ab/trainmeet-tmbox).
- Den nativa iPhone-appen finns i
  [trainmeet-iphone](https://github.com/beahead-ab/trainmeet-iphone).
- [TrainMeet TKL](https://github.com/beahead-ab/trainmeet-tkl) är den separata
  stationsapplikationen. Samma UI ingår även under `/tkl/` på servern.

## Två tydligt separerade webbdelar

- **TrainMeet Server** är administrationen. Här definieras träffen, stationernas ordning, enkel- och dubbelspår, körsätt, paneler A–D, boxkopplingar, aktiv tidtabell och lokal klocka.
- **TMBox-simulering** kör exakt samma renderare och tillståndsmaskin som firmwaren, inte en efterlikning: `tmbox-render.js`, `tmbox-nav.js` och `tmbox-attention.js` hålls mot firmwarens egna guldfiler och serverns testsvit faller om de skiljer sig. Den ritar alla fyra displaygeometrier — **16×2, 20×2, 16×4 och 20×4** — som växlas i vyn, så en skärm går att granska i den storlek boxen faktiskt har.

Simulatorn ger också **uppmärksamhetssignalerna**: en ton och en banderoll när
en klarering begärs hit, när ett svar på en egen begäran kommer, eller när
linjen förklaras ledig mot stationen. Policyn är boxens egen — samma
`AttentionController`, hållen mot samma guldfil — och eftersom hårdvaran ännu
saknar summer är det här enda stället signalerna går att höra. Det som inte
låter är minst lika viktigt: en klarering som redan väntade, den första
ögonblicksbilden efter start, och det tågklareraren själv nyss gjorde är tysta.

Ändringar sparas först som ett utkast och aktiveras uttryckligen. Om topologin ändras krävs serveromstart, så en pågående körning inte ändras tyst. Administrationsvyn har en knapp för kontrollerad omstart.

<<<<<<< HEAD
### Typsnitt

Webbadmin serverar Inter från servern själv, i fyra vikter, latin, cirka
92 kB totalt. Sidan hämtade tidigare typsnitt från Google Fonts, vilket
serverns egen Content-Security-Policy (`style-src 'self'`) avvisade vid varje
sidladdning — ett konsolfel per besök och en DNS-uppslagning mot en extern
värd som en server byggd för att köra en träff utan internet aldrig ska
behöva.
=======
## Driftlägen

| Läge | Cloud | Redigering på servern |
|---|---|---|
| `cloud-linked` | nås, är redaktör | **låst** |
| `offline-meet` | nås inte, eller medvetet frånkopplad | **öppen** |

Läget är **beständigt tillstånd som en människa satt** — det överlever omstart
och härleds aldrig ur om Cloud svarade just nu. Ett nätavbrott låser alltså
aldrig upp redigering på egen hand, och ett nät som kommer tillbaka låser
aldrig mitt i någons arbete. En server som aldrig kopplats till Cloud kör
lokalt utan att någon behöver välja.

I `offline-meet` går det att öppna den aktiva Cloud-versionen som en
arbetskopia, rätta tider och spår, och aktivera. Aktiveringen skriver en ny
paketrevision `<bas>+local-rN` genom samma maskineri som en Cloud-publicering,
så TKL och boxarna ser en `config_version` de inte sett och läser om. De vet
inte, och behöver inte veta, att revisionen gjordes lokalt.

Att gå tillbaka till `cloud-linked` betyder att Clouds publicering gäller igen
och att de lokala revisionerna kastas. Det sker **aldrig tyst**: servern visar
först exakt vilka rader som ändrats, lagts till eller tagits bort, och kräver
en bekräftelse. Finns inget lokalt att kasta krävs ingen bekräftelse — en
bekräftelseruta för ingenting lär folk att klicka igenom dem.
>>>>>>> origin/main

## Lokal och extern adminåtkomst

Webbadmin öppnas direkt på datorn eller Raspberry Pi:n som kör servern. Vid en
helt ny installation får den första administratören skapas från servern eller
dess privata nätverk. Det finns inget förvalt användarnamn eller lösenord.
Installationsguiden kräver ett eget användarnamn och ett lösenord på minst åtta
tecken. Lösenordet lagras saltat och hashat; externa webbläsare får en
tidsbegränsad HttpOnly-session efter inloggning.

Bakom en reverse proxy eller Kubernetes Ingress ska servern startas med
`--force-external-auth` eller `TRAINMEET_FORCE_EXTERNAL_AUTH=true`. Annars ser
servern proxyhoppets privata adress i stället för slutanvändarens externa adress.
Helm-chartet gör detta automatiskt när `ingress.enabled=true`.

## Uppdatera från webbadmin

På Raspberry Pi, Ubuntu, Debian och Mac kan en administratör öppna
**⚙ Inställningar → Programuppdatering** och klicka på **Uppdatera TrainMeet Server**.
Uppdateringen hämtar alltid senaste versionen från `main`; det finns inget
kanalval att ta ställning till.

Före installationen säkerhetskopieras SQLite-databasen. Misslyckas
uppdateringen återställs föregående version automatiskt. Efter installationen
startar TrainMeet Server om och webbsidan ansluter automatiskt igen.

### Versionsnummer

TrainMeet Server har ett **användarvänligt versionsnummer** enligt SemVer:
`större.funktion.rättning`. Det står i `VERSION` i repotets rot, och det är
den enda auktoritativa källan — installationsskript, API, webbadmin,
paketering och `pyproject.toml` läser samma fil, och ett test faller om de
säger olika. Tre påståenden hade hunnit glida isär innan den fanns:
`pyproject.toml` sa 0.6.0, User-Agent-strängen sa 0.7, och det som faktiskt
visades var en git-sha.

Git-committen finns kvar, men som det den är: **bygginformation**. Den svarar
på «exakt vilken kod är detta», vilket ett versionsnummer medvetet inte gör.

```text
Version 1.0.0 · build 4bd9c9a
```

I webbadmin står versionsnumret som rubrik och committen under **Teknisk
information**. `GET /healthz` lämnar båda, utan att kräva inloggning — en
hälsokontroll kan inte logga in först — och säger ingenting annat.

### Uppdateringsförloppet

Uppdateringen rapporterar sju verkliga steg, inte en animation:

| Steg | Vad som händer |
|---|---|
| Söker efter uppdatering | frågar GitHub vilken commit `main` står på |
| Hämtar | laddar ner arkivet |
| Verifierar | kontrollerar att arkivet går att packa upp och innehåller installationsskriptet |
| Installerar | säkerhetskopierar databasen och kör installationen |
| Startar om | tjänsten startar om |
| Kontrollerar att tjänsten fungerar | pollar `/healthz` tills den svarar |
| Klart | allt ovan gick igenom |

Vid fel markeras **steget där felet inträffade**, felmeddelandet visas och en
knapp för att försöka igen dyker upp. Föregående version återställs
automatiskt.

**Ett framgångsmeddelande kommer aldrig före hälsokontrollen.** Det gjorde det
förut: `complete` skrevs innan omstarten, så en administratör fick veta att
uppdateringen lyckats innan den ens hade provats en gång.

Stegen är desamma i TrainMeet Cloud. De två uppdateras på helt olika sätt — en
systemd-tjänst som packar upp ett arkiv respektive en värdtjänst som bygger om
en Docker-image — men det en administratör behöver veta är identiskt, så
`src/tmbox_gateway/update_contract.py` är ordagrant samma modul i båda repona
och ett test låser fast den i vardera.

### Vad installationen gör för att en uppdatering ska bita

Fyra steg finns just för att en uppdatering annars kan rapportera framgång
medan den gamla koden fortsätter köra. Var och en har inträffat:

1. **Gammal `src` rensas.** `src/` tas bort innan den nya kopieras in, så att
   ett borttaget paket inte lever kvar bredvid det nya. Bara kod ligger här —
   databasen bor i `STATE_DIR` och rörs inte.
2. **systemd-drop-ins migreras.** En drop-in under
   `/etc/systemd/system/trainmeet-server.service.d/` åsidosätter unit-filen
   installationen just skrev. En egen drop-in med riktiga inställningar — bind-
   adress, extern broker — pekade tyst på `tambox_gateway.local_server` och
   startade paketet vi bytt namn från. Installationen skriver om modulnamnet
   i drop-in:en i stället för att radera någons konfiguration.
3. **Tjänsten startas om, inte bara aktiveras.** `systemctl enable --now` gör
   ingenting alls när tjänsten redan kör. `enable` och `restart` är därför
   separerade.
4. **Effektivt `ExecStart` kontrolleras.** `is-active` är sant för en process
   som kör fel kod lika villigt som för rätt. Efter omstart läses tjänstens
   effektiva `ExecStart` och installationen avbryter med felmeddelande om det
   inte innehåller `tmbox_gateway.local_server` — och pekar på drop-in-
   katalogen som trolig orsak.

På Raspberry Pi och Linux körs uppdateringen av en separat root-ägd
systemd-tjänst; webbservern har ingen generell sudo-behörighet. På Mac ligger
hela installationen i din egen användarmapp, så uppdateringen körs utan root
och utan hjälptjänst.

Docker- och Kubernetesinstallationer saknar knappen med flit: en container kan
inte byta ut sin egen image utan att få tillgång till värdens Docker-socket,
vilket i praktiken ger containern root på värdmaskinen. De uppdateras i stället
genom ny image respektive Helm-deployment.

## Arkitektur

```text
TrainMeet Cloud (import, validering och publicering)
                         |
                         v   enkelriktat: Cloud publicerar, servern hämtar
Raspberry Pi: TrainMeet Server + SQLite + Mosquitto
        |              |              |              |
        v              v              v              v
  fysisk ESP32    Swift-klient   webbsimulering   TKL-terminal/webb
```

Raspberry Pi:n är alltid auktoritativ. MQTT används som transport med QoS 1, retained snapshots och idempotenta kommandon. En klient som tappar nätet återansluter, presenterar sig igen och får hela det aktuella läget. Klienterna avgör aldrig själva om ett tåg får skickas.

Ett operativt beslut lämnas alltid på `A` eller `B`, aldrig på `#`. `KLART`,
`EJ KLART`, `AVGÅTT` och `ANKOMMIT` följer den regeln i trafikmotorn, så
ingen klient kan införa ett eget undantag. `#` väljer, bekräftar inmatad data
och kvitterar visning. Den negativa knappen heter alltid `B=EJ`.

Ett trafikärende tillhör sträckan på servern, inte TMBoxens aktuella skärm.
När en operatör har begärt ett tåg återgår panelen därför direkt till sin
A–D-översikt och kan hantera nästa tåg. Väntande, inkommande och godkända
ärenden markeras i respektive A–D-position och öppnas med samma riktningsknapp.
Ett svar från en annan station avbryter aldrig en tågnummerinmatning som redan
pågår. Den tillfälliga panelinteraktionen rensas vid serveromstart, medan
begäran, reservationer och belagda sträckor återställs från SQLite.

MQTT är avsiktligt lösenordsfritt på träffens lokala nät. Servern ska inte exponeras direkt mot internet.

Loggningen är strukturerad `nyckel=värde` och varje kommando bär ett
korrelations-id — för en TMBox är det kommandots `message_id`. Samma id finns
i loggraderna, i auditjournalen och i trafikmotorns egen post, så hela vägen
från mottaget meddelande till registrerad effekt går att följa. Hemligheter
redigeras bort på fältnamn innan något skrivs.

Ansvarsfördelningen mellan TrainMeet Cloud och servern — vem som får ändra
vad, och hur en ändring rör sig — är fastslagen i
[docs/cloud-server.md](docs/cloud-server.md). Flödet är enkelriktat: Cloud
bygger och trycker ner, servern kör. Servern kan redigera träffen lokalt bara
när den är satt i offline-läge, och de ändringarna lever bara under träffen.

Protokollet mellan en fysisk TMBox och servern är specificerat i
[docs/protocol/v2/](docs/protocol/v2/README.md): topics, meddelandekuvert,
revisionsregler och tillståndsmaskiner, med JSON-scheman och kompletta
exempel för både Charlottendal och den fiktiva testtopologin. Kontraktet är
normativt — säger koden och dokumentet olika saker är det en bugg i koden.
Den nuvarande MQTT-gatewayn talar fortfarande v1; v2-ytan byggs mot det här
kontraktet.

## Lokal konfiguration och tidtabell

Servern kan skapa och aktivera en träff helt lokalt. Den kan också installera
ett normaliserat, versionsmärkt runtime-paket från valfri kompatibel
konfigurationsserver. Standardadressen är `https://cloud.trainmeet.app/config`.
Användaren anger bara serveradressen och en sexsiffrig kod. Den permanenta
länkidentiteten returneras av konfigurationsservern och lagras osynligt lokalt;
ingen lång API-nyckel behöver kopieras eller visas.

En ansluten server kan skicka lokala konfigurationsändringar tillbaka till
TrainMeet Cloud. Stationer, sträckor, TMBoxar och grundinställningar läggs då
som separata poster i en lokal, beständig kö. Cloud-admin godkänner eller avslår
varje post innan den påverkar Cloud-utkastet. Först när utkastet publiceras som
en ny version kan ändringen hämtas tillbaka av lokala servrar.

Administratören kan aktivera automatisk Cloud-synk. Servern kontrollerar då
var femtonde sekund om en ny komplett version har publicerats, hämtar och
aktiverar den samt gör en kontrollerad omstart när trafikmotorns stations- eller
TMBox-konfiguration har ändrats.

Under den nuvarande utvecklingsfasen stöds endast runtime-schema 3. Vi håller inte
ett kompatibilitetslager för äldre testformat innan den första externa releasen;
det gör att stations-, tidtabells- och skärmmodellen kan utvecklas utan onödig
komplexitet. Före en publik release införs dokumenterade migreringar mellan
stabila schemaversioner.

Schema 3 lägger till spårkatalogen. Varje station eller driftplats har en
katalog med stabila spår-id:n, synlig beteckning, aktiv-flagga och
sorteringsordning, och tidtabellens rader refererar ett spår med `track_id` i
stället för att bära en fritextsträng. Servern validerar varje spårskrivning
mot katalogen, oavsett om den kommer från en TMBox, webben eller
TKL-terminalen — ett spår som inte finns går inte att skriva. Ett inaktiverat
spår försvinner ur spårväljaren utan att bryta de rader som redan pekar på
det.

En station kan innehålla flera driftplatser utan att delas upp i flera noder i
banöversikten. Driftpaketet anger då `operating_points` under stationen och
varje importerad tågrad anger `operating_point_id`. Alias används för att
matcha PDF-rubriker till rätt driftplats, inte för att kasta bort skillnaden
mellan dem. Referensfallet Charlottendal publiceras därför som en station med
driftplatserna C och Rbg: stationens topologi och TMBox är gemensamma, medan
spår och tågrörelser behåller sin driftplats.

TKL deklarerar två saker inför en avgång: att tåget är **uppställt** och att
**föraren är på plats**. Båda registreras beständigt på servern och överlever
omstart. **REDO** går inte att sätta — det härleds av servern ur de två
deklarationerna plus serverns egna regler, så ingen klient kan gena förbi dem.

Klarering och «linjen är ledig» är två skilda saker och har skilda endpoints.
`POST /v1/tkl/clearance` driver ett klareringsärende: begär, svara, avbryt,
avgå, ankom. `POST /v1/tkl/line-available` skickar ett ensidigt
linjen-ledig-meddelande, som aldrig beläggningskontrolleras mot en
klareringsbegäran och bara kan kvitteras som visat. `POST /v1/tkl/line` finns
kvar som gammalt namn för klareringsvägen tills terminalerna har flyttat.

TKL och rangerare är två olika operativa roller. TKL ansvarar för hela
stationen, klarering och avgång. Rangeraren arbetar på rangerdriftplatsen,
färdigställer tåget och aviserar TKL, men kan aldrig skicka tåget. TKL
kvitterar överlämningen och väljer avgångstid. Denna ansvarsfördelning ska
upprätthållas av serverns arbetsflöde och inte bara av vilka knappar klienten
råkar visa.

Viktiga API:er:

- `GET/POST /v1/local-configuration`
- `GET /v1/setup`
- `POST /v1/setup/admin`
- `POST /v1/setup/server`
- `POST /v1/setup/complete`
- `POST /v1/local-configuration/activate`
- `POST /v1/server/restart`
- `POST /v1/runtime/install`
- `POST /v1/runtime/sync`
- `GET /healthz`
- `GET/POST /v1/runtime/update`
- `POST /v1/runtime/activate`
- `POST /v1/cloud/auto-sync`
- `GET /v1/runtime`
- `GET /v1/timetable?station_id=...`
- `GET /v1/display`
- `POST /v1/clock`
- `GET /v1/tkl/context?station_id=...`
- `POST /v1/tkl/shift/start`
- `POST /v1/tkl/shift/finish`
- `POST /v1/tkl/movement`
- `POST /v1/tkl/clearance`
- `POST /v1/tkl/line-available`

TKL-terminalen kopplas en gång med en lokal sexsiffrig kod, eller använder
adminsessionen när `/tkl/` öppnas i en extern webbläsare. Före varje körning tar
en namngiven operatör stationen i tjänst. Pågående trafikärenden överlever
överlämning, terminalbyte och serveromstart. Tågklarering, avgång och ankomst går
via samma auktoritativa trafikmotor som de fysiska TMBoxarna.

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
typografi, kort, fullskärmsuttryck och TMBoxens särskilda proportioner beskrivs
i [den grafiska identiteten](docs/GRAPHIC_IDENTITY.md).

## Utveckling och test

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[mqtt]'
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Den fysiska boxens firmware finns i [trainmeet-tmbox](https://github.com/beahead-ab/trainmeet-tmbox). Den nativa appen finns separat i [trainmeet-iphone](https://github.com/beahead-ab/trainmeet-iphone).
