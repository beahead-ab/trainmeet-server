//: Referensstationen som allt i TMBox-dokumentationen ritas ur.
//
// Charlottendal med tågen 421 och 428 är samma uppsättning som firmwarens
// test_native/fixtures.h, och det är den som golden_frames.txt är avtryckt
// ur. Den bodde tidigare inne i tests/js/render_golden.mjs, vilket räckte så
// länge guldtestet var enda läsaren. Nu ritar även skärmkatalogen i KÖR →
// TMBox v2 ur den, och två kopior av en referensstation är två som glider.
//
// Alltså: en fil, laddad både av harnesset i Node och av sidan i webbläsaren.
// Ändras något här faller guldtestet om ändringen inte finns i firmwaren.
(function (global) {
  "use strict";

  const config = {
    station_id: "st-cda", code: "CDA", name: "Charlottendal",
    tracks: [
      { id: "track-cda-1a", display_label: "1A" },
      { id: "track-cda-1b", display_label: "1B" },
      { id: "track-cda-2a", display_label: "2A" },
      { id: "track-cda-2b", display_label: "2B" },
    ],
    connections: [
      { connection_id: "connection-cda-vst", other_station_code: "VST", track_type: "single" },
      { connection_id: "connection-cda-kun", other_station_code: "KUN", track_type: "double" },
    ],
  };

  const snapshot = {
    station_id: "st-cda",
    clock: { time: "09:00", running: true },
    movements: [
      { id: "movement-421-cda", train_number: "421", arrival_time: "", departure_time: "09:20",
        departure: "none", arrival: "none", assignedTrackId: "track-cda-1b", crewReady: false,
        allowed_actions: ["train.position.set"] },
      { id: "movement-428-cda", train_number: "428", arrival_time: "09:41", departure_time: "",
        departure: "none", arrival: "none", assignedTrackId: "track-cda-2a", crewReady: false,
        allowed_actions: ["train.approaching", "train.arrived"] },
    ],
    active_clearances: [
      { clearance_id: "clr-1", movement_id: "movement-421-cda",
        connection_id: "connection-cda-vst", status: "waiting",
        from_station_id: "st-vst", to_station_id: "st-cda" },
    ],
    line_messages: [
      { message_id: "msg-1", connection_id: "connection-cda-vst",
        status: "delivered_to_device", from_station_id: "st-vst" },
    ],
  };

  //: [namn i guldfilen, Screen-värde, vilken rörelse som är vald]
  //
  // Ordningen är dump_golden.cpp:s. Den är inte godtycklig - guldfilen jämförs
  // rad för rad, så en omkastning här ser ut som en renderingsändring.
  const CASES = [
    ["identity", "Identity", -1],
    ["awaiting-assignment", "AwaitingAssignment", -1],
    ["loading-station", "LoadingStation", -1],
    ["resetting-network", "ResettingNetwork", -1],
    ["station-overview", "StationOverview", -1],
    ["movement-departure", "MovementDetail", 0],
    ["movement-arrival", "MovementDetail", 1],
    ["track-picker", "TrackPicker", 0],
    ["connection-picker", "ConnectionPicker", 0],
    ["train-lookup", "TrainLookup", -1],
    ["lookup-results", "LookupResults", -1],
    ["clearance-inbox", "ClearanceInbox", 0],
    ["line-inbox", "LineInbox", 0],
    ["command-accepted", "CommandAccepted", -1],
    ["command-rejected", "CommandRejected", -1],
  ];

  const GEOMETRIES = [
    ["16x2", { rows: 2, cols: 16, supportsSwedish: false }],
    ["20x2", { rows: 2, cols: 20, supportsSwedish: false }],
    ["16x4", { rows: 4, cols: 16, supportsSwedish: false }],
    ["20x4", { rows: 4, cols: 20, supportsSwedish: false }],
  ];

  //: Vyn som ett givet fall renderas ur. Samma fält som firmwarens View.
  function viewFor(screen, movement) {
    return {
      screen, device_code: "TMBOX-A7K2C3", selected_movement: movement,
      selected_track: 0, selected_connection: 0, selected_case: 0,
      reason: "unknown_train_number",
      lookup_digits: "42", selected_match: 0,
      lookup_matches: [
        { movement_id: "movement-421-cda", train_number: "421", arrival_time: "",
          departure_time: "09:20", track_id: "track-cda-1b" },
        { movement_id: "movement-428-cda", train_number: "421", arrival_time: "09:41",
          departure_time: "", track_id: "track-cda-2a" },
      ],
    };
  }


  //: Ögonblicksbilden som tangentspåren körs mot. Skiljer sig från den ovan
  //: genom att ärendekorgen är tom tills ett scenario säger annat.
  function twoMovements() {
    return {
      station_id: "st-cda", clock: { time: "09:00", running: true },
      movements: [
        { id: "movement-421-cda", train_number: "421", arrival_time: "", departure_time: "09:20",
          departure: "none", arrival: "none", assignedTrackId: "track-cda-1b", crewReady: false,
          allowed_actions: ["train.position.set"] },
        { id: "movement-428-cda", train_number: "428", arrival_time: "09:41", departure_time: "",
          departure: "none", arrival: "none", assignedTrackId: "track-cda-2a", crewReady: false,
          allowed_actions: ["train.approaching", "train.arrived"] },
      ],
      active_clearances: [], line_messages: [],
    };
  }

  function withCases() {
    const snapshot = twoMovements();
    snapshot.active_clearances = [{ clearance_id: "clr-1", movement_id: "movement-421-cda",
      connection_id: "connection-cda-vst", status: "waiting",
      from_station_id: "st-vst", to_station_id: "st-cda" }];
    snapshot.line_messages = [{ message_id: "msg-1", connection_id: "connection-cda-vst",
      status: "delivered_to_device", from_station_id: "st-vst" }];
    return snapshot;
  }

  //: Ett tryck per halv sekund: låset har alltid hunnit löpa ut, så spåret
  //: visar vad varje tangent betyder. HURRIED är snabbare än en operatör -
  //: det är vad en kärvande tangent eller en studsande kontakt ger.
  const UNHURRIED = 500;
  const HURRIED = 100;

  //: De tolv tangentspåren, i dump_traces.cpp:s ordning.
  //
  // `name` och `keys` är firmwarens; `title` och `note` är skrivna för
  // människor och är det enda i den här filen som inte kommer ur guldfilen.
  // Ordningen får inte kastas om - guldfilen jämförs rad för rad.
  const TRACES = [
    { name: "browse-and-position", keys: "CCA*", snapshot: "two", allowed: ["train.position.set"], pace: UNHURRIED,
      title: "Bläddra och ställa upp",
      note: "C stegar mellan tågen, A utför den primära handlingen på det valda, * går tillbaka till stationsöversikten." },
    { name: "track-change-refused-then-allowed", keys: "CBA", snapshot: "two", allowed: ["train.position.set"], pace: UNHURRIED,
      title: "Spårbyte nekas, uppställning tillåts",
      note: "B ignoreras eftersom rörelsen inte tillåter spårbyte. Boxen svarar inte med ett felmeddelande - den gör ingenting, vilket är skillnaden mot en knapp som är trasig." },
    { name: "track-picker-picks-second", keys: "CBCA", snapshot: "two", allowed: ["train.track.change"], pace: UNHURRIED,
      title: "Spårväljaren tar det andra spåret",
      note: "Med spårbyte tillåtet öppnar B väljaren, C stegar i den och A skickar valet." },
    { name: "hash-opens-clearance-and-a-settles", keys: "#A", snapshot: "cases", allowed: [], pace: UNHURRIED,
      title: "Fyrkant öppnar ärendet, A beviljar",
      note: "# är kvittera visning - att öppna korgen är inget operativt beslut. Beslutet ligger på A." },
    { name: "hash-then-b-refuses", keys: "#B", snapshot: "cases", allowed: [], pace: UNHURRIED,
      title: "Fyrkant öppnar ärendet, B avslår",
      note: "Samma väg in, motsatt svar. Både bifall och avslag går via A eller B, aldrig via #." },
    { name: "line-message-only-acknowledges", keys: "##AB", snapshot: "cases", allowed: [], pace: UNHURRIED,
      title: "Linjemeddelandet kan bara kvitteras",
      note: "Det andra trycket på # ignoreras: # kvitterar visning, och när korgen redan är öppen finns inget mer att kvittera. Klareringen som ligger där svaras fortfarande på med A eller B - det operativa beslutet lämnar aldrig fyrkanten." },
    { name: "star-always-returns", keys: "C#*", snapshot: "cases", allowed: [], pace: UNHURRIED,
      title: "Stjärna tar alltid tillbaka",
      note: "# ignoreras inne på en rörelse - det finns ingen visning att kvittera där. * tar tillbaka till stationsöversikten oavsett hur långt in man är." },
    { name: "nothing-allowed-stays-silent", keys: "CA", snapshot: "two", allowed: ["train.track.change"], pace: UNHURRIED,
      title: "Utan tillåten handling händer ingenting",
      note: "A gör inget när rörelsens tillåtna handlingar inte innehåller något A kan utföra." },
    { name: "clearance-request-picks-the-neighbour", keys: "CACA", snapshot: "two", allowed: ["clearance.request"], pace: UNHURRIED,
      title: "Klareringsbegäran väljer motstation",
      note: "A öppnar anslutningsväljaren när handlingen kräver en motpart, C stegar mellan grannarna och A skickar." },
    { name: "digits-look-up-a-train", keys: "421BA", snapshot: "two", allowed: [], pace: UNHURRIED,
      title: "Siffror slår upp ett tåg",
      note: "Siffrorna 4, 2, 1 byggs upp till 421. B raderar sista siffran, så uppslaget som A skickar gäller 42 - inte 421. B är radera här, inte sök." },
    { name: "hurried-presses-are-swallowed", keys: "CCA", snapshot: "two", allowed: ["train.position.set"], pace: HURRIED,
      title: "För snabba tryck sväljs",
      note: "Samma sekvens som det första spåret, men 100 ms mellan trycken. Inmatningslåset på 500 ms gör att tryck efter ett skärmbyte ignoreras - en studsande kontakt får inte bli ett kommando." },
    { name: "hurried-clearance-answer-is-swallowed", keys: "#A", snapshot: "cases", allowed: [], pace: HURRIED,
      title: "För snabbt svar på ett ärende sväljs",
      note: "Låset skyddar särskilt här: ett operativt beslut får aldrig falla ut av att någon råkar hålla ner en tangent." },
  ];

  const api = { config, snapshot, CASES, GEOMETRIES, viewFor,
                twoMovements, withCases, TRACES, UNHURRIED, HURRIED };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.TMBoxFixtures = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
