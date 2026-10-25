# Pi:ns privata Wi-Fi-profiler

Ärende: [#202](https://github.com/beahead-ab/trainmeet-server/issues/202).

## För administratören

Inställningar → Namn och nätverk → Raspberry Pi · Nätverk visar Pi:ns
sparade nätverk. Detta är **inte** Träffens Wi-Fi, som visas för deltagarna.
De privata profilerna och deras lösenord skickas inte till Cloud, TMBoxar,
deltagarvyn, QR-koder eller TrainMeets vanliga säkerhetskopior.

- Lägg till SSID, WPA2 Personal / WPA3 Personal / öppet nätverk, lösenord
  och vid behov dolt nätverk. Sök nätverk kan fylla SSID från närliggande nät.
- Flytta upp/ned för önskad prioritet. NetworkManager väljer det högst
  prioriterade tillgängliga nätverket när en anslutning behövs vid start.
  Det är inte en garanti om exakt söktid per nätverk: radio, signal,
  autentisering och NetworkManagers återförsök påverkar förloppet.
  Befintliga profiler med automatisk anslutning avstängd markeras; ordningen
  slår inte på dem. Spara en sådan profil för att aktivera den vid nästa start.
- **Spara** ändrar inte den aktiva anslutningen. Ändrade inaktiva profiler
  blockeras från automatisk aktivering i den aktuella OS-sessionen, tills de
  aktiveras manuellt eller Pi:n startas om. Vid nästa start används den sparade
  prioriteringen. Ett bättre nätverk som dyker upp bryter inte en fungerande
  anslutning. Nätverket behöver inte ha internet.
- **Anslut nu** visar först en varning. Webbläsaren och anslutna boxar kan
  tappa kontakten, och Pi:n kan få ny IP-adress. Återanslut och välj **Behåll
  anslutningen** inom 90 sekunder. Annars återställer NetworkManager tidigare
  nätverksläge, även om TrainMeet/hjälptjänsten kraschar. **Återställ nu**
  gör samma sak direkt. Bekräftelse kräver anslutet Wi-Fi och en IP-adress,
  inte tillgång till internet.
- Den aktiva Wi-Fi-profilen kan inte ändras eller tas bort. Byt först till
  ett annat Wi-Fi. Detta bevarar en återställningsbar profil och dess lösenord.
- Vid redigering betyder tomt lösenordsfält ”behåll sparat lösenord”, men
  bara om SSID och säkerhetstyp är oförändrade. Lösenord hämtas aldrig tillbaka
  till webbläsaren. Borttagning kräver bekräftelse.

En riktig administratörsinloggning krävs, även under första installationen
och på Pi:ns egen skärm. Anslutningskoder och TMBox-behörighet räcker inte.
Ändringar görs antingen i webbläsaren **på Pi:n**, via localhost, eller via
en konfigurerad **HTTPS-proxy på samma Pi**. Okrypterad HTTP från andra datorer
är skrivskyddad, så lösenord inte skickas i klartext över träffens nätverk.
För en Pi utan skärm behövs alltså lokal HTTPS eller inledande nätverkssetup
med Raspberry Pi Imager. Funktionen skapar inte en hotspot eller certifikat.

## Installation och arkitektur

Förutsättning: Raspberry Pi OS Bookworm eller senare, med en redan aktiv
NetworkManager och ett hanterat Wi-Fi-kort. WPA3 kräver också stöd i adapter,
drivrutin och accesspunkt. Enterprise/802.1X, WEP, hotspot-profiler,
användarspecifika/agentlagrade lösenord och SSID som inte kan representeras
i UTF-8 är skrivskyddade. Ethernet, statisk IP/DNS och annan nätverksstyrning
är inte nya inställningar i detta gränssnitt.

Det vanliga installations-/uppdateringsskriptet installerar `python3-dbus`
och `trainmeet-network.service` **endast** på en identifierad Raspberry Pi
med redan aktiv NetworkManager. Det installerar eller byter inte
nätverkshanterare och ändrar inte befintliga nätverksprofiler.
På molnserver, Mac och andra system utan hjälptjänsten visas inte kortet.

Webbservern fortsätter köra som `trainmeet-server`, utan root eller generisk
kommandokörning. En liten root-tjänst lyssnar enbart på
`/run/trainmeet-network/control.sock`, root-ägd med grupp `trainmeet-server`,
läge 0660 i en root-ägd katalog. Den kontrollerar Linux `SO_PEERCRED` och
accepterar endast webbserverns användar-ID. Åtgärder är strikt begränsade
till Wi-Fi via NetworkManagers system-D-Bus; inga skalsträngar används.

NetworkManager sparar profiler/lösenord i operativsystemet. HTTP-processen
skickar nya lösenord vidare i minnet och får aldrig befintliga lösenord
tillbaka. D-Bus-fel filtreras så att hemligheter inte hamnar i API-fel/loggar.
Prioritetsändringar bevarar profilernas hemligheter, IP/DNS och andra fält.
Vid ett skrivfel återställs tidigare prioriteringar så långt det går; listan
läses sedan om från det faktiska systemet.

API (endast explicit admin-session via cookie):

| Anrop | Innehåll |
| --- | --- |
| `GET /v1/host/wifi` | Stöd, radio/adapter, IP-adresser, profiler utan lösenord, sökresultat, provanslutning |
| `POST /v1/host/wifi/scan` | `interface` |
| `POST /v1/host/wifi/save` | Valfri `uuid`, `ssid`, `security` (`wpa2`, `wpa3`, `open`), `password`, `hidden` |
| `POST /v1/host/wifi/order` | `uuids`: exakt hela den skrivbara listan i önskad ordning |
| `POST /v1/host/wifi/delete` | `uuid` |
| `POST /v1/host/wifi/connect` | `uuid`, `interface` |
| `POST /v1/host/wifi/confirm` eller `/rollback` | Engångs-`token` från provanslutningen |

Skrivningar kräver JSON, `X-TrainMeet-Network: 1`, samma ursprung samt
localhost eller konfigurerad HTTPS-origin med lokal proxy. En godtycklig
`X-Forwarded-Proto` från LAN godtas inte. Proxy-konfigurationen ska sätta
och rensa forwarded-headerfält själv. API:t svarar med `Cache-Control: no-store`.
Ingen ny API-koppling behövs i Cloud eller TMBox-firmware/simulator.

Provanslutningar serialiseras i hjälptjänsten. NetworkManagers checkpoint
omfattar bara valt Wi-Fi-kort, inte Ethernet. Upprepning av samma anslutning
är idempotent. Återställningstimern ägs av NetworkManager, inte webbläsaren.
Efter en omstart av hjälptjänsten blockeras nya ändringar tills befintlig
checkpoint har avslutats. Ingen tjänst förstör en okänd checkpoint.
Om själva Pi:n startas om försvinner checkpointen; då gäller de sparade
profilernas startprioritet. Detta är inte en skyddsåtgärd mot strömavbrott.

## Verifiering före aktivering på riktiga Pi-enheter

Automatiska tester täcker validering, hemlighetsbevarande, behörigheter,
HTTPS/ursprung, aktiva profiler, prioritetskonflikt, timeout, hjälptjänstens
omstart, dubbeltryck, felaktig anslutning och dator-/mobildialoger. De använder
simulerad hårdvara; följande **måste även provas på en separat fysisk Pi**:

- [ ] Installera via vanliga skriptet på stödd Pi OS. Kontrollera tjänst och
      socketbehörigheter, utan ändring av befintlig anslutning.
- [ ] Spara två nätverk, ändra ordning, starta om: rätt tillgängliga nät väljs.
- [ ] Stäng högst prioriterade accesspunkten; nästa tillgängliga nät fungerar.
- [ ] Prova fel lösenord, dolt SSID, WPA2, WPA3 och nät utan internet.
- [ ] Spara/redigera/prioritera utan att aktivt Wi-Fi bryts; verifiera att
      tidigare lösenord och eventuell statisk IP/DNS behålls.
- [ ] Byt nät med `Anslut nu`: bekräfta från återansluten webbläsare.
- [ ] Upprepa utan bekräftelse: tidigare anslutning återkommer efter 90 s.
- [ ] Stoppa hjälptjänsten mitt i provet: samma återställning sker ändå.
- [ ] Ha Ethernet anslutet: det förblir opåverkat av provanslutningen.
- [ ] Ingen privat SSID/lösenordsdata i deltagarvy, Cloud, QR, backup eller logg.
- [ ] Mobil via HTTPS kan spara; mobil via vanlig LAN-HTTP kan inte spara.

Teknisk referens: [Raspberry Pi OS nätverk](https://www.raspberrypi.com/documentation/computers/configuration.html),
[NetworkManager anslutningsprioritet](https://www.networkmanager.dev/docs/api/latest/settings-connection.html),
[checkpoint-API](https://www.networkmanager.dev/docs/api/latest/gdbus-org.freedesktop.NetworkManager.html)
och [Update2](https://www.networkmanager.dev/docs/api/1.38.6/gdbus-org.freedesktop.NetworkManager.Settings.Connection.html).
