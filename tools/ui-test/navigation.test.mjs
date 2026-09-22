import test from "node:test";
import assert from "node:assert/strict";
import {
  NavigationMemory,
  navigationTarget,
  pageKey,
  pointSelector,
} from "./navigation.mjs";
const tree = name => ({
  nodes: [
    {
      index: 0,
      parent: null,
      package: "demo",
      className: "Button",
      resourceId: `id/${name}`,
      label: name,
      text: name,
      description: "",
      selected: false,
      enabled: true,
      clickable: true,
      checkable: false,
      bounds: [0, 0, 100, 100],
    },
  ],
});
const a = tree("设置"),
  b = tree("显示"),
  c = tree("意外弹窗");
const selector = pointSelector(a, { x: 20, y: 20 });
function setup(knowledge = {}, seeds = {}) {
  let current = a,
    taps = 0;
  const events = [];
  const device = {
    read: async () => current,
    physicalSize: async () => ({ width: 100, height: 100 }),
    tap: async (_, validate) => {
      validate?.(current);
      taps++;
      current = b;
    },
  };
  const memory = new NavigationMemory({
    knowledge,
    seeds,
    device,
    emit: e => events.push(e),
  });
  return {
    memory,
    device,
    events,
    get taps() {
      return taps;
    },
    set current(t) {
      current = t;
    },
    get current() {
      return current;
    },
  };
}
const goal = { source: pageKey(a), target: pageKey(b), prompt: "进入显示页面" };
const edge = { from: pageKey(a), to: pageKey(b), selector };
test("only unambiguous navigation instructions qualify; toggle and compound commands remain AI tasks", () => {
  assert.equal(navigationTarget("进入显示页面"), "显示");
  assert.equal(navigationTarget("打开显示页面"), "显示");
  for (const p of [
    "打开蓝牙",
    "进入显示页面，然后关闭亮度",
    "打开显示页面并关闭开关",
    "点击显示",
    "关闭蓝牙",
  ])
    assert.equal(navigationTarget(p), null);
});
test("actual successful tap learns a path; next run reuses it with no model call", async () => {
  const first = setup();
  let calls = 0;
  assert.equal(
    await first.memory.run(
      goal.prompt,
      async () => {
        calls++;
        await first.memory.observeTap(
          { x: 10, y: 10 },
          { width: 100, height: 100 },
          async () => {
            first.current = b;
          }
        );
        return "passed";
      },
      async () => true
    ),
    "passed"
  );
  assert.equal(calls, 1);
  const knowledge = first.events.find(
    e => e.type === "navigation_update"
  ).knowledge;
  assert.equal(knowledge.edges.length, 1);
  const second = setup(knowledge);
  assert.equal(
    await second.memory.run(
      goal.prompt,
      () => {
        throw Error("must not call model");
      },
      () => {
        throw Error("must not verify through model");
      }
    ),
    "passed"
  );
  assert.equal(second.taps, 1);
  assert.equal(second.events.at(-1).aiRequests, 0);
  assert.equal(second.events.at(-1).hits, 1);
});
test("unconfirmed AI result is not learned or marked successful", async () => {
  const s = setup();
  assert.equal(
    await s.memory.run(
      goal.prompt,
      async () => {
        await s.memory.observeTap(
          { x: 10, y: 10 },
          { width: 100, height: 100 },
          async () => {
            s.current = b;
          }
        );
        return "passed";
      },
      async () => false
    ),
    "needs_review"
  );
  assert.equal(s.events[0].knowledge.edges.length, 0);
  assert.equal(s.events[0].knowledge.goals.length, 0);
});
test("wrong landing invalidates edge and falls back from the actual current screen", async () => {
  const s = setup({ edges: [edge], goals: [goal] });
  let calls = 0;
  s.device.tap = async () => {
    s.current = c;
  };
  await s.memory.run(
    goal.prompt,
    async () => {
      assert.equal(s.current, c);
      calls++;
      s.current = b;
      return "passed";
    },
    async () => true
  );
  assert.equal(calls, 1);
  assert.equal(s.events[0].knowledge.edges[0].stale, true);
});
test("uncertain input failure stops without AI retry", async () => {
  const s = setup({ edges: [{ ...edge }], goals: [goal] });
  let calls = 0;
  s.device.tap = async () => {
    throw Error("outcome unknown");
  };
  await assert.rejects(
    s.memory.run(
      goal.prompt,
      async () => {
        calls++;
      },
      async () => true
    )
  );
  assert.equal(calls, 0);
});
test("duplicate controls do not become replayable locators", () => {
  assert.equal(
    pointSelector(
      { nodes: [a.nodes[0], { ...a.nodes[0], index: 1 }] },
      { x: 10, y: 10 }
    ),
    null
  );
});
test("manual saved tree seeds can be used immediately", async () => {
  const s = setup(
    {},
    {
      pages: [
        { id: "a", name: "设置", ...a },
        { id: "b", name: "显示", ...b },
      ],
      edges: [{ from: "a", to: "b", selector }],
    }
  );
  await s.memory.run(
    goal.prompt,
    () => {
      throw Error("unexpected AI");
    },
    async () => true
  );
  assert.equal(s.taps, 1);
});
test("stale manual edge stays invalid across runs", async () => {
  const seeds = {
    pages: [
      { id: "a", name: "设置", ...a },
      { id: "b", name: "显示", ...b },
    ],
    edges: [{ from: "a", to: "b", selector }],
  };
  const s = setup({}, seeds);
  s.device.tap = async () => {
    s.current = c;
  };
  await s.memory.run(
    goal.prompt,
    async () => {
      s.current = b;
      return "passed";
    },
    async () => true
  );
  const second = setup(s.events[0].knowledge, seeds);
  let called = 0;
  await second.memory.run(
    goal.prompt,
    async () => {
      called++;
      second.current = b;
      return "passed";
    },
    async () => true
  );
  assert.equal(called, 1);
  assert.equal(second.taps, 0);
});
test("selected tabs remain distinct while volatile summary values do not", () => {
  assert.notEqual(
    pageKey(a),
    pageKey({ nodes: [{ ...a.nodes[0], selected: true }] })
  );
  assert.equal(
    pageKey({
      nodes: [{ ...a.nodes[0], resourceId: "id/summary", text: "1小时" }],
    }),
    pageKey({
      nodes: [{ ...a.nodes[0], resourceId: "id/summary", text: "2小时" }],
    })
  );
});
test("non-navigation tasks bypass tree reads and verification", async () => {
  const s = setup();
  s.device.read = async () => {
    throw Error("must not read");
  };
  assert.equal(
    await s.memory.run(
      "关闭自动亮度",
      async () => "passed",
      async () => {
        throw Error("must not verify");
      }
    ),
    "passed"
  );
  assert.equal(s.events.length, 0);
});
test("installed Midscene Android Tap action traverses the observer exactly once", async () => {
  const { AndroidDevice } = await import("@midscene/android");
  const { attachNavigationObserver } = await import("./navigation.mjs");
  const device = new AndroidDevice("fake");
  let taps = 0,
    observations = 0;
  device.size = async () => ({ width: 100, height: 100 });
  device.inputPrimitives.pointer.tap = async p => {
    assert.deepEqual(p, { x: 12, y: 34 });
    taps++;
  };
  const memory = {
    collecting: true,
    observeTap: async (point, size, invoke) => {
      observations++;
      return invoke();
    },
  };
  assert.equal(attachNavigationObserver(device, memory), true);
  await device
    .actionSpace()
    .find(a => a.name === "Tap")
    .call({ locate: { center: [12, 34] } });
  assert.equal(taps, 1);
  assert.equal(observations, 1);
});
