# Till Cloud: exakt integrationsbeställning från Server

Datum: 2026-09-27. Detta dokument kan delas i sin helhet med Cloud-arbetet.
Serverändringarna finns lokalt i `codex/server-cloud-contract`; detta är inte
ett besked om att de redan är driftsatta.

## Beslut att bevara

- Cloud äger planering, utkast, kontroll och oföränderliga publiceringar.
  Server äger träffens drift, klocka, simulation och klienttilldelning.
- Flera namngivna träffservrar får vara kopplade till samma träff, exempelvis
  en Raspberry Pi och en hostad testserver. De rapporterar oberoende.
- Casper har bekräftat att **TMBox-placering redigeras på träffservern**.
  Cloud härleder bara en standard ur banschemat.
- TMBoxens A–D är funktionstangenter i den nya profilen, inte destinationer.
  Äldre paketfält med A–D behålls för kompatibilitet, inte som operatörsval.
- Publicerade konflikter/observationer är rådgivande. Strukturfel hanteras
  fortfarande före publicering. Ingen ny konfliktmotor ska byggas i Server.

## 1. Utöka befintlig GET /config med rapportering

Behåll befintlig autentisering med `token`, svar, manifest och long-poll.
Server skickar följande två URL-kodade query-parametrar på ordinarie hämtning,
`manifest=1` och `manifest=1&after=…&wait=25`:

| Parameter | Exakt betydelse |
|---|---|
| `name` | Serverns sparade installationsnamn, annars gateway-id. Trimmat, högst 80 tecken. |
| `running_version` | **Aktiv publicerings `publication_id` som sträng**, inte ett numeriskt versionsnummer. |

Exempel; platshållarna är inte riktiga tokens eller publicerings-id:n:

```http
GET /config?token=LINK_TOKEN&manifest=1&after=PUB_9&wait=25&name=Grimslov-Pi&running_version=PUB_8
```

Exemplet är giltigt: version 9 kan ha hämtats men vänta på säker aktivering.
Servern kör fortfarande version 8. `after`, senast levererad version,
programvaruversionen och Serverns lokala `config_version` är **inte** körd version.

Cloud ska:

1. Identifiera befintlig serveranslutning via token, aldrig via namn. Spara
   rapporterat namn som `reported_name`; ett användarsatt `display_name`
   har företräde i gränssnittet. `location_note` förblir Cloud-redigerad fritext.
2. Uppdatera `last_seen_at` när giltig kontakt tas, även för manifest/long-poll
   där ingen ny publicering finns. Skapa inte en ny serverpost per kontakt.
3. Tolka `running_version` som publiceringsidentitet. Slå upp dess ordinal
   **inom anslutningens träff**, så att gränssnittet kan visa exempelvis
   ”Version 8”. Acceptera inte en version från en annan träff som aktiv.
   Ogiltig identitet ska visas som okänd/ogiltig rapport, aldrig som senaste
   publicering; låt inte en sådan rapport stoppa den vanliga configleveransen.
4. Skilja på saknad parameter och explicit tom parameter:
   - Saknad: äldre Server rapporterar inte stöd. Skriv inte över tidigare
     känd uppgift med ”senast levererad”; visa rapportstatus/tid tydligt.
   - `running_version=`: denna Server kan just nu inte intyga någon färdig
     aktiv publicering, exempelvis under en konfigurationsövergång.
     Spara okänt/tomt aktivt läge och visa `—`, inte föregående som aktuell.
   - Giltigt id: senast rapporterad aktiv publicering, med rapporttid.
   Bevara därför tomma query-värden i parsningen; tom sträng får inte bli
   samma sak som en parameter som helt saknas.
5. Behålla kopplingskodflödet. Första anslutningen använder redan
   `GET /config?code=…&server_name=…`. `server_name` ska fortsätta fungera;
   det är inte ett nytt namn på heartbeat-parametern `name`.

Svarskontraktet ändras inte. Inga nya inkommande portar, separat heartbeat-API
eller nya poll-loopar krävs. Eftersom token redan ingår i detta befintliga API
ska query-tokens fortsatt maskeras i loggar.

**Viktig begränsning:** pausad automatisk Cloud-synk innebär fortfarande att
Server inte gör automatiska kontakter. Lokal trafik kan fortsätta trots gammal
`last_seen_at`. Visa helst ”Ingen aktuell kontakt” och senaste tid, inte ett
säkert påstående att träffservern har stannat. Manuell kontroll rapporterar.
Fristående heartbeat under synkpaus ingår inte i denna leverans.

## 2. Frys findings i varje publicerat driftpaket

Lägg valfria `findings` på paketets toppnivå, bredvid `publication_id`,
`meet`, `stations` osv. Rekommenderat wire-format är en lista:

```json
{
  "publication_id": "PUB_8",
  "findings": [
    {
      "key": "A:stretch-1:93:94:mon",
      "rule": "A",
      "level": "conflict",
      "stretch_id": "stretch-1",
      "train_a": "93",
      "train_b": "94",
      "weekday": "mon",
      "start_min": 735,
      "end_min": 742,
      "source_rows": ["movement-93-cda", "movement-94-va"],
      "message": "Två tåg använder samma enkelspår samtidigt."
    }
  ]
}
```

