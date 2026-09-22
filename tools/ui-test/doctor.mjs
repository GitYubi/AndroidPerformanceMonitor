import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { loadConfig, publicConfig } from "./config.mjs";

loadConfig();
const checks = {
  node: process.version,
  model: publicConfig(),
  midscene: false,
  adb: false,
  devices: [],
};
try {
  createRequire(import.meta.url).resolve("@midscene/android");
  checks.midscene = true;
} catch {
  checks.dependencyError = "请先在 tools/ui-test 运行 npm ci";
}
try {
  checks.adbVersion = execFileSync("adb", ["version"], {
    encoding: "utf8",
    timeout: 10000,
  }).trim();
  const output = execFileSync("adb", ["devices", "-l"], {
    encoding: "utf8",
    timeout: 10000,
  });
  checks.devices = output
    .split("\n")
    .slice(1)
    .filter(line => line.trim())
    .map(line => {
      const [serial, state, ...details] = line.trim().split(/\s+/);
      return {
        serial,
        state,
        details: details.join(" "),
        emulator: serial.startsWith("emulator-"),
      };
    });
  checks.adb = true;
} catch {
  checks.adbError = "ADB 检查失败，请检查本机 ADB 服务和 USB 授权";
}
console.log(JSON.stringify(checks, null, 2));
process.exitCode =
  checks.adb && checks.midscene && checks.model.keyConfigured ? 0 : 1;
