import sharp from "sharp";

export async function decodeImage(image, colorMode) {
  let pipeline = sharp(image, { limitInputPixels: 16_000_000 }).toColourspace("srgb");
  if (colorMode === "grayscale") pipeline = pipeline.greyscale();
  return pipeline.ensureAlpha().raw().toBuffer({ resolveWithObject: true });
}
