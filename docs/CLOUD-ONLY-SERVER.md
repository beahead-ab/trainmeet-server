# Cloud förbereder, Server kör

En TrainMeet Server representerar exakt en vald träff, även när klockan är
stoppad eller en US-körning har avslutats. EU/US är en egenskap hos den valda
träffen, inte en växel för operatören. EU- och US-trafikmotorerna behålls
separerade men delar en beständig träffspärr.

## Config och drift är olika saker

Träffens bana, stationer/territorier, tidtabell och övriga planering redigeras
och publiceras i Cloud. Serverns byggläge och lokala import-/redigeringsvägar
är borttagna. Äldre lokala data och historik raderas inte av denna förändring.

Servern behåller driftåtgärderna: klockstyrning, tågrapporter, klareringar,
track warrants, extra tåg, operatörstilldelningar och TMBox-kopplingar.
TMBoxarnas stationstilldelning är en lokal driftinställning, inte en ändring
av banan. Serverkonton, enhetsidentiteter och nätverksinställningar hör till
servern och skapas inte om för varje träff.

## Anslutning och uppdateringar

1. Administratören anger den publicerade träffens Cloud-kod i serverns
   inställningar. Servern sparar kopplingen och hämtar hela träffens config.
2. Paketet valideras innan den fungerande kopplingen ersätts. Servern använder
   därefter den lokala kopian och behöver inte Cloud för trafiken.
3. När Cloud annonserar stöd i versionsbeskedet håller servern en utgående,
   autentiserad **long-poll** öppen i högst 25 sekunder. Publicering väcker
   kontrollen; efter svaret eller tidsgränsen kontrolleras aktuell config igen.
   Äldre Cloud, kapacitetsbegränsning eller anslutningsfel ger vanlig kontroll
   var 15:e sekund som reserv. Detta är inte en WebSocket eller en inkommande
   anslutning till träffens lokalnät. Endast publicerade versioner används;
   Cloud-utkast påverkar inte driften.
4. **Sök configuppdatering** använder samma kontroll- och aktiveringskedja.
   Manuell kontroll kringgår inte trafikspärrarna.

Versionskontrollen jämför publikations-ID och paketets kontrollsumma. Ett
felaktigt paket eller en hämtning från en koppling som under tiden ändrats får
inte ersätta den aktiva träffen. En annan träff får inte smygas in genom
automatisk synkronisering.

## Säker aktivering

Hämtning i sig skriver inte om träffklockan eller trafikläget. Ny config lagras
först och blir aktiv automatiskt när serverns kontroller tillåter det. Om trafik,
operatörsarbete eller befintliga referenser hindrar ändringen visas ett vänteläge
med orsak. En pausad klocka betyder inte att banan är fri.

I EU kontrolleras bland annat sträckornas trafikläge, pågående panelarbete och
befintliga driftposter. I US kontrolleras bland annat öppna track warrants,
rapporterade positioner och ändringar av tåg som redan har driftuppgifter.
Sessionens identitet, giltiga tilldelningar, utfärdade tillstånd och klocka ska
bevaras vid en tillåten uppdatering av samma träff. Vissa infrastrukturella eller
identitetsändringar kan kräva att körningen först avslutas; de tvingas inte igenom.

Ett beständigt övergångsmärke spärrar trafik medan lagring och motor uppdateras.
Detta är inte en transaktion över alla databaser. Ett avbrott mitt i övergången
ska därför ge spärrad drift efter omstart, inte låtsas att blandad config är
säker. Sådan återställning kräver administrativ kontroll och vid behov backup.

## Arbetsytor och navigation

Efter inloggning väljer användaren arbetsyta utifrån behörighet och träff:
Drift och administration, TKL, Dispatcher eller Conductor. Byte av arbetsyta
byter endast gränssnitt. Hem återgår till den aktuella arbetsytan.
Hamburgermenyn samlar inställningar, skärmar, byte av arbetsyta och utloggning.
Administrativa formulär öppnas i modaler; vanliga trafikkommandon ligger direkt
i arbetsytan.

**Byt träff** är ett separat bekräftat administratörsflöde. Pågående trafik
kontrolleras, gamla träffbundna tilldelningar får inte följa med automatiskt,
och serverns konto/nätverk behålls. Det är inte en fabriksåterställning.

## Klienter och kompatibilitetsgräns

TKL-kommandon från detta bygge skickar träffgenerationen från det läge som
operatören faktiskt såg. Ett avvisat gammalt kommando läser om läget men skickas
inte automatiskt igen. US använder dessutom körnings-ID, revision och
idempotenta kommando-ID:n.

Äldre TMBox v2-firmware saknar tillräcklig träffomfattning i sina kommandon.
Efter ett uttryckligt träffbyte eller byte av trafikdag måste sådana oskopade
skrivningar därför spärras tills klienten stöder det nya kontraktet. Att boxen är ansluten är inte bevis på
att gamla kommandon säkert kan återanvändas. Den här serverändringen innehåller
ingen verifiering på fysisk ESP8266/ESP32 eller en pågående riktig träff.

Den tillhörande ESP32-anpassningen i `trainmeet-tmbox` låter assignment,
config och snapshot bekräfta samma generation innan knapparna kan skicka
skrivkommandon. Den rensar gamla val/svar och återförsöker inte gamla beslut
med en ny generationsmärkning. ESP32-S3-bygget och värddatorns tester är
verifierade; firmware behöver levereras tillsammans med denna Server-ändring
innan äldre ESP32-boxar ska användas efter ett träff- eller trafikdagsbyte.
ESP8266:s v1-protokoll använder sin befintliga sessions- och revisionskontroll.

## Programvara är separat

Configuppdateringar installerar inte serverprogrammet och startar inte om
maskinen. Programuppdatering, backup och eventuell återställning är separata
administrationsåtgärder. Ingen produktionsdriftsättning följer automatiskt av
att denna kod byggs eller dess tester körs.
