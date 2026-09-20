import {
  ApiErrorSchema,
  GameSchema,
  type CreateGame,
  type Game,
  type Move,
} from "@gomoku/contracts";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}
export class GomokuClient {
  constructor(private readonly baseUrl = "/api") {}
  private async request(
    path: string,
    method: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<Game> {
    const response = await fetch(this.baseUrl + path, {
      method,
      signal,
      headers: body === undefined ? {} : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data: unknown = await response.json();
    if (!response.ok) {
      const parsed = ApiErrorSchema.safeParse(data);
      throw new ApiError(
        response.status,
        parsed.success ? parsed.data.error.code : "HTTP_ERROR",
        parsed.success ? parsed.data.error.message : "请求失败",
      );
    }
    return GameSchema.parse(data);
  }
  createGame(config: CreateGame, signal?: AbortSignal) {
    return this.request("/games", "POST", config, signal);
  }
  game(id: string, signal?: AbortSignal) {
    return this.request("/games/" + encodeURIComponent(id), "GET", undefined, signal);
  }
  play(id: string, version: number, move: Move, signal?: AbortSignal) {
    return this.request(
      "/games/" + encodeURIComponent(id) + "/moves",
      "POST",
      { version, move },
      signal,
    );
  }
  analyze(id: string, version: number, signal?: AbortSignal) {
    return this.request(
      "/games/" + encodeURIComponent(id) + "/analyze",
      "POST",
      { version },
      signal,
    );
  }
  undo(id: string, version: number) {
    return this.request("/games/" + encodeURIComponent(id) + "/undo", "POST", { version });
  }
  cancel(id: string, version: number) {
    return this.request("/games/" + encodeURIComponent(id) + "/cancel", "POST", { version });
  }
}
