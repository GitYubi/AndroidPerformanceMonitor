import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

export type ScrcpyHandle = { stop: () => Promise<void> };
// WebCodecs is feature-detected; browsers without it keep inspection available.
export default forwardRef<
  ScrcpyHandle,
  {
    api: string;
    serial: string;
    observationId?: string;
    onDraft: (token: string) => void;
  }
>(function ScrcpyScreen({ api, serial, observationId, onDraft }, ref) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const socket = useRef<WebSocket | null>(null);
  const closed = useRef<Promise<void>>(Promise.resolve());
  const [message, setMessage] = useState("正在连接 scrcpy…");
  const [ready, setReady] = useState(false);
  const active = useRef(false);
  const draftCallback = useRef(onDraft);
  draftCallback.current = onDraft;
  useImperativeHandle(
    ref,
    () => ({
      stop: async () => {
        const ws = socket.current;
        if (ws?.readyState === WebSocket.OPEN)
          ws.send(JSON.stringify({ type: "stop" }));
        else if (ws?.readyState === WebSocket.CONNECTING) ws.close();
        await closed.current;
      },
    }),
    []
  );
  useEffect(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const codecs = window as any;
    if (!codecs.VideoDecoder) {
      setMessage(
        "当前浏览器不支持 WebCodecs，请使用新版 Chrome 或 Edge 操作设备。"
      );
      return;
    }
    let disposed = false;
    let configured = false;
    let config: Uint8Array | null = null;
    let resolveClosed = () => {};
    closed.current = new Promise<void>(resolve => {
      resolveClosed = resolve;
    });
    const ws = new WebSocket(
      `${api.replace(/^http/, "ws")}/api/ui/map/stream/${encodeURIComponent(serial)}`
    );
    socket.current = ws;
    ws.binaryType = "arraybuffer";
    const decoder = new codecs.VideoDecoder({
      output: (frame: any) => {
        const c = canvas.current;
        if (c && !disposed) {
          if (
            c.width !== frame.displayWidth ||
            c.height !== frame.displayHeight
          ) {
            c.width = frame.displayWidth;
            c.height = frame.displayHeight;
          }
          c.getContext("2d")?.drawImage(frame, 0, 0);
          setReady(true);
          setMessage("实时操作 · 切换到选择元素后保存");
        }
        frame.close();
      },
      error: (error: Error) => {
        if (!disposed) setMessage(`视频解码失败：${error.message}`);
        ws.send(JSON.stringify({ type: "stop" }));
      },
    });
    ws.onopen = () => ws.send(JSON.stringify({ observationId }));
    ws.onmessage = event => {
      if (disposed) return;
      if (typeof event.data === "string") {
        const data = JSON.parse(event.data);
        if (data.error) {
          setMessage(data.error);
          setReady(false);
        }
        if (data.ready) draftCallback.current(data.token);
        return;
      }
      try {
        const bytes = new Uint8Array(event.data);
        const view = new DataView(event.data);
        if (bytes.length < 12) return;
        const flags = view.getUint32(0);
        const timestamp = (flags & 0x3fffffff) * 4294967296 + view.getUint32(4);
        const payload = bytes.slice(12);
        if (flags & 0x80000000) {
          config = payload;
          for (let i = 0; i + 7 < payload.length; i++) {
            let n = -1;
            if (
              payload[i] === 0 &&
              payload[i + 1] === 0 &&
              payload[i + 2] === 1
            )
              n = i + 3;
            if (n >= 0 && (payload[n] & 31) === 7) {
              const codec =
                "avc1." +
                Array.from(payload.slice(n + 1, n + 4))
                  .map(v => v.toString(16).padStart(2, "0"))
                  .join("");
              decoder.configure({ codec, optimizeForLatency: true });
              configured = true;
              break;
            }
          }
          return;
        }
        if (!configured) return;
        const key = !!(flags & 0x40000000);
        let data = payload;
        if (key && config) {
          data = new Uint8Array(config.length + payload.length);
          data.set(config);
          data.set(payload, config.length);
        }
        if (decoder.decodeQueueSize > 8) {
          decoder.reset();
          configured = false;
          ws.send(JSON.stringify({ type: "stop" }));
          setMessage("视频处理跟不上，已停止会话，请重新进入操作模式。");
          return;
        }
        decoder.decode(
          new codecs.EncodedVideoChunk({
            type: key ? "key" : "delta",
            timestamp: timestamp,
            data,
          })
        );
      } catch (error) {
        setMessage(`视频解码失败：${String(error)}`);
        setReady(false);
        if (ws.readyState === WebSocket.OPEN)
          ws.send(JSON.stringify({ type: "stop" }));
      }
    };
    ws.onerror = () => {
      if (!disposed) setMessage("scrcpy 连接失败，请确认设备在线及后端运行。");
    };
    ws.onclose = () => {
      resolveClosed();
      active.current = false;
      if (!disposed) {
        setReady(false);
        setMessage(m => (m.startsWith("实时操作") ? "操作连接已关闭" : m));
      }
    };
    return () => {
      disposed = true;
      if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: "stop" }));
      else ws.close();
      decoder.close();
    };
  }, [api, serial, observationId]);
  function touch(e: React.PointerEvent<HTMLCanvasElement>, action: number) {
    const c = canvas.current,
      ws = socket.current;
    if (!c || !ready || ws?.readyState !== WebSocket.OPEN) return;
    if (action === 0) {
      if (e.button !== 0) return;
      active.current = true;
      c.setPointerCapture(e.pointerId);
    }
    if (!active.current) return;
    const r = c.getBoundingClientRect();
    const x = Math.min(
      c.width - 1,
      Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * c.width))
    );
    const y = Math.min(
      c.height - 1,
      Math.max(0, Math.floor(((e.clientY - r.top) / r.height) * c.height))
    );
    ws.send(
      JSON.stringify({
        type: "touch",
        action,
        x,
        y,
        width: c.width,
        height: c.height,
      })
    );
    if (action === 1 || action === 3) active.current = false;
  }
  return (
    <div className="space-y-2">
      <p role="status" className="text-sm text-muted-foreground">
        {message}
      </p>
      <canvas
        ref={canvas}
        width={540}
        height={960}
        aria-label="scrcpy 实时设备画面"
        className="block h-auto w-auto max-h-[65vh] max-w-full rounded border bg-black touch-none"
        onContextMenu={e => e.preventDefault()}
        onPointerDown={e => touch(e, 0)}
        onPointerMove={e => touch(e, 2)}
        onPointerUp={e => touch(e, 1)}
        onPointerCancel={e => touch(e, 3)}
      />
    </div>
  );
});
