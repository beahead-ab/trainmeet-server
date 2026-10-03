# Obemannade stationer i vanlig drift

Gäller från den version som stänger issue #115. Det här är vanlig drift, inte
trafikspelssimulatorn: samma trafikdatabas, inga påhittade förseningar.

## Vad som händer

När träffklockan går sköts varje station utan TMBox eller TKL i arbete
**automatiskt** med simulatorns regler. Tider räknas i spelminuter.

| Händelse | Villkor |
|---|---|
| Begär avgång | Tidigast 2 spelminuter före avgång. Tåget står på stationen: tågloppets första avgång, eller föregående del har ankommit. |
| Ge klart | Det planerade mottagningsspåret är ledigt. |
| Avgång | Klartecken eller direktklarering finns och avgångstiden (efter planerat uppehåll) har nåtts. |
| Ankomst | Faktisk avgång plus tidtabellens gångtid har passerat. |
| Nekad begäran | Upprepas inte. En människa får ta ställning. |

- Tåg vars första avgång ligger före den tid då automatiken först körde under
  trafikdagen skickas inte. De är historia, inte en kö som ska skickas på en gång.
- Automatiken arbetar genom samma stationskommandon som en box, med aktören
  `automatik`. Stationstjänsten nekar den på en bemannad station.
- Avgångs- och ankomsttider sparas i `automatic_events` i samma transaktion
  som trafikändringen, vem som än anmälde dem.

## Bemanning

- Den första TMBox eller TKL som arbetar med stationen gör den **manuell**.
  En TMBox räknas via sin tilldelning, en TKL via stationen den läser eller
  manövrerar. Inget trafikpass behövs.
- Tappar operatören kontakten i 45 verkliga sekunder står stationen som
  **Kontakt saknas – väntar**. Automatiken tar inte över vid ett nätavbrott.
- Under **Inställningar → Obemannade stationer** kan admin lämna en station till
  automatiken, eller ge tillbaka den till en ansluten box eller TKL. En box vars
  station lämnats till automatiken tar den inte tillbaka av sig själv.
- Bemanningen sparas per publicering och trafikdag och överlever en omstart.
  Efter omstart står en bemannad station som Kontakt saknas tills boxen hörs av.

## Av och på

Automatiken är på som standard och stängs av under **Inställningar → Obemannade
stationer**. Den gör ingenting medan klockan står still eller medan en simulering
körs.

## Ta emot ett tåg med tvång

Glömmer en station att klarera och skickar bara tåget, skriver mottagaren
tågnumret och svarar `#` på **FLYTTA 93 HIT?**. Se TMBox-flödet "Flytta hit ett
tåg som ingen skickat".

En TKL får grannstationernas läge i `/v1/tkl/context` som `station_modes`
(`automatic`, `manual` eller `disconnected` per granne; tomt när automatiken är
avstängd), så att ställverket kan visa vem som svarar i andra änden.

Admin-API: `GET /v1/automatic-stations`, `POST /v1/automatic-stations` med
`action` = `enable` (`enabled`), `automatic` (`station_id`, `confirmed: true`) eller
`manual` (`station_id`, `device_id`, `confirmed: true`).
