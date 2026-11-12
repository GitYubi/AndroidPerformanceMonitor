import test from "node:test";
import assert from "node:assert/strict";
import {
  validateJob,
  substitute,
  executeStep,
  stepDeadlineMs,
} from "./protocol.mjs";
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
test("fixed delays run locally and have a deadline beyond their duration", async () => {
  const delay = {
    id: "delay",
    type: "delay",
    prompt: "等待时间",
    timeoutMs: 1000,
    options: { durationMs: 1500 },
  };
  const local = { serial: "", testCase: { schemaVersion: 1, steps: [delay] } };
  validateJob(local);
  assert.ok(stepDeadlineMs(delay) > delay.options.durationMs);
  delay.options.durationMs = 0;
  assert.throws(() => validateJob(local), /等待时长/);
  delay.options.durationMs = 30;
  const began = performance.now();
  assert.equal(await executeStep(null, delay, {}, null), "passed");
  assert.ok(performance.now() - began >= 29);
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
      colorMode: "color",
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
    "passed"
  );
});

test("global image search finds a template anywhere and supports grayscale and color", async () => {
  const { default: sharp } = await import("sharp");
  const png = async (r, g, b) =>
    sharp({
      create: { width: 1, height: 1, channels: 3, background: { r, g, b } },
    })
      .png()
      .toBuffer();
  const baseline = await png(255, 0, 0);
  const screen = await sharp({
    create: { width: 3, height: 2, channels: 3, background: "#000000" },
  })
    .composite([{ input: baseline, left: 2, top: 1 }])
    .png()
    .toBuffer();
  const check = {
    ...step,
    prompt: "全屏搜索",
    type: "image_assert",
    options: {
      baselineId: "a".repeat(64),
      minSimilarity: 100,
      pixelTolerance: 0,
      mode: "screen",
      colorMode: "color",
    },
  };
  const io = { captureImage: async () => screen, loadBaseline: () => baseline };
  assert.equal(await executeStep(null, check, {}, null, io), "passed");
  io.loadBaseline = () => png(0, 0, 255);
  assert.equal(await executeStep(null, check, {}, null, io), "failed");
  const gray = await sharp(baseline).greyscale().png().toBuffer();
  io.loadBaseline = () => gray;
  check.options.colorMode = "grayscale";
  assert.equal(await executeStep(null, check, {}, null, io), "passed");
  check.options.colorMode = "color";
  assert.equal(await executeStep(null, check, {}, null, io), "failed");
});

test("screenshot assertions compare two earlier captures without reading the screen again", async () => {
  const { default: sharp } = await import("sharp");
  const png = (r, width = 1) =>
    sharp({
      create: { width, height: 1, channels: 3, background: { r, g: 0, b: 0 } },
    })
      .png()
      .toBuffer();
  const before = {
    ...step,
    id: "before",
    type: "screenshot",
    prompt: "截图",
    options: { captureName: "图片1" },
  };
  const after = { ...before, id: "after", options: null };
  const check = {
    ...step,
    type: "screenshot_assert",
    options: { firstStepId: before.id, secondStepId: after.id },
  };
  const j = job();
  j.testCase.steps = [before, check, after];
  assert.throws(() => validateJob(j), /前面已启用/);
  j.testCase.steps = [before, after, check];
  validateJob(j);
  const first = await png(20);
  const recorded = [];
  const agent = { recordToReport: (_, report) => recorded.push(report) };
  await executeStep(agent, before, {}, null, {
    captureImage: async () => first,
  });
  await executeStep(agent, before, {}, null, {
    captureImage: async () => first,
    sensitive: true,
  });
  assert.deepEqual(recorded, [
    {
      screenshots: [{ base64: first.toString("base64"), description: "图片1" }],
    },
  ]);
  let second = await png(20);
  let message;
  const io = {
    loadScreenshot: id => (id === before.id ? first : second),
    captureImage: () => assert.fail("comparison must reuse captured images"),
    onResult: value => {
      message = value;
    },
  };
  const compare = () => executeStep(null, check, j.parameters, null, io);
  assert.equal(await compare(), "passed");
  check.options.relation = "different";
  assert.equal(await compare(), "failed");
  second = await png(21);
  assert.equal(await compare(), "passed");
  check.options.pixelTolerance = 1;
  assert.equal(await compare(), "failed");
  second = await png(20, 2);
  assert.equal(await compare(), "passed");
  assert.match(message, /尺寸不同/);
  second = undefined;
  assert.equal(await compare(), "error");
});

