import test from "node:test";
import assert from "node:assert/strict";
import { runCase } from "./framework.mjs";
const step = (id, extra = {}) => ({
  id,
  type: "assert",
  timeoutMs: 1000,
  onFailure: "stop",
  ...extra,
});
test("Midscene Test stops after a failed assertion unless explicitly continued", async () => {
  const calls = [];
  const execute = async s => {
    calls.push(s.id);
    return s.id === "a" ? "failed" : "passed";
  };
  assert.equal(
    await runCase({ steps: [step("a"), step("b")] }, execute),
    "failed"
  );
  assert.deepEqual(calls, ["a"]);
  calls.length = 0;
  assert.equal(
    await runCase(
      { steps: [step("a", { onFailure: "continue" }), step("b")] },
      execute
    ),
    "failed"
  );
  assert.deepEqual(calls, ["a", "b"]);
});
test("model error prevents subsequent actions even with continue enabled", async () => {
  const calls = [];
  const state = await runCase(
    { steps: [step("a", { onFailure: "continue" }), step("b")] },
    async s => {
      calls.push(s.id);
      throw new Error("transport");
    }
  );
  assert.equal(state, "error");
  assert.deepEqual(calls, ["a"]);
});
test("actions alone need review; disabled assertions cannot imply success", async () => {
  assert.equal(
    await runCase(
      { steps: [step("a", { type: "action" }), step("b", { enabled: false })] },
      async () => "passed"
    ),
    "needs_review"
  );
});
