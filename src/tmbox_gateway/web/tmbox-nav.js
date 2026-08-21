/* TMBox navigation state machine.

   A mirror of firmware/esp32/lib/tmbox_core/navigation.cpp in trainmeet-tmbox.
   Held to the firmware's own answers by golden_traces.txt rather than to a
   reading of the spec: tests/test_display_golden.py replays the same key
   sequences and fails the moment the two disagree.

   What a key does is decided here. Whether it was allowed stays the server's
   answer, read from allowed_actions and never inferred.                     */

(function (global) {
  "use strict";

  const INPUT_LOCK_MS = 500;

  const PRIMARY_ORDER = [
    "train.position.set", "train.crew_ready.set", "clearance.request",
    "train.departed", "train.approaching", "train.arrived",
  ];

  function allows(movement, action) {
    return (movement.allowed_actions || []).includes(action);
  }

  function primaryAction(movement) {
    for (const action of PRIMARY_ORDER) {
      if (allows(movement, action)) return action;
    }
    return "";
  }

  /** A train number is at most five digits; more is a slipped finger. */
  const MAX_TRAIN_DIGITS = 5;

  function indexOfMovement(snapshot, movementId) {
    return (snapshot.movements || []).findIndex((entry) => entry.id === movementId);
  }

  function nextIndex(current, count) {
    return count === 0 ? -1 : (current + 1) % count;
  }

  class LocalNavigationState {
    constructor() {
      this.view = {
        screen: "StationOverview",
        device_code: "",
        access_point_name: "",
        selected_movement: -1,
        selected_track: 0,
        selected_connection: 0,
        lookup_digits: "",
        lookup_matches: [],
        selected_match: 0,
        selected_case: 0,
        reason: "",
      };
      this.screenChangedAt = 0;
      this.everShown = false;
    }

    locked(nowMs) {
      return this.everShown && nowMs - this.screenChangedAt < INPUT_LOCK_MS;
    }

    show(screen, nowMs) {
      if (this.view.screen === screen) return;
      this.view.screen = screen;
      this.screenChangedAt = nowMs;
      this.everShown = true;
    }

    /** Take the server's answer to a train.lookup.

        One match goes straight to that train - making the operator choose
        from a list of one is a keypress that carries no information. */
    applyLookup(snapshot, matches, nowMs) {
      this.view.lookup_matches = matches;
      this.view.selected_match = 0;
      if (matches.length === 1) {
        const index = indexOfMovement(snapshot, matches[0].movement_id);
        if (index >= 0) {
          this.view.selected_movement = index;
          this.view.lookup_digits = "";
          this.view.lookup_matches = [];
          this.show("MovementDetail", nowMs);
          return;
        }
      }
      this.show("LookupResults", nowMs);
    }

    /** A fresh snapshot replaces the cache wholesale, so a selection that no
        longer exists must not survive it. */
    reconcile(config, snapshot, nowMs) {
      const movements = (snapshot.movements || []).length;
      const clearances = (snapshot.active_clearances || []).length;
      const messages = (snapshot.line_messages || []).length;
      if (this.view.selected_movement >= movements) {
        // Sliding to a neighbour would put a different movement under the key
        // the operator is about to press.
        this.view.selected_movement = -1;
        this.show("StationOverview", nowMs);
      }
      if (this.view.selected_case >= clearances && this.view.screen === "ClearanceInbox") {
        this.view.selected_case = 0;
        if (clearances === 0) this.show("StationOverview", nowMs);
      }
      if (this.view.selected_case >= messages && this.view.screen === "LineInbox") {
        this.view.selected_case = 0;
        if (messages === 0) this.show("StationOverview", nowMs);
      }
      if (this.view.selected_track >= (config.tracks || []).length) this.view.selected_track = 0;
      if (this.view.selected_connection >= (config.connections || []).length) {
        this.view.selected_connection = 0;
      }
    }

    press(key, nowMs, config, snapshot) {
      const ignored = { outcome: "Ignored", command: null };
      if (this.locked(nowMs)) return ignored;

      const movements = snapshot.movements || [];
      const clearances = snapshot.active_clearances || [];
      const messages = snapshot.line_messages || [];
      const tracks = config.tracks || [];

      // `*` always means back, from anywhere. Someone who is lost should never
      // have to work out where they are first.
      if (key === "*") {
        if (this.view.screen === "TrackPicker" || this.view.screen === "ConnectionPicker") {
          this.show("MovementDetail", nowMs);
          return { outcome: "Redraw", command: null };
        }
        this.view.selected_movement = -1;
        this.view.selected_case = 0;
        this.view.lookup_digits = "";
        this.view.lookup_matches = [];
        this.show("StationOverview", nowMs);
        return { outcome: "Redraw", command: null };
      }

      // A digit is always the start of a train number, wherever the operator
      // is. Nothing else on the keypad means a digit.
      if (key >= "0" && key <= "9") {
        if (this.view.screen !== "TrainLookup") {
          this.view.lookup_digits = "";
          this.show("TrainLookup", nowMs);
        }
        if (this.view.lookup_digits.length < MAX_TRAIN_DIGITS) this.view.lookup_digits += key;
        return { outcome: "Redraw", command: null };
      }

      switch (this.view.screen) {
        case "StationOverview": {
          if (key === "C" && movements.length > 0) {
            this.view.selected_movement = 0;
            this.show("MovementDetail", nowMs);
            return { outcome: "Redraw", command: null };
          }
          // `#` moves the eye, never a decision.
          if (key === "#") {
            if (clearances.length > 0) {
              this.view.selected_case = 0;
              this.show("ClearanceInbox", nowMs);
              return { outcome: "Redraw", command: null };
            }
            if (messages.length > 0) {
              this.view.selected_case = 0;
              this.show("LineInbox", nowMs);
              return { outcome: "Redraw", command: null };
            }
          }
          return ignored;
        }

        case "MovementDetail": {
          const movement = movements[this.view.selected_movement];
          if (!movement) return ignored;
          if (key === "C") {
            this.view.selected_movement = nextIndex(this.view.selected_movement, movements.length);
            this.screenChangedAt = nowMs;  // a different train is a new screen
            return { outcome: "Redraw", command: null };
          }
          if (key === "A") {
            const action = primaryAction(movement);
            if (!action) return ignored;
            if (action === "clearance.request") {
              // The box asks which line rather than guessing it.
              if ((config.connections || []).length === 0) return ignored;
              this.view.selected_connection = 0;
              this.show("ConnectionPicker", nowMs);
              return { outcome: "Redraw", command: null };
            }
            return { outcome: "Send", command: { action, movement_id: movement.id } };
          }
          if (key === "B" && allows(movement, "train.track.change")) {
            this.view.selected_track = 0;
            this.show("TrackPicker", nowMs);
            return { outcome: "Redraw", command: null };
          }
          return ignored;
        }

        case "TrackPicker": {
          if (tracks.length === 0) return ignored;
          if (key === "C") {
            this.view.selected_track = nextIndex(this.view.selected_track, tracks.length);
            this.screenChangedAt = nowMs;
            return { outcome: "Redraw", command: null };
          }
          if (key === "A") {
            const movement = movements[this.view.selected_movement];
            if (!movement || !allows(movement, "train.track.change")) return ignored;
            return {
              outcome: "Send",
              command: {
                action: "train.track.change",
                movement_id: movement.id,
                track_id: tracks[this.view.selected_track].id,
              },
            };
          }
          return ignored;
        }

        case "TrainLookup": {
          if (key === "B") {
            if (!this.view.lookup_digits) return ignored;
            this.view.lookup_digits = this.view.lookup_digits.slice(0, -1);
            return { outcome: "Redraw", command: null };
          }
          if (key === "A") {
            if (!this.view.lookup_digits) return ignored;
            return {
              outcome: "Send",
              command: { action: "train.lookup", train_number: this.view.lookup_digits },
            };
          }
          return ignored;
        }

        case "LookupResults": {
          const found = this.view.lookup_matches || [];
          if (found.length === 0) return ignored;
          if (key === "C") {
            this.view.selected_match = nextIndex(this.view.selected_match, found.length);
            this.screenChangedAt = nowMs;
            return { outcome: "Redraw", command: null };
          }
          // Choosing which movement to look at changes nothing, so `#` may.
          if (key === "#") {
            const index = this.view.selected_match < found.length ? this.view.selected_match : 0;
            const movement = indexOfMovement(snapshot, found[index].movement_id);
            if (movement < 0) return ignored;
            this.view.selected_movement = movement;
            this.view.lookup_digits = "";
            this.view.lookup_matches = [];
            this.show("MovementDetail", nowMs);
            return { outcome: "Redraw", command: null };
          }
          return ignored;
        }

        case "ConnectionPicker": {
          const connections = config.connections || [];
          if (connections.length === 0) return ignored;
          if (key === "C") {
            this.view.selected_connection =
              nextIndex(this.view.selected_connection, connections.length);
            this.screenChangedAt = nowMs;
            return { outcome: "Redraw", command: null };
          }
          if (key === "A") {
            const movement = movements[this.view.selected_movement];
            if (!movement || !allows(movement, "clearance.request")) return ignored;
            return {
              outcome: "Send",
              command: {
                action: "clearance.request",
                movement_id: movement.id,
                connection_id: connections[this.view.selected_connection].connection_id,
              },
            };
          }
          return ignored;
        }

        case "ClearanceInbox": {
          if (clearances.length === 0) return ignored;
          const index = this.view.selected_case < clearances.length ? this.view.selected_case : 0;
          if (key === "C") {
            this.view.selected_case = nextIndex(this.view.selected_case, clearances.length);
            this.screenChangedAt = nowMs;
            return { outcome: "Redraw", command: null };
          }
          // A grants, B refuses. Both are operative, so neither may sit on `#`.
          if (key === "A" || key === "B") {
            return {
              outcome: "Send",
              command: {
                action: "clearance.response",
                clearance_id: clearances[index].clearance_id,
                approved: key === "A",
                has_approved: true,
              },
            };
          }
          return ignored;
        }

        case "LineInbox": {
          if (messages.length === 0) return ignored;
          const index = this.view.selected_case < messages.length ? this.view.selected_case : 0;
          if (key === "C") {
            this.view.selected_case = nextIndex(this.view.selected_case, messages.length);
            this.screenChangedAt = nowMs;
            return { outcome: "Redraw", command: null };
          }
          if (key === "A") {
            return {
              outcome: "Send",
              command: {
                action: "line.available.acknowledge",
                message_id: messages[index].message_id,
              },
            };
          }
          return ignored;
        }

        default:
          return ignored;
      }
    }
  }

  const api = { LocalNavigationState, INPUT_LOCK_MS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.TMBoxNav = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
