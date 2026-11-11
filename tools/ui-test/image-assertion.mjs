import sharp from "sharp";

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

export async function compareScreenshots(actual, baseline, pixelTolerance) {
  const decode = image =>
    sharp(image, { limitInputPixels: 16_000_000 })
      .toColourspace("srgb")
      .ensureAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });
  const [current, expected] = await Promise.all([
    decode(actual),
    decode(baseline),
  ]);
  const { width, height } = expected.info;
  if (current.info.width !== width || current.info.height !== height)
    return {
      similarity: null,
      message: `截图尺寸 ${current.info.width}×${current.info.height}，基准图 ${width}×${height}`,
    };
  let matchingPixels = 0;
  for (let i = 0; i < current.data.length; i += 4) {
    let channel = 0;
    while (
      channel < 4 &&
      Math.abs(current.data[i + channel] - expected.data[i + channel]) <=
        pixelTolerance
    )
      channel++;
    if (channel === 4) matchingPixels++;
  }
  return { similarity: (matchingPixels * 100) / (width * height) };
}
