# Versionsnummer i TrainMeet

Varje repo har ett **eget** versionsnummer och höjer det oberoende av de
andra. Server, Cloud, TKL, iPhone och TMBox driftsätts var för sig, så att
låsa numren till varandra skulle betyda att en komponent fick en ny version
för en rättning som bara rörde en annan.

Kompatibiliteten dem emellan bärs av två helt andra tal, som redan finns och
som **inte** följer produktversionen:

| Tal | Var | Vad det lovar |
|---|---|---|
| `protocol_version` | TMBox-protokollet över MQTT | vad en box och en server kommer överens om |
| `schema_version` | driftpaketet från Cloud | vad Cloud publicerar och servern kan läsa |

## Var versionen bor

`VERSION` i repotets rot. En rad, `större.funktion.rättning`. Det är den enda
auktoritativa källan.

Allt annat är **härlett** och skrivs därifrån av `scripts/version.py sync`:
`pyproject.toml`, `package.json`, `project.yml`, `project.pbxproj`,
`FIRMWARE_VERSION` i skissen. Testsviten faller om någon av dem säger något
annat, vilket är hela poängen — tre påståenden hade hunnit glida isär innan
den här filen fanns.

Git-committen är **bygginformation**, inte en version. Den svarar på «exakt
vilken kod är detta», vilket ett versionsnummer medvetet inte gör:

```text
Version 1.0.0 · build 4bd9c9a
```

## Hur numret höjs

Automatiskt, vid varje merge till `main`. Du behöver inte göra något.

Vill du styra nivån skriver du en markör i en commits **ämnesrad** — enklast
i PR-titeln, eftersom en squash-merge gör den till ämnesrad. Markörer läses
aldrig ur brödtexten, just för att en text *om* en markör inte ska vara en
markör:

| Markör | Från `1.4.2` | När |
|---|---|---|
| `[major]` | `2.0.0` | brytande ändring — någon som uppgraderar måste göra något |
| `[minor]` | `1.5.0` | ny funktion, bakåtkompatibel |
| `[patch]` | `1.4.3` | rättning *(samma som förvalet, så sällan nödvändig)* |
| `[skip version]` | `1.4.2` | ingen höjning alls |

### Ordningen mellan reglerna

Starkast först. Den första som passar bestämmer:

1. **Alla commits i mergen är robotens egna** — då är det en loop, och den
   stoppas. Kontrollen görs på *avsändaradress*
   (`version@trainmeet.app`), inte på texten. Det var inte första försöket:
   en vakt som letade efter `[skip version]` i meddelandet utlöstes av en
   commit vars brödtext *förklarade* markören. Prosa kan inte utge sig för
   att vara en avsändare.
2. **`[skip version]`** i en commits **ämnesrad**.
3. **`VERSION` ändrades i mergen** — någon har redan skrivit ett exakt
   nummer, och det ska inte höjas förbi. Det här är varför den merge som
   införde `1.0.0` inte blev `1.0.1`.
4. **`[major]` / `[minor]` / `[patch]`** i en **ämnesrad** — starkaste
   markören i hela mergen vinner. En brytande ändring bland flera commits
   gör hela släppet brytande.
5. **Bara `docs/`, `README.md`, `.github/`, `LICENSE`, `.gitignore` ändrades**
   — ingenting skeppas, alltså ingen version. En stavfelsrättning ska inte
   ge ett släpp.
6. **Annars `patch`** — en merge till main är en driftsättbar ändring här.

### Vad roboten gör

Skriver `VERSION`, synkar de härledda filerna, committar
`Version X.Y.Z [skip version]` och sätter taggen `vX.Y.Z`.

Taggen sätts **när numret saknar tagg**, inte bara när roboten höjde det. Ett
nummer någon satt för hand är ett medvetet släppbeslut och förtjänar en tagg
mer än en automatisk patch gör — förut fick det ingen alls.

## Om det inte fungerar

**Push nekad.** Nästan alltid grenskydd: har `main` obligatorisk PR eller
obligatoriska statuskontroller gäller de robotens push också. Lägg till
`github-actions[bot]` under *Allow specified actors to bypass required pull
requests*. Roboten misslyckas högljutt i stället för tyst — en version som
tappas bort i tysthet lämnar skeppad kod med fel nummer.

**Två merges samtidigt.** Arbetsflödet kör en i taget
(`concurrency`), och en push som ändå förlorar kapplöpningen hämtar,
lägger om och försöker igen tre gånger.

**Fel nummer.** Sätt rätt nummer i `VERSION` för hand och merga. Regel 2
ovan gör att roboten låter det stå.

## Kommandon

```bash
python3 scripts/version.py current   # nuvarande version
python3 scripts/version.py check     # faller om en härledd fil glidit
python3 scripts/version.py sync      # skriv om de härledda filerna
python3 scripts/version.py bump minor
```

`sync` går att köra hur många gånger som helst; `bump` gör det inte, eftersom
den också räknar upp iPhone-byggnumret — TestFlight vägrar ett byggnummer den
redan sett.
