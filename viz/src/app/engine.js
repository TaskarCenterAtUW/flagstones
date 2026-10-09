import { createSession } from "../engine/session.js";

// Runs the session on the next tick, so a compute does not run inside the
// call that asked for it and the busy notify paints first. Node tests use
// it, and so does any runtime with no Worker; a browser that has Worker but
// not module workers gets the worker engine and its error.
export function inProcessEngine(bundle) {
  const session = createSession(bundle);
  return {
    kind: "in-process",
    send: (msg) => new Promise((resolve, reject) => setTimeout(() => {
      try { resolve(session.handle(msg)); } catch (err) { reject(err); }
    }, 0)),
  };
}

// A module Worker hosts the session, so a Seattle recompute stays off the main
// thread. Each send waits on the init message that delivers the bundle.
export function workerEngine(bundle) {
  const worker = new Worker(new URL("../engine/worker.js", import.meta.url), { type: "module" });
  const pending = new Map();
  let next = 0;
  worker.onmessage = ({ data }) => {
    const p = pending.get(data.id);
    if (!p) return;
    pending.delete(data.id);
    if (data.ok) p.resolve(data.result); else p.reject(new Error(data.error));
  };
  worker.onerror = (event) => {
    for (const p of pending.values()) p.reject(new Error(event.message ?? "worker error"));
    pending.clear();
  };
  const post = (msg) => new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, msg });
  });
  // The worker receives city, nodes and edges on init, all the session
  // reads; clips and reference stay home, out of the structured clone.
  const ready = post({ type: "init", bundle: { city: bundle.city, nodes: bundle.nodes, edges: bundle.edges } });
  return { kind: "worker", send: (msg) => ready.then(() => post(msg)) };
}

export function defaultEngine(bundle) {
  return typeof Worker === "function" ? workerEngine(bundle) : inProcessEngine(bundle);
}
