# Spårvisning för TMBox-appen (TMBox iOS #46)

Stationens spårvisning bestämmer vilka spår appens **Spår**-vy visar och i
vilken ordning. Den sparas på träffservern **per träff och station** och
delas av alla appar som är tilldelade samma station. Den överlever omstart
av både app och server.

Spårvisningen är bara presentation. Den ändrar inte Clouds spårkatalog,
`sort_order`, standardspårens prioritet, faktiska spårplaceringar, boxarnas
spårval eller några tåg. Den skickas inte till Cloud.

## Grund och ny publicering

- Grunden är den aktiva publiceringens spårkatalog för stationen, i samma
  ordning som tidigare (`tracks_for_station`: stationens egna spår, sedan
  driftplatsens, efter `sort_order`).
- En anpassning är en ordning av spår-**id** och en mängd dolda id.
- Ny publicering för **samma träff** (`meet_id`): valen behålls för
  kvarvarande spår, nya spår läggs **synliga sist** och borttagna spår
  försvinner. En **annan träff** har egna val.
- **Återställ** tar bort anpassningen. Då gäller grundordningen och alla spår
  visas igen.

## Behörighet

Endast en TMBox-identitet (`kind` = TMBox, även iPhone-appen) med giltig
bearer-token. Stationen tas alltid från boxens **aktuella tilldelning**,
aldrig från anropet, och kontrolleras igen vid varje skrivning. En box kan
inte läsa eller ändra en annan stations val. Webbadministratörer har ingen
egen station här och får `403`. Inget administratörsgränssnitt ingår.

## Läsa: `GET /v1/tmbox/terminal/track-display`

```json
{
  "supported": true,
  "meet_id": "meet-a",
  "publication_id": "publication-2026-08-11-a",
  "station": {"id": "station-a", "code": "CDA", "name": "Charlottendahl"},
  "revision": 1,
  "customized": true,
  "tracks": [
    {"id": "track-station-a-2", "label": "2", "visible": true, "operating_point": null},
    {"id": "track-station-a-1", "label": "1", "visible": false, "operating_point": null}
  ]
}
```

- `tracks` innehåller **alla** spår i visningsordning, även dolda
  (`visible: false`). Det är listan inställningsvyn visar.
- `operating_point` är `null` för stationens egna spår, annars
  `{"id", "code", "name"}`. Använd den för att skilja lika spårnamn åt
  (samma beteckning kan finnas på stationen och på driftplatsen).
- `revision` är ett heltal per station som räknar upp vid varje sparning och
  återställning. Skicka tillbaka det du läste.
- Ingen station tilldelad: `station: null`, `tracks: []`.
  Ingen aktiv EU-träff (eller US-träff): `supported: false`.

## Spara: `POST /v1/tmbox/terminal/track-display`

```json
{
  "meet_id": "meet-a",
  "publication_id": "publication-2026-08-11-a",
  "station_id": "station-a",
  "revision": 1,
  "tracks": [
    {"id": "track-station-a-1", "visible": true},
    {"id": "track-station-a-2", "visible": false}
  ]
}
```

- `tracks` är hela den nya listan i önskad ordning: **varje aktuellt spår
  exakt en gång**, med `id` och `visible` (bool). Andra fält avvisas.
- Svar `200` har samma form som `GET`, med ny `revision`.

## Återställ: `POST /v1/tmbox/terminal/track-display/reset`

Samma kropp utan `tracks`. Svar `200` med `customized: false` och alla spår
synliga i grundordning.

## Fel

Alla fel har `{"error": "<kod>", "message": "<svensk text>"}`. Vid `409`
finns dessutom `current` med serverns aktuella läge i samma form som `GET`.

| Status | `error` | Betydelse och vad appen gör |
| --- | --- | --- |
| 409 | `revision_conflict` | Någon annan har sparat sedan du läste. Visa fel, läs om; skriv inte över automatiskt. |
| 409 | `stale_publication` | Ny publicering eller annan träff sedan du läste. Läs om. |
| 409 | `station_changed` | Boxen har fått en annan station. Läs om. |
| 409 | `tracks_changed` | Stationens spårkatalog har ändrats (spår saknas eller okänt id). Läs om. |
| 409 | `station_not_assigned` | Boxen har ingen station. |
| 409 | `runtime_missing` | Ingen aktiv EU-träff. |
| 400 | `invalid_track_display` | Fel form: saknad/felaktig `revision`, dubblett, fel fält. |
| 401 | | Saknad eller ogiltig token. |
| 403 | `box_required` | Inte en TMBox-identitet. |

Appen ska aldrig påstå att något sparats förrän servern svarat `200`, och
aldrig automatiskt försöka igen mot en annan träff eller station.

## Tidtabellen: `GET /v1/tmbox/terminal/timetable`

Oförändrat för appar som inte känner till spårvisningen, med två tillägg:

- `tracks` (befintligt fält) följer nu stationens spårvisning: **synliga**
  spår i visningsordning, som etiketter. Utan anpassning är listan exakt som
  förut. Valet gör att även äldre appversioner på stationen visar samma
  ordning. Ett dolt spår som ett tåg står på visas i äldre appar sist, som
  tidigare för spår som bara finns i tidtabellsraderna.
- `track_display` (nytt): `{"revision", "customized", "tracks"}` med samma
  innehåll som `GET /v1/tmbox/terminal/track-display`. Andra appar på
  stationen får en ändring vid nästa ordinarie hämtning av tidtabellen.
- Varje rad har nya fält `track_id` och `actual_track_id` (spårets id eller
  `null`) bredvid etiketterna `track` och `actual_track`.

Rekommendation för appen:

1. Finns `track_display`: bygg Spår-vyn från `track_display.tracks` där
   `visible` är `true`, i den ordningen, även tomma spår. Placera tåg på spår
   med `actual_track_id ?? track_id`. Dolda spår visas inte i Spår-vyn, men
   tåg, tidtabellsrader och klareringskort filtreras **inte**. Tåg utan spår
   (`null`) visas som tidigare i **Utan spår** sist. Är alla spår dolda: visa
   en tomvy med länk till Inställningar → Spår.
2. Saknas `track_display` (äldre server): använd `tracks` som i dag och visa
   att servern behöver uppdateras för gemensamma visningsval. Ingen lokal
   reserv på telefonen.

## Var det ligger

- Logik: `src/tmbox_gateway/track_display.py`.
- Lagring: `runtime_settings`, nyckel `track_display:<meet_id>`, ett objekt
  per station (`revision`, `order`, `hidden`). Rör inte `config_version`.
- API: `TrainMeetHTTPApplication.tmbox_track_display` och
  `save_tmbox_track_display` i `http_server.py`.
- Tester: `tests/test_track_display.py`.
