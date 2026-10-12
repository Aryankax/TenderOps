// Module-level SSE buffer shared across remounts: switching tabs unmounts
// StageTracker, but the events must not disappear. Events dedupe by seq —
// the backend replays history on resubscribe (?after=<seq>), so a remount
// restores the timeline instead of duplicating it. A sessionKey (bumped per
// run/resume) separates consecutive runs sharing one tender_id.

const MAX = 200;

export const eventStore = {
  sessionKey: null,
  events: [],
  lastSeq: 0,

  ensure(sessionKey) {
    if (this.sessionKey !== sessionKey) {
      this.sessionKey = sessionKey;
      this.events = [];
      this.lastSeq = 0;
    }
  },

  append(event) {
    const seq = event?.seq || 0;
    if (seq && this.events.some((e) => e.seq === seq)) return false;
    this.events = [...this.events.slice(-(MAX - 1)), event];
    if (seq > this.lastSeq) this.lastSeq = seq;
    return true;
  },
};

/** Client-side mirror of the backend tender filter (observability.read_events). */
export function eventMatchesTender(event, tenderId) {
  if (!tenderId) return true;
  const d = event?.data || {};
  return d.tender_id === tenderId || d.run_id === tenderId;
}
