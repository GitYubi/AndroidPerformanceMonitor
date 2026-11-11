import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const exec = promisify(execFile);
const helper = fileURLToPath(
  new URL("../../backend/app/ui_element_capture.py", import.meta.url)
);

export async function captureElement(python, serial, selector, timeoutMs) {
  try {
    const { stdout } = await exec(
      python,
      [helper, serial, JSON.stringify(selector)],
      {
        encoding: "buffer",
        maxBuffer: 32 * 1024 * 1024,
        timeout: timeoutMs,
        windowsHide: true,
      }
    );
    return { image: stdout };
  } catch (error) {
    if (error.code === 3)
      return { state: "failed", message: "未找到可截图的匹配元素" };
    if (error.code === 4)
      return { state: "failed", message: "匹配到多个元素，请补充定位条件" };
    return {
      state: "error",
      message:
        error.code === 2
          ? "当前 Python 环境缺少 uiautomator2 依赖"
          : "uiautomator2 元素截图失败，请检查设备连接和当前页面",
    };
  }
}
