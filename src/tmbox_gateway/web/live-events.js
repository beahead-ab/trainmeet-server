/* What changed on the server, as it happens (GET /v1/events).
 *
 * The server says only *that* traffic, the clock, the boxes, the meet or the
 * automatic stations changed. Each page then fetches just that again at once, from
 * the endpoints it always used; its own timer stays as a slow fallback, and
 * returns to its usual pace whenever the stream is down.
 *
 * TrainMeetLive.subscribe(callback) opens the stream; callback(topics) gets a
 * Set of topic names, several changes within 250 ms together. A passive
 * subscriber (display-feed.js) listens without opening a stream, so a
 * page that needs none, such as a virtual TMBox, does not take one.
 */
(() => {
  const COALESCE_MS = 250;
  const RETRY_MS = 30000;
  const ALL = ["traffic", "clock", "devices", "runtime", "automatic"];
  const subscribers = new Set();
  const statusListeners = new Set();
  let source = null, retryTimer = null, flushTimer = null;
  let connected = false, wanted = false;
  let last = null; // {boot, seq} of the last event this page heard
  let pending = new Set();

  function setConnected(value) {
    if (connected === value) return;
    connected = value;
    for (const listener of statusListeners) {
      try { listener(value); } catch (error) { console.error(error); }
    }
  }

  function flush() {
    flushTimer = null;
    const topics = pending;
    pending = new Set();
    for (const callback of subscribers) {
      try { callback(topics); } catch (error) { console.error(error); }
    }
  }

  function changed(topics) {
    for (const topic of topics) pending.add(topic);
    if (!flushTimer) flushTimer = setTimeout(flush, COALESCE_MS);
  }

  function open() {
    if (!wanted || source || typeof EventSource === "undefined") return;
    clearTimeout(retryTimer);
    source = new EventSource("/v1/events");
    source.addEventListener("hello", (event) => {
      let hello;
      try { hello = JSON.parse(event.data); } catch { return; }
      // Back after a gap: anything may have changed while the stream was down.
      if (last && (last.boot !== hello.boot || last.seq !== hello.seq)) changed(ALL);
      last = { boot: hello.boot, seq: hello.seq };
      setConnected(true);
    });
    source.addEventListener("change", (event) => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (last) last.seq = data.seq;
      changed((data.topics || []).filter((topic) => ALL.includes(topic)));
    });
    source.onerror = () => {
      setConnected(false);
      // While CONNECTING the browser retries by itself (retry: 2000). Closed
      // means it gave up – the server was full or not one that streams.
      if (source.readyState === EventSource.CLOSED) {
        source = null;
        retryTimer = setTimeout(open, RETRY_MS);
      }
    };
  }

  globalThis.TrainMeetLive = {
    subscribe(callback, { passive = false } = {}) {
      subscribers.add(callback);
      if (!passive) { wanted = true; open(); }
      return () => subscribers.delete(callback);
    },
    onStatus(listener) {
      statusListeners.add(listener);
      return () => statusListeners.delete(listener);
    },
    get connected() { return connected; },
  };
})();
