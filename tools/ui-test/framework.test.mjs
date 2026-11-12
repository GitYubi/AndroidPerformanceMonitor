import test from "node:test";
import assert from "node:assert/strict";
import { runCase } from "./framework.mjs";
import { executeStep, validateJob } from "./protocol.mjs";
const step = (id, extra = {}) => ({
  id,
  type: "assert",
  timeoutMs: 1000,
  onFailure: "stop",
  ...extra,
});
test("stops after a failed assertion unless explicitly continued", async () => {
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

test("for loops execute the matching If or Else branch with scoped indexes", async () => {
  const testCase = {
    schemaVersion: 1,
    steps: [
      step("loop", {
        type: "for",
        options: { count: 3, indexVariable: "index" },
      }),
      step("check", {
        type: "if",
        parentId: "loop",
        condition: step("check", {
          type: "variable_assert",
          options: {
            variable: "index",
            operator: "eq",
            expectedValue: "${wanted}",
          },
        }),
      }),
      step("yes", {
        parentId: "check",
        type: "delay",
        options: { durationMs: 1 },
      }),
      step("fallback", {
        parentId: "check",
        branch: "else",
        type: "else",
      }),
      step("no", {
        parentId: "fallback",
        type: "delay",
        options: { durationMs: 1 },
      }),
      step("done", {
        type: "variable_assert",
        options: { variable: "wanted", expectedValue: "2" },
      }),
    ].map(item => ({
      prompt: item.id,
      ...item,
      ...(item.condition && {
        condition: { prompt: item.id, ...item.condition },
      }),
    })),
  };
  const parameters = { wanted: "2" };
  validateJob({ serial: "", testCase, parameters });
  const calls = [];
  const state = await runCase(
    testCase,
    async (item, variables, occurrence, iterations) => {
      calls.push([item.id, variables.index, occurrence, iterations]);
      return executeStep(null, item.condition ?? item, {
        ...parameters,
        ...variables,
      });
    }
  );
  assert.equal(state, "passed");
  assert.deepEqual(calls, [
    ["loop", undefined, 1, []],
    ["check", "1", 1, [1]],
    ["fallback", "1", 1, [1]],
    ["no", "1", 1, [1]],
    ["check", "2", 2, [2]],
    ["yes", "2", 1, [2]],
    ["check", "3", 3, [3]],
    ["fallback", "3", 2, [3]],
    ["no", "3", 2, [3]],
    ["done", undefined, 1, []],
  ]);
});

test("a condition error stops the case without entering Else", async () => {
  const condition = step("check", { type: "if" });
  const calls = [];
  const state = await runCase(
    { steps: [condition, step("no", { parentId: "check", branch: "else" })] },
    async item => {
      calls.push(item.id);
      return "error";
    }
  );
  assert.equal(state, "error");
  assert.deepEqual(calls, ["check"]);
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