test("ADB ROI and element captures expose only the selected PNG to the run", async () => {
  const { default: sharp } = await import("sharp");
  const screen = await sharp({
    create: { width: 8, height: 6, channels: 3, background: "red" },
  })
    .png()
    .toBuffer();
  const selector = { resourceId: "demo:id/control" };
  let captured;
  const io = {
    captureImage: async () => screen,
    onImage: image => {
      captured = image;
    },
  };
  const snapshot = {
    ...step,
    prompt: "截图",
    type: "screenshot",
    options: {
      mode: "roi",
      roi: { left: 1, top: 2, right: 5, bottom: 5 },
    },
  };
  assert.equal(await executeStep(null, snapshot, {}, null, io), "passed");
  const cropped = await sharp(captured).metadata();
  assert.deepEqual([cropped.width, cropped.height], [4, 3]);
  snapshot.options.roi.right = 10;
  assert.equal(await executeStep(null, snapshot, {}, null, io), "error");
  assert.equal(captured, null);

  assert.equal(
    await executeStep(
      null,
      { ...snapshot, type: "element_screenshot", selector, options: null },
      {},
      null,
      {
        ...io,
        captureImage: () =>
          assert.fail("element capture should not take an ADB screenshot"),
        captureElement: async target => {
          assert.deepEqual(target, selector);
          return { image: screen };
        },
      }
    ),
    "passed"
  );
  assert.equal(captured, screen);
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

test("global image search matches exhaustive comparison across colors and thresholds", async () => {
  const { default: sharp } = await import("sharp");
  const { searchScreenshot, compareScreenshots } = await import(
    "./image-assertion.mjs"
  );
  let seed = 731;
  const random = () => (seed = (seed * 1664525 + 1013904223) >>> 0);
  for (const colorMode of ["grayscale", "color"]) {
    for (const threshold of [0, 50, 80, 99, 100]) {
      for (let trial = 0; trial < 5; trial++) {
        const bytes = Buffer.alloc(7 * 6 * 3);
        for (let i = 0; i < bytes.length; i++) bytes[i] = random() >>> 24;
        const screen = await sharp(bytes, {
          raw: { width: 7, height: 6, channels: 3 },
        })
          .png()
          .toBuffer();
        const left = random() % 5,
          top = random() % 5;
        const template = await sharp(screen)
          .extract({ left, top, width: 3, height: 2 })
          .png()
          .toBuffer();
        let expected = null;
        for (let y = 0; y < 5 && !expected; y++) {
          for (let x = 0; x < 5; x++) {
            const candidate = await sharp(screen)
              .extract({ left: x, top: y, width: 3, height: 2 })
              .png()
              .toBuffer();
            const result = await compareScreenshots(
              candidate,
              template,
              16,
              "基准图",
              colorMode
            );
            if (result.similarity >= threshold) {
              expected = { x, y, similarity: result.similarity };
              break;
            }
          }
        }
        const actual = await searchScreenshot(
          screen,
          template,
          16,
          threshold,
          colorMode
        );
        assert.equal(actual.found, Boolean(expected));
        assert.equal(actual.x, expected?.x);
        assert.equal(actual.y, expected?.y);
        if (expected)
          assert.ok(Math.abs(actual.similarity - expected.similarity) < 1e-9);
      }
    }
  }
});
