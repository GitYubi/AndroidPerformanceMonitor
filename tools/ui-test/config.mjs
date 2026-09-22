import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

export function loadConfig() {
  const path = fileURLToPath(new URL(".env", import.meta.url));
  if (existsSync(path)) process.loadEnvFile(path);
  process.env.MIDSCENE_MODEL_BASE_URL ||= "https://api.deepseek.com";
  process.env.MIDSCENE_MODEL_NAME ||= "deepseek-flash";
  process.env.MIDSCENE_MODEL_FAMILY ||= "deepseek";
}

export function publicConfig() {
  return {
    baseUrl: process.env.MIDSCENE_MODEL_BASE_URL,
    model: process.env.MIDSCENE_MODEL_NAME,
    family: process.env.MIDSCENE_MODEL_FAMILY,
    keyConfigured: Boolean(process.env.MIDSCENE_MODEL_API_KEY?.trim()),
  };
}
