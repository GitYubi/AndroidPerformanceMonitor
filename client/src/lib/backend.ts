// Portable builds use the launcher's origin, including its selected port.
const configured = import.meta.env.VITE_BACKEND_URL;
export const BACKEND_URL = (
  configured === "same-origin"
    ? window.location.origin
    : configured || "http://127.0.0.1:8090"
).replace(/\/$/, "");
