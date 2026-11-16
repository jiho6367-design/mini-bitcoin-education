import test from "node:test";
import assert from "node:assert/strict";
import {
  createSerialQueue,
  draftFingerprint,
  acceptsRevision,
  jobIsActive,
  strictInteger,
  escapeHtml,
  latestActualSample,
  mergeMiningSnapshot,
  confirmedBlockIndex,
} from "../static/ui-state.js";

test("submitted feedback becomes confirmed only when the selected valid chain includes that transaction", () => {
  const chain = {
    valid: true,
    blocks: [
      { index: 1, transactions: [{ txid: "funding" }] },
      { index: 2, transactions: [{ txid: "signed-transfer" }] },
    ],
  };
  assert.equal(confirmedBlockIndex(chain, "signed-transfer"), 2);
  assert.equal(confirmedBlockIndex(chain, "still-pending"), null);
  assert.equal(
    confirmedBlockIndex({ ...chain, valid: false }, "signed-transfer"),
    null,
  );
  assert.equal(confirmedBlockIndex(chain, null), null);
});

test("a new mining job cannot inherit the previous job's success message", () => {
  const old = {
    job_id: "old",
    node: "A",
    status: "found",
    resultText: "old success",
    found: true,
    accepted: true,
    samples: [{ nonce: 10, hash: "old" }],
  };
  const next = { job_id: "new", status: "running", attempts: 0, samples: [] };
  const replacement = mergeMiningSnapshot(old, next, "A");
  assert.equal(replacement.resultText, undefined);
  assert.equal(replacement.found, undefined);
  assert.equal(replacement.accepted, undefined);
  assert.deepEqual(replacement.samples, []);
  const same = mergeMiningSnapshot(
    old,
    { job_id: "old", status: "found" },
    "A",
  );
  assert.equal(same.resultText, "old success");
});

test("changing the recipient, amount or node invalidates the prepared transaction identity", () => {
  const draft = { node: "A", from: "Alice", to: "Bob", amount: "20" };
  const original = draftFingerprint(draft);
  assert.equal(original, draftFingerprint({ ...draft }));
  for (const changed of [
    { to: "Carol" },
    { amount: "99" },
    { from: "Carol" },
    { node: "B" },
  ]) {
    assert.notEqual(original, draftFingerprint({ ...draft, ...changed }));
  }
});

test("requests execute in user order even when the first result is delayed", async () => {
  const queue = createSerialQueue();
  const seen = [];
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const first = queue.run(async () => {
    seen.push("first-start");
    await gate;
    seen.push("first-end");
  });
  const second = queue.run(async () => {
    seen.push("second");
  });
  await Promise.resolve();
  assert.deepEqual(seen, ["first-start"]);
  release();
  await Promise.all([first, second]);
  assert.deepEqual(seen, ["first-start", "first-end", "second"]);
});

test("a rejected request does not permanently poison the request queue", async () => {
  const queue = createSerialQueue();
  await assert.rejects(
    queue.run(() => Promise.reject(new Error("server unavailable"))),
  );
  assert.equal(await queue.run(() => 42), 42);
});

test("old revisions are ignored within one practice but a new practice can bootstrap", () => {
  const current = { id: "practice-a", revision: 8 };
  assert.equal(
    acceptsRevision(current, { id: "practice-a", revision: 7 }),
    false,
  );
  assert.equal(
    acceptsRevision(current, { id: "practice-a", revision: 8 }),
    true,
  );
  assert.equal(
    acceptsRevision(current, { id: "practice-a", revision: 9 }),
    true,
  );
  assert.equal(
    acceptsRevision(current, { id: "practice-b", revision: 0 }),
    true,
  );
});

test("terminal candidate history is not treated as an active mining task", () => {
  for (const status of ["found", "stopped", "error", "idle"])
    assert.equal(jobIsActive({ job_id: "old", status }), false);
  for (const status of ["running", "paused"])
    assert.equal(jobIsActive({ job_id: "new", status }), true);
  assert.equal(jobIsActive(null), false);
});

test("only actual samples provide nonce/hash pairs, including nonce zero", () => {
  assert.equal(
    latestActualSample({
      nonce: 99,
      next_nonce: 100,
      hash: "unattempted",
      samples: [],
    }),
    null,
  );
  const zero = { nonce: 0, hash: "actually-calculated", passed: false };
  assert.deepEqual(
    latestActualSample({ next_nonce: 1, samples: [zero] }),
    zero,
  );
});

test("integer form inputs reject fractions, exponent notation and unsafe numbers", () => {
  for (const value of ["", "-1", "1.5", "1e3", "NaN", "9007199254740992"])
    assert.equal(strictInteger(value), null);
  assert.equal(strictInteger("20"), 20);
  assert.equal(strictInteger("0", 0), 0);
  assert.equal(strictInteger("6", 1, 5), null);
});

test("wallet names and server messages cannot create injected HTML", () => {
  assert.equal(
    escapeHtml('<img src=x onerror="attack()">&'),
    "&lt;img src=x onerror=&quot;attack()&quot;&gt;&amp;",
  );
});
