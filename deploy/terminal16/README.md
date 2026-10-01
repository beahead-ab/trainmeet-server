# Fristående TMBox-provbank på testservern

> **Ersatt från TrainMeet Server 1.17.3.** Servern serverar `/tmbox-lab/`
> själv, med samma kod som allt annat, så provbänken följer med varje
> serveruppdatering. Den fristående tjänsten körde en fast commit och drev
> isär: gammal sida och gammal knappsats.
>
> Avveckla den så här, som root på testvärden, efter att Servern är 1.17.3
> eller senare:
>
> ```sh
> python3 retire.py
> ```
>
> Skriptet kontrollerar att Servern själv svarar på `/tmbox-lab/healthz`
> (`"served_by": "server"`), säkerhetskopierar Caddyfile, återställer blocket
> för server.trainmeet.app till exakt det `deploy.py` hittade, validerar och
> laddar om Caddy och kontrollerar via HTTPS att Servern svarar. Först därefter
> stoppas och avaktiveras `trainmeet-tmbox-lab`. Vid fel återställs Caddyfile
> och den gamla tjänsten fortsätter. Server och Cloud startas aldrig om.
> Pågående provsessioner nollställs. Katalogen `/opt/trainmeet-tmbox-lab` och
> säkerhetskopiorna lämnas kvar. Att köra skriptet igen gör bara att tjänsten
> säkert är avstängd.
>
> Resten av den här filen beskriver den gamla tjänsten.

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

För uppdatering, använd det nya paketets `update.py --payload /root/PAYLOAD
--sha FULL_SHA --previous INSTALLED_SHA`. Skriptet kontrollerar den installerade
revisionen, byter bara provtjänstens versionskatalog och återgår till den gamla
vid fel. Proxy, Cloud och ordinarie Server ändras inte. Provsessionerna nollställs.
