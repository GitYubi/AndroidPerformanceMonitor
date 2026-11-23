import { useEffect, useRef, useState, type PointerEvent } from "react";
import type { ImageRegion } from "./uiImages";
import { screenPixel, type Observation } from "./uiObservation";

type Frame = Pick<Observation, "image" | "width" | "height">;

export function useImageRegion(frame: Frame | null, enabled: boolean) {
  const [region, setRegion] = useState<ImageRegion | null>(null);
  const start = useRef<{ x: number; y: number; pointerId: number } | null>(
    null
  );
  function reset() {
    start.current = null;
    setRegion(null);
  }
  useEffect(reset, [frame, enabled]);

  function pixel(event: PointerEvent<HTMLImageElement>) {
    return (
      frame &&
      screenPixel(
        frame,
        event.currentTarget.getBoundingClientRect(),
        event.clientX,
        event.clientY
      )
    );
  }
  function startRegion(event: PointerEvent<HTMLImageElement>) {
    if (!enabled || !event.isPrimary || event.button !== 0) return;
    const point = pixel(event);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    start.current = { ...point, pointerId: event.pointerId };
    setRegion(null);
  }
  function drawRegion(event: PointerEvent<HTMLImageElement>) {
    const origin = start.current;
    if (!enabled || origin?.pointerId !== event.pointerId) return;
    const end = pixel(event);
    if (end)
      setRegion(
        origin.x === end.x && origin.y === end.y
          ? null
          : [
              Math.min(origin.x, end.x),
              Math.min(origin.y, end.y),
              Math.max(origin.x, end.x) + 1,
              Math.max(origin.y, end.y) + 1,
            ]
      );
    if (event.type === "pointerup") start.current = null;
  }
  function cancelRegion(event: PointerEvent<HTMLImageElement>) {
    if (start.current?.pointerId === event.pointerId) reset();
  }
  return { region, startRegion, drawRegion, cancelRegion };
}
