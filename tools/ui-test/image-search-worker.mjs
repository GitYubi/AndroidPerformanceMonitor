import { parentPort, workerData } from "node:worker_threads";
import { decodeImage } from "./image-pixels.mjs";

async function search(
  actual,
  baseline,
  pixelTolerance,
  minSimilarity,
  colorMode = "grayscale"
) {
  const [screen, template] = await Promise.all([
    decodeImage(Buffer.from(actual), colorMode),
    decodeImage(Buffer.from(baseline), colorMode),
  ]);
  const { width, height, channels } = template.info;
  if (width > screen.info.width || height > screen.info.height)
    return {
      similarity: null,
      found: false,
      message: `基准图 ${width}×${height} 超出截图 ${screen.info.width}×${screen.info.height}`,
    };
  const maxMismatch = Math.floor(
    (width * height * (100 - minSimilarity)) / 100 + 1e-9
  );
  // Check less common template pixels first. Every pixel is still checked
  // before accepting a match, and rejection only happens past the threshold.
  const frequency = new Uint32Array(256);
  for (let i = 0; i < template.data.length; i += channels)
    frequency[template.data[i]]++;
  const cursor = new Uint32Array(256);
  const orderedColors = Array.from(frequency.keys()).sort((a, b) => frequency[a] - frequency[b]);
  let offset = 0;
  for (const color of orderedColors) {
    cursor[color] = offset;
    offset += frequency[color];
  }
  const screenOffsets = new Uint32Array(width * height);
  const templateOffsets = new Uint32Array(width * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const index = (y * width + x) * channels;
      const position = cursor[template.data[index]]++;
      screenOffsets[position] = (y * screen.info.width + x) * channels;
      templateOffsets[position] = index;
    }
  }
  for (let y = 0; y <= screen.info.height - height; y++) {
    for (let x = 0; x <= screen.info.width - width; x++) {
      let mismatches = 0;
      const start = (y * screen.info.width + x) * channels;
      for (let i = 0; i < templateOffsets.length && mismatches <= maxMismatch; i++) {
        const a = start + screenOffsets[i];
        const b = templateOffsets[i];
        for (let channel = 0; channel < channels; channel++) {
          if (
            Math.abs(screen.data[a + channel] - template.data[b + channel]) >
            pixelTolerance
          ) {
            mismatches++;
            break;
          }
        }
      }
      if (mismatches <= maxMismatch)
        return {
          similarity: 100 * (1 - mismatches / (width * height)),
          found: true,
          x,
          y,
        };
    }
  }
  return {
    similarity: null,
    found: false,
    message: "全屏未找到满足相似度阈值的基准图",
  };
}

parentPort.postMessage(await search(...workerData));
