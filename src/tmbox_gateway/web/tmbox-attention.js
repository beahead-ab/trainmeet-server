/* TMBox attention policy.

   A mirror of firmware/esp32/lib/tmbox_core/attention.cpp in trainmeet-tmbox.
   Held to the firmware's own answers by golden_attention.txt rather than to a
   reading of the spec: tests/test_display_golden.py replays the same snapshot
   runs and fails the moment the two disagree.

   What deserves a sound is decided here, and most of the time the answer is
   nothing. A box that pips at everything gets ignored.

   A change the dispatcher caused is not news to them, and that needs no
   suppression flag: the server only lets the receiver answer a clearance and
   only the sender cancel it, so the direction of a row already says who moved
   it. The direction checks below carry the rule.                            */

(function (global) {
  "use strict";

  const Attention = {
    None: "None",
    ConnectionLost: "ConnectionLost",
    IncomingRequest: "IncomingRequest",
    RequestDenied: "RequestDenied",
    RequestApproved: "RequestApproved",
    IncomingTrain: "IncomingTrain",
    ConnectionRestored: "ConnectionRestored",
  };

  // Descending. Losing the server outranks everything, because every other
  // event is a claim about state the box can no longer confirm.
  const PRIORITY = [
    Attention.ConnectionLost, Attention.IncomingRequest, Attention.RequestDenied,
    Attention.RequestApproved, Attention.IncomingTrain, Attention.ConnectionRestored,
  ];

  function settled(status) {
    return status === "approved" || status === "rejected";
  }

  class AttentionController {
    constructor() {
      this.seeded = false;
      this.online = false;
      this.hasBeenOnline = false;
      this.clearances = new Map();
      this.messages = new Set();
    }

    /* Reassignment. The box is now a different station and remembers nothing.
       The link is deliberately not forgotten: reassignment does not disconnect
       anything, and pretending otherwise would announce a restore that never
       happened. */
    forget() {
      this.seeded = false;
      this.clearances = new Map();
      this.messages = new Set();
    }

    observe(snapshot) {
      const events = [];
      // The wire calls it active_clearances; the firmware struct calls it
      // clearances. The JS side always works on wire shape.
      const clearanceRows = snapshot.active_clearances || [];
      const messageRows = snapshot.line_messages || [];

      // Rebuilt rather than updated, so a clearance that has left the snapshot
      // stops costing memory.
      const clearances = new Map();
      const messages = new Set();
      for (const row of clearanceRows) clearances.set(row.clearance_id, row.status);
      for (const row of messageRows) messages.add(row.message_id);

      if (!this.seeded) {
        // Catching up is not news.
        this.seeded = true;
        this.clearances = clearances;
        this.messages = messages;
        return events;
      }

      for (const row of clearanceRows) {
        const id = row.clearance_id;

        if (!this.clearances.has(id)) {
          // Someone wants to send a train here. A waiting clearance pointing
          // the other way is our own request still in flight - not news yet.
          if (row.status === "waiting" && row.to_station_id === snapshot.station_id) {
            events.push({ kind: Attention.IncomingRequest, subject: id });
          }
          continue;
        }

        // Only a transition counts, and only into a settled answer. A
        // cancellation or an expiry takes something away; it does not need a
        // sound to tell the dispatcher to stop waiting.
        if (this.clearances.get(id) === row.status || !settled(row.status)) continue;
        if (row.from_station_id !== snapshot.station_id) continue;
        events.push({
          kind: row.status === "approved" ? Attention.RequestApproved : Attention.RequestDenied,
          subject: id,
        });
      }

      for (const row of messageRows) {
        const id = row.message_id;
        if (this.messages.has(id)) continue;
        if (row.from_station_id === snapshot.station_id) continue;
        events.push({ kind: Attention.IncomingTrain, subject: id });
      }

      this.clearances = clearances;
      this.messages = messages;
      return events;
    }

    observeLink(online) {
      const events = [];
      if (online === this.online) return events;
      this.online = online;
      if (online) {
        if (this.hasBeenOnline) {
          events.push({ kind: Attention.ConnectionRestored, subject: "" });
        }
        this.hasBeenOnline = true;
      } else {
        // No hasBeenOnline guard: reaching this branch means online was true,
        // and the only place that sets it also sets hasBeenOnline.
        events.push({ kind: Attention.ConnectionLost, subject: "" });
      }
      return events;
    }

    /* One buzzer, one sound. */
    static loudest(events) {
      for (const kind of PRIORITY) {
        if (events.some((event) => event.kind === kind)) return kind;
      }
      return Attention.None;
    }
  }

  const api = { AttentionController, Attention };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.TMBoxAttention = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
