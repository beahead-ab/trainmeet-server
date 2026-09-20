# Fristående TMBox-provbank på testservern

Adress: `https://server.trainmeet.app/tmbox-lab/`.
Detta är **inte** en Cloud-funktion och ansluter inte till Serverns riktiga träff.

Tjänsten lyssnar endast på `127.0.0.1:8797`, bakom befintlig HTTPS-proxy.
Alla trafikregler och texter körs i samma Python-kod som den lokala provbänken.
Ingen egen klientbaserad trafikmotor, databas, MQTT-anslutning eller produktions-
konfiguration används. Stationerna MUN/CDA/VA är förhandstilldelade testdata.

Varje webbläsare har sin egen slumpmässiga sessionscookie (Secure, HttpOnly,
SameSite=Strict, avgränsad till `/tmbox-lab/`). Flikar i samma webbläsarprofil
delar test; en annan profil/privat fönster får ett separat test. Inget konto
behövs. Sessionen är tillfällig: högst fyra timmar, 30 minuter utan aktivitet,
och försvinner vid omstart. Ladda om sidan för ett nytt test.

## Begränsningar och skydd

- Högst 24 sessioner, 24 nyskapade/minut, två samtidiga eventströmmar/session,
  32 eventströmmar totalt, 30 kommandon/10 sekunder/session och 48 HTTP-arbetstrådar.
- Senaste 100 trafikhändelser och 512 kommando-ID:n i minnet, ingen beständig data.
- Gamla vyer/inmatningar blir ogiltiga vid nollställning och kan inte återskapa
  trafik. Endast den egna sessionen nollställs eller byter testläge.
- Alla skrivningar kräver rätt HTTPS-origin och sessionscookie. Okända
  produktions-/fil-/administrationsrutter exponeras inte.
- Tjänsten kör som separat dynamisk systemanvändare, skrivskyddat filsystem,
  inga produktionsdatakataloger, endast loopback-nätverk, 96 MB minnestak och
  35 procent CPU-kvot. Det är en begränsad demonstrationsmiljö, inte produktion.

## Installation

`deploy.py` är en explicit **förstagångsinstallation** på den befintliga
Linux-testvärden. Den kräver en separat payload med `REVISION` och
`MANIFEST.json` (relativ filväg → SHA-256), skapad från en fast git-commit.
Payloaden ska innehålla bara provbänken, dess importerade Python-domänmoduler,
webbfilerna och detta deploymentskript. Inga miljöfiler eller databaser.

Skapa arkivet från en committad revision med:

```sh
python3 deploy/terminal16/package.py --revision FULL_SHA --output /tmp/tmbox-lab.tar.gz
```

Överför arkivet, jämför dess SHA-256, packa upp i en ny tom katalog och kör:

```sh
python3 /root/PAYLOAD/deploy/terminal16/deploy.py --payload /root/PAYLOAD --sha FULL_SHA
```

Installationen kontrollerar ledig port, exakt tidigare proxydel, hälsa och
starttider. Den säkerhetskopierar proxyfilen, startar endast den nya tjänsten,
validerar Caddy och laddar om proxyn utan att starta om Server eller Cloud.
Clouds proxyfil verifieras oförändrad. Slutkontrollen gör ett HTTPS-anrop med
certifikatvalidering och jämför de befintliga tjänsternas versioner/starttider.
Resultatet skrivs till `/opt/trainmeet-tmbox-lab/deployment.json`.

Vid misslyckande stängs den nya provtjänsten av och vår egen proxyändring
återställs om ingen annan hunnit ändra filen. Ingen riktig träff återställs.
En framtida uppdatering ska byta en versionslåst release och starta om enbart
`trainmeet-tmbox-lab`; alla pågående tester blir då tomma. Kör inte
förstagångsskriptet en gång till.
