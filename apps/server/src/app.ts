import Fastify from "fastify";
import { CreateGameSchema, PlaySchema, VersionSchema } from "@gomoku/contracts";
import { ApiProblem, GameService } from "./games.js";
import { EngineError, WorkerPool, type Engine } from "./worker-pool.js";

export function createApp(engine: Engine = new WorkerPool(), logger = false) {
  const app = Fastify({ logger, bodyLimit: 65536, forceCloseConnections: true });
  const games = new GameService(engine);
  app.setErrorHandler((error, request, reply) => {
    if (error instanceof ApiProblem)
      return reply.code(error.status).send({ error: { code: error.code, message: error.message } });
    if (error instanceof EngineError) {
      const status =
        error.code === "INVALID_REQUEST" ? 400 : error.code === "CANCELLED" ? 409 : 503;
      return reply.code(status).send({ error: { code: error.code, message: error.message } });
    }
    if (error instanceof Error && error.name === "ZodError")
      return reply
        .code(400)
        .send({ error: { code: "INVALID_REQUEST", message: "请求参数不符合协议" } });
    const status =
      error instanceof Error &&
      "statusCode" in error &&
      typeof error.statusCode === "number" &&
      error.statusCode >= 400 &&
      error.statusCode < 500
        ? error.statusCode
        : 500;
    request.log.error(error);
    return reply.code(status).send({
      error: {
        code: "REQUEST_FAILED",
        message: status === 500 ? "服务暂时无法完成请求" : "请求格式错误",
      },
    });
  });
  app.get("/api/health", async () => {
    const result = await engine.request({ method: "about" });
    return { status: "ok", engine: result };
  });
  app.post("/api/games", async (request, reply) => {
    const game = await games.create(CreateGameSchema.parse(request.body));
    return reply.code(201).send(game);
  });
  app.get<{ Params: { id: string } }>("/api/games/:id", async (request) =>
    games.get(request.params.id),
  );
  app.post<{ Params: { id: string } }>("/api/games/:id/moves", async (request) => {
    const body = PlaySchema.parse(request.body);
    return games.play(request.params.id, body.version, body.move);
  });
  app.post<{ Params: { id: string } }>("/api/games/:id/analyze", async (request) => {
    const body = VersionSchema.parse(request.body);
    return games.analyze(request.params.id, body.version);
  });
  app.post<{ Params: { id: string } }>("/api/games/:id/undo", async (request) => {
    const body = VersionSchema.parse(request.body);
    return games.undo(request.params.id, body.version);
  });
  app.post<{ Params: { id: string } }>("/api/games/:id/cancel", async (request) => {
    const body = VersionSchema.parse(request.body);
    return games.cancel(request.params.id, body.version);
  });
  app.addHook("onClose", async () => {
    games.close();
  });
  return app;
}
