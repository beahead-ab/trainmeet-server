/* Presentation only. Published source text and authority wording are never translated. */
(() => {
  const rows = [
  {"en":"Downloading and validating the package…","sv":"Hämtar och kontrollerar paketet…","da":"Henter og kontrollerer pakken…","nb":"Henter og kontrollerer pakken…","de":"Paket wird geladen und geprüft…"},
  {"en":"Arrival","sv":"Ankomst","da":"Ankomst","nb":"Ankomst","de":"Ankunft"},
  {"en":"Departure","sv":"Avgång","da":"Afgang","nb":"Avgang","de":"Abfahrt"},
  {"en":"Pass","sv":"Passage","da":"Passage","nb":"Passering","de":"Durchfahrt"},
  {"en":"Switching","sv":"Växling","da":"Rangering","nb":"Skifting","de":"Rangieren"},
  {
    "en": "Download from Cloud",
    "sv": "Hämta från Cloud",
    "da": "Hent fra Cloud",
    "nb": "Hent fra Cloud",
    "de": "Aus Cloud laden"
  },
  {
    "en": "Saved US packages",
    "sv": "Sparade US-paket",
    "da": "Gemte US-pakker",
    "nb": "Lagrede US-pakker",
    "de": "Gespeicherte US-Pakete"
  },
  {
    "en": "Review package",
    "sv": "Granska paket",
    "da": "Gennemgå pakke",
    "nb": "Gjennomgå pakke",
    "de": "Paket prüfen"
  },
  {
    "en": "Review US package",
    "sv": "Granska US-paket",
    "da": "Gennemgå US-pakke",
    "nb": "Gjennomgå US-pakke",
    "de": "US-Paket prüfen"
  },
  {
    "en": "Download package",
    "sv": "Hämta paket",
    "da": "Hent pakke",
    "nb": "Hent pakke",
    "de": "Paket laden"
  },
  {
    "en": "Local JSON file",
    "sv": "Lokal JSON-fil",
    "da": "Lokal JSON-fil",
    "nb": "Lokal JSON-fil",
    "de": "Lokale JSON-Datei"
  },
  {
    "en": "Config URL",
    "sv": "Config-adress",
    "da": "Config-adresse",
    "nb": "Config-adresse",
    "de": "Config-URL"
  },
  {
    "en": "Six-digit session code",
    "sv": "Sexsiffrig träffkod",
    "da": "Sekscifret træfkode",
    "nb": "Sekssifret treffkode",
    "de": "Sechsstelliger Treffencode"
  },
  {
    "en": "Only published US packages are downloaded. Live operations are never changed.",
    "sv": "Endast publicerade US-paket hämtas. Pågående trafik ändras aldrig.",
    "da": "Kun publicerede US-pakker hentes. Igangværende drift ændres aldrig.",
    "nb": "Bare publiserte US-pakker hentes. Pågående drift endres aldri.",
    "de": "Es werden nur veröffentlichte US-Pakete geladen. Der laufende Betrieb bleibt unverändert."
  },
  {
    "en": "Leave the code empty to download the latest version from the saved connection.",
    "sv": "Lämna koden tom för att hämta senaste versionen via den sparade kopplingen.",
    "da": "Lad koden være tom for at hente seneste version via den gemte forbindelse.",
    "nb": "La koden stå tom for å hente siste versjon via den lagrede forbindelsen.",
    "de": "Code leer lassen, um die neueste Version über die gespeicherte Verbindung zu laden."
  },
  {
    "en": "Stored on this server. Internet is not required to start or run a downloaded session.",
    "sv": "Lagrat på denna server. Internet behövs inte för att starta eller köra en hämtad träff.",
    "da": "Gemt på denne server. Internet er ikke nødvendigt for at starte eller køre et hentet træf.",
    "nb": "Lagret på denne serveren. Internett er ikke nødvendig for å starte eller kjøre et nedlastet treff.",
    "de": "Auf diesem Server gespeichert. Für Start und Betrieb einer geladenen Sitzung ist kein Internet nötig."
  },
  {
    "en": "No downloaded US package yet.",
    "sv": "Inget US-paket har hämtats ännu.",
    "da": "Ingen US-pakke er hentet endnu.",
    "nb": "Ingen US-pakke er hentet ennå.",
    "de": "Noch kein US-Paket geladen."
  },
  {
    "en": "train runs",
    "sv": "tåglopp",
    "da": "togløb",
    "nb": "togløp",
    "de": "Zugläufe"
  },
  {
    "en": "track segments",
    "sv": "spårsegment",
    "da": "sporsegmenter",
    "nb": "sporsegmenter",
    "de": "Gleisabschnitte"
  },
  {
    "en": "territories",
    "sv": "trafikområden",
    "da": "trafikområder",
    "nb": "trafikkområder",
    "de": "Streckenbereiche"
  },
  {
    "en": "named points",
    "sv": "namngivna platser",
    "da": "navngivne steder",
    "nb": "navngitte steder",
    "de": "benannte Punkte"
  },
  {
    "en": "US clock starts paused. EU traffic and its clock stay unchanged.",
    "sv": "US-klockan startar pausad. EU-trafiken och dess klocka förblir oförändrade.",
    "da": "US-uret starter på pause. EU-driften og dens ur forbliver uændrede.",
    "nb": "US-klokken starter på pause. EU-driften og klokken forblir uendret.",
    "de": "Die US-Uhr startet pausiert. EU-Betrieb und EU-Uhr bleiben unverändert."
  },
  {
    "en": "Source instructions and dispatcher districts",
    "sv": "Källinstruktioner och dispatcherområden",
    "da": "Kildeinstruktioner og dispatcherområder",
    "nb": "Kildeinstruksjoner og dispatcherområder",
    "de": "Quellanweisungen und Dispatcher-Bezirke"
  },
  {
    "en": "Planning reference only. District permissions and Train Token handovers are not enforced in this profile.",
    "sv": "Endast planeringsunderlag. Områdesbehörigheter och Train Token-överlämningar styrs inte av denna profil.",
    "da": "Kun planlægningsgrundlag. Områderettigheder og Train Token-overdragelser håndhæves ikke i denne profil.",
    "nb": "Kun planleggingsgrunnlag. Områdetilganger og Train Token-overleveringer håndheves ikke i denne profilen.",
    "de": "Nur Planungsreferenz. Bezirksberechtigungen und Train-Token-Übergaben werden in diesem Profil nicht durchgesetzt."
  },
  {
    "en": "Finish the current session before starting another package. Downloading never changes live operations.",
    "sv": "Avsluta pågående körning innan ett annat paket startas. Hämtning ändrar aldrig pågående trafik.",
    "da": "Afslut den aktuelle kørsel før en anden pakke startes. Hentning ændrer aldrig igangværende drift.",
    "nb": "Avslutt pågående kjøring før en annen pakke startes. Nedlasting endrer aldri pågående drift.",
    "de": "Die laufende Sitzung vor dem Start eines anderen Pakets beenden. Ein Download verändert den Betrieb nicht."
  },
  {
    "en": "Save a package locally, then review it before starting.",
    "sv": "Spara paketet lokalt och granska det före start.",
    "da": "Gem pakken lokalt og gennemgå den før start.",
    "nb": "Lagre pakken lokalt og gjennomgå den før start.",
    "de": "Paket lokal speichern und vor dem Start prüfen."
  },
  {
    "en": "US clock",
    "sv": "US-klocka",
    "da": "US-ur",
    "nb": "US-klokke",
    "de": "US-Uhr"
  },
  {
    "en": "US clock running",
    "sv": "US-klockan går",
    "da": "US-uret kører",
    "nb": "US-klokken går",
    "de": "US-Uhr läuft"
  },
  {
    "en": "US clock paused",
    "sv": "US-klockan är pausad",
    "da": "US-uret er på pause",
    "nb": "US-klokken er på pause",
    "de": "US-Uhr pausiert"
  },
  {
    "en": "Only this US session is affected. Clock time never grants movement authority.",
    "sv": "Endast denna US-körning påverkas. Klockslag ger aldrig körtillstånd.",
    "da": "Kun denne US-kørsel påvirkes. Klokkeslæt giver aldrig køretilladelse.",
    "nb": "Bare denne US-kjøringen påvirkes. Klokkeslett gir aldri kjøretillatelse.",
    "de": "Nur diese US-Sitzung ist betroffen. Die Uhrzeit erteilt niemals eine Fahrberechtigung."
  },
  {
    "en": "Run US clock",
    "sv": "Kör US-klockan",
    "da": "Start US-uret",
    "nb": "Start US-klokken",
    "de": "US-Uhr laufen lassen"
  },
  {
    "en": "Apply US clock",
    "sv": "Ställ US-klockan",
    "da": "Indstil US-uret",
    "nb": "Still US-klokken",
    "de": "US-Uhr einstellen"
  }
];
  globalThis.TrainMeetMessages ||= {};
  for (const row of rows) for (const source of Object.values(row)) globalThis.TrainMeetMessages[source] = row;
})();
