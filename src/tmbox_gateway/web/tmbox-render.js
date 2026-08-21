/* TMBox display renderer.

   A mirror of firmware/esp32/lib/tmbox_core/renderer.cpp in trainmeet-tmbox.
   Two implementations of one layout is how the firmware and the server drifted
   apart before, so this one is not trusted to be right by reading: the C++
   renderer publishes golden_frames.txt and tests/test_display_golden.py fails
   the moment this file stops reproducing it, character for character.

   Change the layout there first, regenerate, then change it here.            */

(function (global) {
  "use strict";

  const FOLDS = { "Å": "A", "å": "A", "Ä": "A", "ä": "A", "Ö": "O", "ö": "O" };

  function transliterate(value) {
    let result = "";
    for (const character of String(value)) {
      if (FOLDS[character]) { result += FOLDS[character]; continue; }
      // Anything else outside plain ASCII would render as noise on a
      // character display, so it becomes a space rather than a stray glyph.
      result += character.charCodeAt(0) < 0x80 ? character : " ";
    }
    return result;
  }

  function fit(value, width) {
    return String(value).slice(0, width).padEnd(width, " ");
  }

  function departureMark(trainNumber, track) {
    return track ? `${trainNumber}>[${track}]` : `${trainNumber}>`;
  }

  function arrivalMark(trainNumber, track) {
    return track ? `[${track}]<${trainNumber}` : `<${trainNumber}`;
  }

  function frameOf(geometry, lines) {
    const frame = [];
    for (let row = 0; row < geometry.rows; row += 1) {
      const source = row < lines.length ? lines[row] : "";
      frame.push(fit(geometry.supportsSwedish ? source : transliterate(source), geometry.cols));
    }
    return frame;
  }

  /** The A key carries one operative action; the server decides which are
      allowed and this order only picks between those. */
  const PRIMARY_ORDER = [
    ["train.position.set", "UPP"],
    ["train.crew_ready.set", "FORARE"],
    ["clearance.request", "BEGAR"],
    ["train.departed", "AVGATT"],
    ["train.approaching", "NARMAR"],
    ["train.arrived", "ANKOMMIT"],
  ];

  function primaryAction(movement) {
    const allowed = movement.allowed_actions || [];
    for (const [action, label] of PRIMARY_ORDER) {
      if (allowed.includes(action)) return { action, label };
    }
    return { action: "", label: "" };
  }

  function allows(movement, action) {
    return (movement.allowed_actions || []).includes(action);
  }

  function isArrival(movement) {
    return !movement.departure_time;
  }

  function trackLabel(config, movement) {
    const track = (config.tracks || []).find((entry) => entry.id === movement.assignedTrackId);
    return track ? track.display_label : "";
  }

  function movementMark(config, movement) {
    const track = trackLabel(config, movement);
    return isArrival(movement)
      ? arrivalMark(movement.train_number, track)
      : departureMark(movement.train_number, track);
  }

  function movementTime(movement) {
    return (isArrival(movement) ? movement.arrival_time : movement.departure_time) || "";
  }

  /** An operator reads Swedish, not a protocol enum. */
  function clearanceWord(status) {
    switch (status) {
      case "waiting": return "VANTAR";
      case "approved": return "KLART";
      case "rejected": return "EJ KLART";
      case "cancelled": return "ATERTAGEN";
      case "expired": return "UTGANGEN";
      // frameOf folds Swedish where the display needs it, and it is the only
      // place that should - folding here strips the vowels even on a display
      // that carries them.
      default: return status || "";
    }
  }

  /** Why a command was refused, in words the person holding the box reads. */
  const REFUSALS = {
    unknown_train_number: "FINNS EJ IDAG",
    missing_train_number: "SAKNAR NUMMER",
    unknown_movement: "TAGET FINNS EJ",
    unknown_track: "SPARET FINNS EJ",
    unknown_connection: "INGEN SADAN LINJE",
    channel_occupied: "LINJEN UPPTAGEN",
    clearance_not_pending: "REDAN AVGJORD",
    already_acknowledged: "REDAN KVITTERAD",
    unknown_clearance: "ARENDET AR BORTA",
    unknown_message: "ARENDET AR BORTA",
    not_receiver: "EJ ER FRAGA",
    not_sender: "EJ ER BEGARAN",
    not_assigned: "BOXEN EJ KOPPLAD",
    station_mismatch: "FEL STATION",
    stale_revision: "LAGET HAR ANDRATS",
    invalid_revision: "LAGET HAR ANDRATS",
    no_active_configuration: "INGEN TRAFF",
    unsupported_protocol: "FEL PROTOKOLL",
    unknown_action: "OKANT KOMMANDO",
  };

  function rejectionWord(reason) {
    // An unmapped reason still reaches the display rather than vanishing.
    return REFUSALS[reason] || reason || "";
  }

  /** The station's own code, not the id it carries internally. */
  function otherStationCode(config, connectionId) {
    const connection = (config.connections || [])
      .find((entry) => entry.connection_id === connectionId);
    return connection ? connection.other_station_code : "";
  }

  /** Push `right` hard against the right edge, so it lands in the same place
      on every geometry and the eye learns where to look once. */
  function spread(left, right, cols) {
    return cols > left.length + right.length
      ? left + " ".repeat(cols - left.length - right.length) + right
      : `${left} ${right}`;
  }

  function render(geometry, view, config, snapshot) {
    const movements = snapshot.movements || [];
    const clearances = snapshot.active_clearances || [];
    const messages = snapshot.line_messages || [];
    let lines = [];

    switch (view.screen) {
      case "Identity": lines = ["TRAINMEET TMBOX", view.device_code || ""]; break;
      case "NoNetwork": lines = ["NAT SAKNAS", "FORSOKER IGEN"]; break;
      case "SetupPortal": lines = ["INSTALLERA WIFI", view.access_point_name || ""]; break;
      case "SeekingServer": lines = ["SOKER SERVER", view.device_code || ""]; break;
      case "ServerGone": lines = ["SERVER BORTA", "FORSOKER IGEN"]; break;
      case "AwaitingAssignment": lines = ["KOPPLA BOXEN", view.device_code || ""]; break;
      // The station is known but its data has not arrived. An empty overview
      // would claim there are no trains today, which is a different thing.
      case "LoadingStation": lines = ["STATION KOPPLAD", "HAMTAR DATA..."]; break;
      case "ResettingNetwork": lines = ["NATVERK RADERAS", view.device_code || ""]; break;
      case "Sending": lines = ["SKICKAR...", ""]; break;
      case "CommandAccepted": lines = ["KOMMANDO OK", ""]; break;
      case "CommandRejected": lines = ["KOMMANDO NEKAT", rejectionWord(view.reason)]; break;

      case "StationOverview": {
        const clock = (snapshot.clock && snapshot.clock.time) || "--:--";
        lines.push(spread(config.code || "TMBOX", clock, geometry.cols));
        lines.push(movements.length === 0
          ? "INGA TAG IDAG"
          : `${movements.length} TAG  C=BLADDRA`);
        if (geometry.rows >= 4) {
          for (const movement of movements) {
            if (lines.length >= geometry.rows) break;
            lines.push(`${movementTime(movement)} ${movementMark(config, movement)}`);
          }
        }
        break;
      }

      case "MovementDetail": {
        const movement = movements[view.selected_movement];
        if (!movement) { lines = ["INGET TAG VALT", "*=TILLBAKA"]; break; }
        lines.push(spread(movementMark(config, movement), movementTime(movement), geometry.cols));
        const primary = primaryAction(movement);
        let actions = primary.label ? `A=${primary.label}` : "";
        if (allows(movement, "train.track.change")) {
          actions = actions ? `${actions}  B=ANDRA` : "B=ANDRA";
        }
        lines.push(actions || "INGET TILLATET");
        if (geometry.rows >= 4) {
          lines.push(`FORARE ${movement.crewReady ? "PA PLATS" : "SAKNAS"}`);
          lines.push("C=NASTA  *=TILLBAKA");
        }
        break;
      }

      case "TrackPicker": {
        const tracks = config.tracks || [];
        if (tracks.length === 0) { lines.push("VALJ SPAR"); lines.push("INGA SPAR"); break; }
        const index = view.selected_track >= 0 && view.selected_track < tracks.length
          ? view.selected_track : 0;
        lines.push(spread("VALJ SPAR", tracks[index].display_label, geometry.cols));
        lines.push("A=VALJ  C=NASTA");
        if (geometry.rows >= 4) {
          lines.push(`${index + 1} AV ${tracks.length}`);
          lines.push("*=TILLBAKA");
        }
        break;
      }

      case "ConnectionPicker": {
        const connections = config.connections || [];
        if (connections.length === 0) {
          lines.push("BEGAR MOT"); lines.push("INGEN GRANNE"); break;
        }
        const index = view.selected_connection >= 0 && view.selected_connection < connections.length
          ? view.selected_connection : 0;
        // A request has to name the line the train is taking; the box does not
        // invent a choice the operator has to make.
        lines.push(spread("BEGAR MOT", connections[index].other_station_code, geometry.cols));
        lines.push("A=BEGAR  C=NASTA");
        if (geometry.rows >= 4) {
          lines.push(`${index + 1} AV ${connections.length}`);
          lines.push("*=TILLBAKA");
        }
        break;
      }

      case "TrainLookup": {
        // The cursor shows there is more to type; an empty field still says so.
        lines.push(spread("TAG", `${view.lookup_digits || ""}_`, geometry.cols));
        lines.push("A=SOK  B=SUDDA");
        if (geometry.rows >= 4) {
          lines.push("SIFFROR PA TANGENT");
          lines.push("*=AVBRYT");
        }
        break;
      }

      case "LookupResults": {
        const found = view.lookup_matches || [];
        if (found.length === 0) { lines = ["INGEN TRAFF", "*=TILLBAKA"]; break; }
        const index = view.selected_match >= 0 && view.selected_match < found.length
          ? view.selected_match : 0;
        const match = found[index];
        lines.push(`${match.train_number} ${found.length} TRAFFAR`);
        // Choosing which movement to look at is not an operative decision.
        lines.push("C=NASTA #=VALJ");
        if (geometry.rows >= 4) {
          const time = match.departure_time || match.arrival_time || "";
          const what = match.departure_time ? "AVG" : "ANK";
          lines.push(spread(`${what} ${time}`, `${index + 1}/${found.length}`, geometry.cols));
          lines.push("*=TILLBAKA");
        }
        break;
      }

      case "ClearanceInbox": {
        if (clearances.length === 0) { lines = ["INGA ARENDEN", "*=TILLBAKA"]; break; }
        const index = view.selected_case >= 0 && view.selected_case < clearances.length
          ? view.selected_case : 0;
        const clearance = clearances[index];
        lines.push(`KLARERING ${clearanceWord(clearance.status)}`);
        // A settles it and B refuses it; # never leaves an operative decision.
        lines.push("A=KLART  B=EJ");
        if (geometry.rows >= 4) {
          const from = otherStationCode(config, clearance.connection_id);
          lines.push(`FRAN ${from || clearance.from_station_id}`);
          lines.push(`${index + 1} AV ${clearances.length}  *=TILLBAKA`);
        }
        break;
      }

      case "LineInbox": {
        if (messages.length === 0) { lines = ["INGA MEDDELANDEN", "*=TILLBAKA"]; break; }
        const index = view.selected_case >= 0 && view.selected_case < messages.length
          ? view.selected_case : 0;
        const message = messages[index];
        lines.push("LINJEN LEDIG");
        // One-sided information: the only answer is that it was shown.
        lines.push("A=KVITTERA");
        if (geometry.rows >= 4) {
          const from = otherStationCode(config, message.connection_id);
          lines.push(`FRAN ${from || message.from_station_id}`);
          lines.push("*=TILLBAKA");
        }
        break;
      }
      default: lines = [];
    }

    return frameOf(geometry, lines);
  }

  const api = { render, transliterate, fit, departureMark, arrivalMark };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.TMBoxRender = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
