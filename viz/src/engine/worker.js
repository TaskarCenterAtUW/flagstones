import { createSession } from "./session.js";

// The Worker side of app/engine.js, which waits on a reply per message id.
let session = null;
self.onmessage = ({ data: { id, msg } }) => {
  try {
    if (msg.type === "init") session = createSession(msg.bundle);
    const result = msg.type === "init" ? null : session.handle(msg);
    self.postMessage({ id, ok: true, result });
  } catch (err) {
    self.postMessage({ id, ok: false, error: err?.message ?? String(err) });
  }
};
