// Page-side handle on the Relate worker: query(cypher, params) -> Promise.

export class GraphClient {
  constructor(onEvent) {
    this.worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
    this.pending = new Map();
    this.next = 1;
    this.worker.onmessage = (e) => {
      const msg = e.data;
      if (msg.id !== undefined) {
        const p = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        p && p({ ...msg.result, ms: msg.ms });
      } else {
        onEvent(msg);
      }
    };
    this.worker.onerror = (e) => onEvent({ type: "failed", error: e.message || "worker failed to start" });
  }

  /** Raw envelope: {columns, rows, error, ms}. */
  run(cypher, params) {
    const id = this.next++;
    return new Promise((resolve) => {
      this.pending.set(id, resolve);
      this.worker.postMessage({ id, cypher, params: params || null });
    });
  }

  /** Rows, or throws on a Cypher error. */
  async rows(cypher, params) {
    const res = await this.run(cypher, params);
    if (res.error) throw new Error(res.error);
    return res.rows;
  }
}
