import test from "node:test";
import assert from "node:assert/strict";
import { validateJob, substitute, executeStep } from "./protocol.mjs";
const step = {
  id: "check",
  type: "assert",
  prompt: "已连接 ${SSID}",
  timeoutMs: 10000,
};
const job = () => ({
  serial: "emulator-5554",
  testCase: { schemaVersion: 1, steps: [{ ...step }] },
  parameters: { SSID: "test" },
});
test("rejects duplicate step IDs and unknown parameters before any action", () => {
  const j = job();
  j.testCase.steps.push({ ...step });
  assert.throws(() => validateJob(j), /重复/);
  assert.throws(() => validateJob({ ...job(), parameters: {} }), /缺少参数/);
});
test("parameter values are substituted literally, including replacement tokens", () => {
  assert.equal(substitute("${SSID}", { SSID: "$&${OTHER}" }), "$&${OTHER}");
});
test("false assertion differs from model transport failure", async () => {
  assert.equal(
    await executeStep({ aiBoolean: async () => false }, step, { SSID: "test" }),
    "failed"
  );
  await assert.rejects(
    executeStep(
      {
        aiBoolean: async () => {
          throw new Error("network");
        },
      },
      step,
      { SSID: "test" }
    ),
    /network/
  );
});
test("input uses locate-first API and preserves input value", async () => {
  let args;
  await executeStep(
    {
      aiInput: async (...values) => {
        args = values;
      },
    },
    { ...step, type: "input", value: "${PASSWORD}" },
    { SSID: "test", PASSWORD: "0011" }
  );
  assert.deepEqual(args, ["已连接 test", { value: "0011", mode: "replace" }]);
});
test("assertion receives both QUERY substitutions, not template placeholders", async () => {
  let received;
  const result = await executeStep(
    {
      aiBoolean: async prompt => {
        received = prompt;
        return false;
      },
    },
    {
      ...step,
      prompt: "设置搜索框中显示 ${QUERY}，搜索结果中有 ${QUERY} 相关设置",
    },
    { QUERY: "蓝牙" }
  );
  assert.equal(received, "设置搜索框中显示 蓝牙，搜索结果中有 蓝牙 相关设置");
  assert.equal(result, "failed"); // Preserve the model result; never turn a false assertion into success.
});
