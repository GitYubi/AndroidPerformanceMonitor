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

test("ADB coordinates validate before execution and remain numeric", async () => {
  const request = job();
  request.testCase.steps = [
    {
      ...step,
      type: "adb_swipe",
      options: { startX: 10, startY: 220, endX: 10, endY: 70, durationMs: 300 },
    },
  ];
  const valid = validateJob(request).testCase.steps[0];
  let received;
  assert.equal(
    await executeStep(null, valid, request.parameters, {
      swipeCoordinates: async options => {
        received = options;
      },
    }),
    "passed"
  );
  assert.deepEqual(received, valid.options);
  valid.options.startX = "0; input keyevent 3";
  assert.throws(() => validateJob(request), /坐标或时长无效/);
});

test("element assertions distinguish absence, disabled nodes, and ambiguity", async () => {
  const check = {
    ...step,
    type: "element_assert",
    selector: { resourceId: "demo:id/switch" },
    options: { expected: "disabled" },
  };
  const node = { resourceId: "demo:id/switch", enabled: false };
  let nodes = [node];
  const device = { read: async () => ({ nodes }) };
  assert.equal(
    await executeStep(null, check, { SSID: "test" }, device),
    "passed"
  );
  nodes = [node, node];
  assert.equal(
    await executeStep(null, check, { SSID: "test" }, device),
    "failed"
  );
  nodes = [];
  check.options.expected = "absent";
  assert.equal(
    await executeStep(null, check, { SSID: "test" }, device),
    "passed"
  );
});

test("image assertions evaluate pixel tolerance and dimension mismatch", async () => {
  const { default: sharp } = await import("sharp");
  const make = (value, width = 1) =>
    sharp({
      create: {
        width,
        height: 1,
        channels: 3,
        background: { r: value, g: 0, b: 0 },
      },
    })
      .png()
      .toBuffer();
  const baseline = await make(20);
  let actual = await make(30);
  const check = {
    ...step,
    type: "image_assert",
    options: {
      baselineId: "a".repeat(64),
      minSimilarity: 100,
      pixelTolerance: 10,
    },
  };
  const io = { captureImage: async () => actual, loadBaseline: () => baseline };
  assert.equal(
    await executeStep(null, check, { SSID: "test" }, null, io),
    "passed"
  );
  check.options.pixelTolerance = 9;
  assert.equal(
    await executeStep(null, check, { SSID: "test" }, null, io),
    "failed"
  );
  actual = await make(20, 2);
  assert.equal(
    await executeStep(null, check, { SSID: "test" }, null, io),
    "failed"
  );
});

test("ROI assertions compare and report the cropped pixels, with bounds checked", async () => {
  const { default: sharp } = await import("sharp");
  const screen = await sharp(Buffer.from([255, 0, 0, 0, 255, 0]), {
    raw: { width: 2, height: 1, channels: 3 },
  })
    .png()
    .toBuffer();
  const baseline = await sharp(screen)
    .extract({ left: 1, top: 0, width: 1, height: 1 })
    .png()
    .toBuffer();
  const check = {
    ...step,
    type: "image_assert",
    options: {
      baselineId: "a".repeat(64),
      minSimilarity: 100,
      pixelTolerance: 0,
      mode: "roi",
      roi: { left: 1, top: 0, right: 2, bottom: 1 },
    },
  };
  const request = job();
  request.testCase.steps = [check];
  validateJob(request);
  let image, message;
  const io = {
    captureImage: async () => screen,
    loadBaseline: () => baseline,
    onImage: value => {
      image = value;
    },
    onResult: value => {
      message = value;
    },
  };
  assert.equal(
    await executeStep(null, check, request.parameters, null, io),
    "passed"
  );
  assert.equal((await sharp(image).metadata()).width, 1);
  check.options.roi.right = 3;
  assert.equal(
    await executeStep(null, check, request.parameters, null, io),
    "failed"
  );
  assert.equal(image, null);
  assert.match(message, /ROI 超出截图范围/);
  check.options.roi.right = 1;
  assert.throws(() => validateJob(request), /ROI 四角坐标无效/);
});

test("element image assertions use only the element capture and surface lookup failures", async () => {
  const { default: sharp } = await import("sharp");
  const baseline = await sharp({
    create: { width: 1, height: 1, channels: 3, background: "#123456" },
  })
    .png()
    .toBuffer();
  const check = {
    ...step,
    type: "image_assert",
    selector: { resourceId: "demo:id/target" },
    options: {
      baselineId: "a".repeat(64),
      minSimilarity: 100,
      pixelTolerance: 0,
      mode: "element",
    },
  };
  const request = job();
  request.testCase.steps = [check];
  validateJob(request);
  let selector, message;
  const io = {
    captureImage: async () => assert.fail("unexpected full-screen capture"),
    captureElement: async locator => {
      selector = locator;
      return { image: baseline };
    },
    loadBaseline: () => baseline,
    onResult: value => {
      message = value;
    },
  };
  assert.equal(
    await executeStep(null, check, request.parameters, null, io),
    "passed"
  );
  assert.deepEqual(selector, check.selector);
  io.captureElement = async () => ({
    state: "failed",
    message: "未找到可截图的匹配元素",
  });
  assert.equal(
    await executeStep(null, check, request.parameters, null, io),
    "failed"
  );
  assert.match(message, /未找到/);
});
