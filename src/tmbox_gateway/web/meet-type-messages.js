/* Shared EU/US selection copy; UI language never changes operating rules. */
(() => {
  const rows = [
  {
    "en": "Operating session type",
    "sv": "Typ av tågträff",
    "da": "Type af togtræf",
    "nb": "Type togtreff",
    "de": "Betriebsart"
  },
  {
    "en": "US operating session",
    "sv": "US-tågträff",
    "da": "US-togtræf",
    "nb": "US-togtreff",
    "de": "US-Betriebstreffen"
  },
  {
    "en": "EU operating session",
    "sv": "EU-tågträff",
    "da": "EU-togtræf",
    "nb": "EU-togtreff",
    "de": "EU-Betriebstreffen"
  },
  {
    "en": "TKL · station operations · TMBox",
    "sv": "TKL · stationsdrift · TMBox",
    "da": "TKL · stationsdrift · TMBox",
    "nb": "TKL · stasjonsdrift · TMBox",
    "de": "TKL · Bahnhofsbetrieb · TMBox"
  },
  {
    "en": "Dispatcher · Conductor · Track Warrant Control",
    "sv": "Dispatcher · Conductor · Track Warrant Control",
    "da": "Dispatcher · Conductor · Track Warrant Control",
    "nb": "Dispatcher · Conductor · Track Warrant Control",
    "de": "Dispatcher · Conductor · Track Warrant Control"
  },
  {
    "en": "UI language is a separate choice. US operating views default to English.",
    "sv": "Språk väljs separat. US-driftvyer börjar på engelska.",
    "da": "Sprog vælges separat. US-driftsvisninger starter på engelsk.",
    "nb": "Språk velges separat. US-driftsvisninger starter på engelsk.",
    "de": "Die Sprache wird separat gewählt. US-Betriebsansichten starten auf Englisch."
  },
  {
    "en": "The session type is locked once content has been added. Create a new session to use the other type.",
    "sv": "Träfftypen är låst när innehåll har lagts till. Skapa en ny träff för den andra typen.",
    "da": "Træftypen låses, når indhold er tilføjet. Opret et nyt træf for den anden type.",
    "nb": "Trefftypen låses når innhold er lagt til. Opprett et nytt treff for den andre typen.",
    "de": "Nach dem Hinzufügen von Inhalten ist die Betriebsart gesperrt. Erstellen Sie für die andere Betriebsart ein neues Treffen."
  },
  {
    "en": "US Cloud import and publishing are not available yet. Open US Dispatcher on your local TrainMeet Server to load a US package.",
    "sv": "Cloud-import och publicering för US är inte tillgängliga ännu. Öppna US Dispatcher på din lokala TrainMeet Server för att läsa in ett US-paket.",
    "da": "Cloud-import og publicering for US er endnu ikke tilgængelige. Åbn US Dispatcher på din lokale TrainMeet Server for at indlæse en US-pakke.",
    "nb": "Cloud-import og publisering for US er ikke tilgjengelig ennå. Åpne US Dispatcher på din lokale TrainMeet Server for å laste inn en US-pakke.",
    "de": "Cloud-Import und Veröffentlichung für US sind noch nicht verfügbar. Öffnen Sie US Dispatcher auf Ihrem lokalen TrainMeet Server, um ein US-Paket zu laden."
  },
  {
    "en": "Choose EU or US when creating the session.",
    "sv": "Välj EU eller US när du skapar träffen.",
    "da": "Vælg EU eller US, når du opretter træffet.",
    "nb": "Velg EU eller US når du oppretter treffet.",
    "de": "Wählen Sie EU oder US beim Erstellen des Treffens."
  },
  {
    "en": "Switching views does not change or stop an operating session.",
    "sv": "Att byta vy ändrar eller stoppar inte en körning.",
    "da": "Skift af visning ændrer eller stopper ikke en kørsel.",
    "nb": "Bytte av visning endrer eller stopper ikke en kjøring.",
    "de": "Ein Ansichtswechsel ändert oder stoppt keine Betriebssitzung."
  }
];
  globalThis.TrainMeetMessages ||= {};
  for (const row of rows) for (const source of Object.values(row)) globalThis.TrainMeetMessages[source] = row;
})();

