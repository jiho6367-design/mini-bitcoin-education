/** Small state rules shared by the browser and the Node regression tests. */
export function createSerialQueue() {
  let tail = Promise.resolve();
  return {
    run(task) {
      const result = tail.then(task);
      tail = result.catch(() => {});
      return result;
    },
  };
}
export function draftFingerprint({ node, from, to, amount }) {
  return JSON.stringify([
    String(node),
    String(from),
    String(to),
    String(amount),
  ]);
}
export function jobIsActive(job) {
  return Boolean(job && ["running", "paused"].includes(job.status));
}
export function mergeMiningSnapshot(current, candidate, node) {
  const sameJob =
    current?.job_id === candidate.job_id && current?.node === node;
  return sameJob ? { ...current, ...candidate, node } : { ...candidate, node };
}
export function acceptsRevision(current, next) {
  if (!current?.id || !next?.id || current.id !== next.id) return true;
  return (
    !Number.isFinite(next.revision) ||
    !Number.isFinite(current.revision) ||
    next.revision >= current.revision
  );
}
export function strictInteger(
  value,
  minimum = 1,
  maximum = Number.MAX_SAFE_INTEGER,
) {
  const text = String(value).trim();
  if (!/^\d+$/.test(text)) return null;
  const n = Number(text);
  return Number.isSafeInteger(n) && n >= minimum && n <= maximum ? n : null;
}
export function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
export function latestActualSample(job) {
  const samples = job?.samples;
  return Array.isArray(samples) && samples.length ? samples.at(-1) : null;
}
export function confirmedBlockIndex(chain, txid) {
  if (!txid || chain?.valid !== true) return null;
  return (
    chain.blocks?.find((block) =>
      block.transactions?.some((tx) => tx.txid === txid),
    )?.index ?? null
  );
}
