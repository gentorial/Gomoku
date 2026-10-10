import websocket from "@fastify/websocket";
import type { FastifyInstance } from "fastify";
import type { OnlineService } from "./online.js";

/**
 * Serves online play at /ws. Browser connections must come from an allowed origin
 * (clients without an Origin header, such as tests, are accepted). Each socket may
 * send small messages at a bounded rate, and silent sockets are dropped.
 */
export function registerOnline(
  app: FastifyInstance,
  online: OnlineService,
  origins: readonly string[],
) {
  app.register(websocket, { options: { maxPayload: 4096 } });
  app.register(async (scope) => {
    scope.get("/ws", { websocket: true }, (socket, request) => {
      const origin = request.headers.origin;
      if (origin && !origins.includes(origin)) return socket.close(1008, "origin not allowed");
      const session = online.connect({
        send: (message) => {
          if (socket.readyState === socket.OPEN) socket.send(JSON.stringify(message));
        },
        close: () => socket.close(4000, "connected elsewhere"),
      });
      // Token bucket: bursts of 20 messages, refilled at 10 per second.
      let tokens = 20;
      let last = Date.now();
      socket.on("message", (data) => {
        const now = Date.now();
        tokens = Math.min(20, tokens + (now - last) / 100);
        last = now;
        if (tokens < 1) return;
        tokens -= 1;
        let message: unknown = null;
        try {
          message = JSON.parse(String(data));
        } catch {
          /* Rejected by the schema below. */
        }
        void session.receive(message);
      });
      let alive = true;
      socket.on("pong", () => (alive = true));
      const heartbeat = setInterval(() => {
        if (!alive) return socket.terminate();
        alive = false;
        socket.ping();
      }, 30_000);
      socket.on("close", () => {
        clearInterval(heartbeat);
        session.closed();
      });
    });
  });
  const sweeper = setInterval(() => online.sweep(), 60_000);
  sweeper.unref();
  app.addHook("onClose", async () => clearInterval(sweeper));
}
