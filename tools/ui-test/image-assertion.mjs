import { Worker } from "node:worker_threads";
import sharp from "sharp";
import { decodeImage } from "./image-pixels.mjs";

export async function searchScreenshot(
  actual,
  baseline,
  pixelTolerance,
  minSimilarity,
  colorMode = "grayscale",
  signal
) {
  signal?.throwIfAborted();
  // Keep worst-case template matching off the runner's deadline timer thread.
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./image-search-worker.mjs", import.meta.url), {
      workerData: [actual, baseline, pixelTolerance, minSimilarity, colorMode],
      execArgv: [],
    });
    const abort = () => {
      void worker.terminate();
      reject(signal.reason);
    };
    signal?.addEventListener("abort", abort, { once: true });
    worker.once("message", resolve);
    worker.once("error", reject);
    worker.once("exit", code => {
      signal?.removeEventListener("abort", abort);
      if (code !== 0) reject(new Error(`图像搜索进程退出（${code}）`));
    });
  });
}

export async function cropScreenshot(image, { left, top, right, bottom }) {
  const png = sharp(image, { limitInputPixels: 16_000_000 });
  const { width, height } = await png.metadata();
  if (right > width || bottom > height) {
    return { message: `ROI 超出截图范围（${width}×${height}）` };
  }
  return {
    image: await png
      .extract({ left, top, width: right - left, height: bottom - top })
      .png()
      .toBuffer(),
  };
}

export async function compareScreenshots(
  actual,
  baseline,
  pixelTolerance,
  baselineLabel = "基准图",
  colorMode = "color"
) {
  const [current, expected] = await Promise.all([
    decodeImage(actual, colorMode),
    decodeImage(baseline, colorMode),
  ]);
  const { width, height } = expected.info;
  if (current.info.width !== width || current.info.height !== height)
    return {
      similarity: null,
      message: `截图尺寸 ${current.info.width}×${current.info.height}，${baselineLabel} ${width}×${height}`,
    };
  let matchingPixels = 0;
  const channels = expected.info.channels;
  for (let i = 0; i < current.data.length; i += channels) {
    let channel = 0;
    while (
      channel < channels &&
      Math.abs(current.data[i + channel] - expected.data[i + channel]) <=
        pixelTolerance
    )
      channel++;
    if (channel === channels) matchingPixels++;
  }
  return { similarity: (matchingPixels * 100) / (width * height) };
}