Detta är ett förkortat schemaexempel, inte ett komplett importbart driftpaket.
Behåll fyndens verkliga regelkoder, identiteter och källrads-id:n; hitta inte på
nya identiteter vid export. `level` är `conflict` eller `observation`.
Regelberoende fält som station/spår/tåg/tidsintervall kan följa med.
Interaktiva utkastfrågor hör inte till den publicerade konfliktlistan.

- Kopiera fynden från det utkast som faktiskt publiceras, i samma atomära
  publiceringsförlopp. Senare utkaständringar får inte ändra gamla paket.
- Beräkna befintlig paketchecksumma **efter** att fynden lagts till.
- `findings: []` betyder att den publiceringen har en tom fyndlista.
  Saknat fält betyder att uppgiften inte levererats, inte att kontrollen godkänts.
- Server visar fynden för **aktiv** publicering. Ett väntande pakets fynd
  får inte ersätta den aktiva listan. Meddelanden renderas som text, inte HTML.
- Gamla paket utan fältet och gamla servrar ska fortsatt fungera. Server
  tolererar även objekt med listorna `conflicts`/`observations`, men använd
  listformatet ovan för det gemensamma kontraktet.

Server behöver inte anropa utkastets `/api/meets/{id}/findings`.

## 3. Exportera härledd TMBox-standard, behåll kompatibla paket

Ta bort manuell TMBox-placeringsredigering från Cloud. Behåll intern
`station_display_placement` om den används, men fyll den automatiskt enligt
SPEC A8: schemats vänster/höger och deterministisk vinkelordning.

**Den nya interna modellen är inte automatiskt ett nytt wire-format.**
Server läser idag standarden från paketets befintliga `panels[].slots` och
`panels[].slot_layout`. Exportera den härledda standarden där. Exempel:

```json
{
  "panels": [{
    "id": "panel-cda",
    "station_id": "cda",
    "name": "Charlottendal",
    "slot_layout": "columns",
    "slots": {"A": "cda-mun", "B": null, "C": "cda-va", "D": null}
  }]
}
```

| `slot_layout` | Vänster | Höger |
|---|---|---|
| `columns` | A rad 1, B rad 2 | C rad 1, D rad 2 |
| `rows` (äldre standard) | A rad 1, C rad 2 | B rad 1, D rad 2 |

Skicka `slot_layout` explicit; byt inte betydelse tyst i gamla publiceringar.
Om en stations sträcka har samma sida i sina panelreferenser använder Server
den. Saknas entydig placering används kompatibilitetsfallback: station A:s
ände höger, station B:s ände vänster. Undvik motstridiga panelreferenser.
Paketets `connections` är fortfarande hela banan, inte bara synliga panelslots.

Behåll stabila id:n för träff, stationer och sträckor över publiceringar.
Server lagrar lokala val per träff/station/sträcka och kontrollerar även grannens
id. Lokala val ligger utanför originalpaket/checksumma och skickas inte till Cloud.
”Följ Cloud” tar bort den lokala överstyrningen och använder senaste standarden.

En gammal panel har högst två platser per sida. Droppa aldrig sträckor för att
passa den. Serverns moderna 16×2-vy kan sidbläddra; en äldre panel behåller sin
publicerade layout om en lokal överstyrning annars skulle skapa osynliga rader.
Om en ny härledd standard inte kan representeras av befintliga panelfält måste
vi avtala ett separat versionssatt format innan det används. Skicka inte ett
nytt fält och anta att redan installerade servrar förstår det.

## Ansvarsgräns och acceptanstester

Server implementerar själv admin-API:erna `/v1/cloud/presentation` och
`/v1/cloud/display-placement`, effektiv klientkonfiguration och simulatorn.
Cloud ska varken anropa dessa eller exponera motsvarande driftredigering.
Provbänken finns på Server under `/tmbox-lab/`, inte i Cloud.

Verifiera tillsammans innan produktionssättning:

1. Två servrar för samma träff har olika namn och kan rapportera olika aktiva
   publiceringar. Cloud-override av namn överlever heartbeat.
2. PUB_9 levereras men väntar; Server rapporterar PUB_8 tills säker aktivering.
   Testa både manifest, long-poll och vanlig paketleverans.
3. Äldre Server utan parametrar, explicit tom version och id från annan träff
   hanteras utan att felaktigt visa ”senaste versionen körs”.
4. Ett publicerat fynd förblir oförändrat efter utkastredigering; checksumma
   verifieras. Paket utan fynd respektive tom lista fungerar.
5. Cloud-standard vänster/höger syns i Server och TMBox. Lokalt val överlever
   ny publicering/omstart. ”Följ Cloud” visar den uppdaterade standarden.
6. Pågående trafik, stationstilldelningar och oskickad TMBox-inmatning påverkas
   inte av enbart lokal presentationsändring.

Leveransordning: inför bakåtkompatibelt Cloud-stöd och Server-stöd, verifiera
mot en testträff, därefter driftsätt. Denna beställning kräver inte en ny
firmwareversion för enheter som redan kör profilen `server-16x2`.
