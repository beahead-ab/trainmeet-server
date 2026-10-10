# Cloud förbereder, Server kör

En TrainMeet Server representerar exakt en vald träff, även när klockan är
stoppad eller en US-körning har avslutats. EU/US är en egenskap hos den valda
träffen, inte en växel för operatören. EU- och US-trafikmotorerna behålls
separerade men delar en beständig träffspärr.

## Config och drift är olika saker

US stöder både det äldre runtime v1-paketet och **runtime v2 med fristående
mileposts, platser och gränser**. V2 använder uttryckliga spårpunkter och hela
segment för körbesked, inte numeriska MP-intervall. Läs [US-kontraktet och
driftbegränsningarna](TRAINMEET-US.md#fristående-mileposts-i-runtime-v2).
Uppdatera Server innan Cloud publicerar v2. Ingen träff konverteras automatiskt.

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

## E-post via Cloud: det enda som går uppåt

Synken går bara Cloud → Server. Servern skickar aldrig upp konfiguration, träffdata
eller historik. Ett undantag finns, och det är smalt: en kopplad server kan be
Cloud skicka **en inbjudan** eller **en kod för nytt lösenord** till en av
serverns användare (`POST /api/server-mail`, i `cloud_mail.py`).

- Brevet bär bara mottagare, kod, serverns adress och språk (`MAIL_FIELDS`).
  Kontot är mottagarens adress, så inget användarnamn följer med. Cloud har
  avsändaren, mallarna och träffens och serverns namn.
- Nyckeln är serverns kopplingsnyckel och står i `Authorization`.
- Utan koppling eller internet fungerar allt som förut: koden visas för ägaren
  och lämnas över på plats, och `tmbox_gateway.recover` finns på serverdatorn.
- `test_product_boundaries` vaktar både att synken bara läser och att
  `cloud_mail.py` är den enda modul som skriver till Cloud.

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

## Lokala ändringar i tidtabellen

Cloud är källan, men mitt under träffen kan admin behöva flytta en tid, byta
spår, ta bort ett tåg eller göra en sträcka dubbelspårig utan att vänta på
Cloud. Sådana ändringar ligger som ett lager ovanpå Clouds publicering: samma
publicerings-id och samma rad-id:n, så TKL-läget följer med raden, boxarna ser
den nya tiden direkt och Cloud ser ingenting. Clouds version står orörd, och
"Återgå till Cloud-versionen" tar bort lagret efter en säkerhetskopia.

Bara det Data-vyn får ändra får ändras: tågnummer, dagar, spår, ankomst,
avgång, från, till, ej uppehåll och anmärkning på en rad, borttagning av en rad,
och spårtypen på en sträcka. Stationer, signaturer, spårkatalog, nya tåg och nya
sträckor ändras i Cloud. Tjänster och rutter byggs om med Clouds egen kod
(`timetable_core`), så det effektiva paketet är exakt det Cloud skulle ha byggt.

Spärrarna gäller bara det som ändras, inte hela banan: ett tåg som är ute på
linjen, har ett öppet körtillstånd eller väntar på kvittens av ett linjebesked
kan inte ändras; ett tåg med registrerade driftuppgifter kan inte tas bort; en
sträcka byter spårtyp bara när den är fri och ingen TMBox vid den är mitt i en
inmatning. Varje sparning är en ny
revision med samma övergångsmärke som en aktivering, och en sida som ligger
efter får inte spara. API:et är `GET /v1/meet-data`, `POST /v1/meet-data` och
`POST /v1/meet-data/discard`, bara för admin.

När Cloud publicerar en ny version medan lokala ändringar finns aktiveras den
inte automatiskt. Den hämtas och kontrolleras, och Cloud-statusen blir
`local_changes`. `GET /v1/runtime/pending` visar vad Cloud ändrar mot det som
gäller nu och vilka lokala ändringar som försvinner. Admin väljer med
`POST /v1/cloud/local-decision {decision, publication_id, expected_revision}`:

- **Ta Cloud-versionen** (`take`): en säkerhetskopia tas, sedan samma väg som en
  automatisk aktivering med samma spärrar. Hindrar trafiken väntar den, valet
  står kvar, och de lokala ändringarna gäller tills banan är fri.
- **Behåll mina ändringar** (`keep`): servern hämtar inte den versionen igen.
  Frågan kommer tillbaka först när Cloud publicerar en nyare version.

Kastas de lokala ändringarna medan en version väntar tas den som vanligt så fort
banan är fri. Utan lokala ändringar fungerar allt som förut.

### Sidan Tidtabell (`/tidtabell`)

Admin ändrar på sidan Tidtabell, som nås med tabellikonen i sidhuvudet och
från Inställningar → Träff och Cloud (på en telefon bara därifrån). Sidan är
Clouds egen Data-vy, samma kod som i Cloud: `web/data-workspace.js` byggs i
Cloud med `vite.embed.config.ts` och kopieras hit med Clouds
`scripts/vendor-data-workspace.mjs`, som också skriver `data-workspace.json`
med Clouds version, sha256 och storlek. Ett prov låser filen mot den. Vyn ritas
i en shadow root med Clouds stilar som konstruerade stilmallar, så serverns CSP
(`style-src 'self'`, ingen `eval`) räcker och sidornas stilar inte påverkar
varandra. Det som bara finns i Cloud är avstängt: import, källfiler,
stationsredigering och Clouds `/api/`, som inte finns på servern. Filen är en
halv megabyte och hämtas först när sidan öppnas.

Celler som är ändrade lokalt är markerade (`local_changes` i
`GET /v1/meet-data`). Raden överst säger hur många lokala ändringar som finns,
med en lista och **Återgå till Cloud-versionen**. Sidan sparar alltid mot den
revision vyn utgick från: har någon annan sparat under tiden svarar servern
409, och vyn ber admin trycka Avbryt och göra om ändringen i den nya
tidtabellen, så att ingen ändring skrivs över. Ny data ges till vyn bara när
inget är osparat.

När en ny Cloud-version väntar på admins val meddelar servern sidorna direkt
(`/v1/events`, ämnet `runtime`). Tidtabell visar då rutan **Ny version finns i
Cloud** med vad Cloud-versionen ändrar och vilka lokala ändringar som då
försvinner, och knapparna **Behåll mina ändringar** och **Ta Cloud-versionen**.
Under Inställningar → Träff och Cloud står en länk **Välj under Tidtabell**.

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

Att en box är ansluten är inte bevis på att gamla kommandon säkert kan
återanvändas efter ett träffbyte eller byte av trafikdag. Sedan Server 2.0.0
ansluter bara firmware 0.7 eller senare. Den talar 16×2-profilen, och servern
ritar varje bild för den träff och trafikdag som gäller just då. Äldre
firmware, med v1- eller v2-protokollet över MQTT, får inget svar alls.

## Programvara är separat

Configuppdateringar installerar inte serverprogrammet och startar inte om
maskinen. Programuppdatering, backup och eventuell återställning är separata
administrationsåtgärder. Ingen produktionsdriftsättning följer automatiskt av
att denna kod byggs eller dess tester körs.
