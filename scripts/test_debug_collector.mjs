import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { redactDebugField } from "../client/public/__manus__/debug-redaction.mjs";

test("browser debug uploads and log serialization redact synthetic credentials", async () => {
  const posted = [];
  const console = Object.fromEntries(["log", "debug", "info", "warn", "error"].map(name => [name, () => {}]));
  const listeners = new Map();
  const window = {
    addEventListener(name, fn) { listeners.set(name, fn); },
    fetch: async (url, init) => {
      if (url === "/__manus__/logs") {
        posted.push(init.body);
        return new Response(null);
      }
      return new Response(JSON.stringify({ token: "fixture-response-body", status: "healthy" }), {
        headers: { "Content-Type": "application/json", "Set-Cookie": "fixture-response-header" },
      });
    },
  };
  const source = readFileSync(new URL("../client/public/__manus__/debug-collector.js", import.meta.url), "utf8");
  assert.ok(source.startsWith('import { redactDebugField } from "./debug-redaction.mjs";\n'));
  runInNewContext(source.slice(source.indexOf("\n") + 1), {
    window, console, Headers, URL, redactDebugField, XMLHttpRequest: class {},
    document: { addEventListener() {} }, setInterval() {},
    navigator: { sendBeacon(_url, body) { posted.push(body); } },
  });
  const secret = "/api/example?token=fixture-query&opaque=fixture-opaque";
  await window.fetch(secret, {
    method: "POST",
    headers: { Authorization: "Bearer fixture-auth", "X-Private": "fixture-header" },
    body: JSON.stringify({ token: "fixture-field", parameters: { PIN: "fixture-secret-value" } }),
  });
  for (let attempt = 0; attempt < 10 && !window.__MANUS_DEBUG_COLLECTOR__.store.networkRequests.length; attempt++)
    await new Promise(resolve => setImmediate(resolve));
  console.error("failed " + secret + " token=fixture-freeform");
  listeners.get("beforeunload")();
  await window.__MANUS_DEBUG_COLLECTOR__.forceReport();
  assert.equal(posted.length, 2);
  for (const body of posted) {
    assert.doesNotMatch(body, /fixture-(query|opaque|auth|header|field|response|secret|freeform)/);
    const [event] = JSON.parse(body).networkRequests;
    assert.equal(event.url, "/api/example");
    assert.equal(event.method, "POST");
    assert.equal(event.response.status, 200);
    assert.equal(event.response.body.status, "healthy");
    assert.equal(event.response.headers["content-type"], "application/json");
  }
  const direct = JSON.stringify({ url: secret, metadata: { apiKey: "fixture-direct" } }, redactDebugField);
  assert.doesNotMatch(direct, /fixture-/);
});
