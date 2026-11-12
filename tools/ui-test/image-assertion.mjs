import sharp from "sharp";

async function decode(image, colorMode) {
  let pipeline = sharp(image, { limitInputPixels: 16_000_000 }).toColourspace(
    "srgb"
  );
  if (colorMode === "grayscale") pipeline = pipeline.greyscale();
  return pipeline.ensureAlpha().raw().toBuffer({ resolveWithObject: true });
}

export async function searchScreenshot(
  actual,
  baseline,
  pixelTolerance,
  minSimilarity,
  colorMode = "grayscale"
) {
  const [screen, template] = await Promise.all([
    decode(actual, colorMode),
    decode(baseline, colorMode),
  ]);
  const { width, height, channels } = template.info;
  if (width > screen.info.width || height > screen.info.height)
    return {
      similarity: null,
      message: `基准图 ${width}×${height} 超出截图 ${screen.info.width}×${screen.info.height}`,
    };
  const maxMismatch = Math.floor(
    (width * height * (100 - minSimilarity)) / 100 + 1e-9
  );
  for (let y = 0; y <= screen.info.height - height; y++) {
    for (let x = 0; x <= screen.info.width - width; x++) {
      let mismatches = 0;
      for (let row = 0; row < height && mismatches <= maxMismatch; row++) {
        for (let col = 0; col < width && mismatches <= maxMismatch; col++) {
          const a = ((y + row) * screen.info.width + x + col) * channels;
          const b = (row * width + col) * channels;
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
      }
      if (mismatches <= maxMismatch)
        return { similarity: 100 * (1 - mismatches / (width * height)), x, y };
    }
  }
  return { similarity: null, message: "全屏未找到满足相似度阈值的基准图" };
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
    decode(actual, colorMode),
    decode(baseline, colorMode),
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
