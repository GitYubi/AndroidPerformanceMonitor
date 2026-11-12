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

test("image absence and variable inequality choose the matching branch, and capture errors stop", async () => {
  const { default: sharp } = await import("sharp");
  const image = color =>
    sharp({ create: { width: 2, height: 2, channels: 3, background: color } })
      .png()
      .toBuffer();
  const baseline = await image("#ffffff");
  const screen = await image("#000000");
  const condition = step("check", {
    type: "if",
    condition: step("check", {
      type: "image_assert",
      prompt: "检查图片",
      options: {
        baselineId: "a".repeat(64),
        mode: "screen",
        expected: "absent",
        minSimilarity: 100,
        pixelTolerance: 0,
      },
    }),
  });
  const yes = step("yes", {
    type: "delay",
    parentId: "check",
    options: { durationMs: 1 },
  });
  const no = step("no", {
    type: "delay",
    parentId: "check",
    branch: "else",
    options: { durationMs: 1 },
  });
  const seen = [];
  const run = async captureImage => {
    seen.length = 0;
    return runCase({ steps: [condition, yes, no] }, async item => {
      seen.push(item.id);
      return item.type === "if"
        ? executeStep(null, item.condition, {}, null, {
            captureImage,
            loadBaseline: () => baseline,
            isCondition: true,
          })
        : "passed";
    });
  };
  assert.equal(await run(async () => screen), "needs_review");
  assert.deepEqual(seen, ["check", "yes"]);
  assert.equal(await run(async () => baseline), "needs_review");
  assert.deepEqual(seen, ["check", "no"]);
  assert.equal(
    await run(async () => {
      throw Error("设备截图失败");
    }),
    "error"
  );
  assert.deepEqual(seen, ["check"]);
  const variable = step("variable", {
    type: "variable_assert",
    prompt: "变量",
    options: { variable: "value", operator: "ne", expectedValue: "1" },
  });
  assert.equal(
    await executeStep(null, variable, { value: "2" }, null),
    "passed"
  );
  assert.equal(
    await executeStep(null, variable, { value: "1" }, null),
    "failed"
  );
});
