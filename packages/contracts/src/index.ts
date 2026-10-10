import { z } from "zod";

export const PROTOCOL_VERSION = 1 as const;
export const ColorSchema = z.enum(["black", "white"]);
export const RuleSchema = z.enum(["freestyle", "standard"]);
export const BoardSizeSchema = z.union([z.literal(15), z.literal(20)]);
export const MoveSchema = z
  .object({
    x: z.number().int().min(0).max(19),
    y: z.number().int().min(0).max(19),
  })
  .strict();
export const PositionSchema = z
  .object({
    size: BoardSizeSchema,
    rule: RuleSchema,
    moves: z.array(MoveSchema).max(400),
  })
  .strict();
export const StatusSchema = z.enum(["playing", "black_win", "white_win", "draw"]);
export const LimitsSchema = z
  .object({
    timeMs: z.number().int().min(0).max(10000),
    maxDepth: z.number().int().min(1).max(12).optional(),
    maxNodes: z.number().int().min(0).max(10000000).optional(),
  })
  .strict();
export const PositionResultSchema = PositionSchema.extend({
  kind: z.literal("position"),
  toMove: ColorSchema,
  status: StatusSchema,
});
export const AnalysisSchema = z.object({
  kind: z.literal("analysis"),
  bestMove: MoveSchema.nullable(),
  score: z.object({
    value: z.number().int(),
    perspective: z.literal("side_to_move"),
    kind: z.enum(["heuristic", "mate"]),
  }),
  depth: z.number().int().nonnegative(),
  nodes: z.number().int().nonnegative(),
  elapsedMs: z.number().int().nonnegative(),
  pv: z.array(MoveSchema),
  reason: z.enum(["completed", "limit", "cancelled", "terminal"]),
  evaluator: z.string(),
  model: z.object({ id: z.string(), label: z.string(), sha256: z.string() }).optional(),
});
const AboutSchema = z.object({
  kind: z.literal("about"),
  name: z.string(),
  version: z.string(),
  protocolVersion: z.literal(1),
  rules: z.array(RuleSchema),
  sizes: z.array(BoardSizeSchema),
  evaluator: z.string(),
  nnue: z
    .object({
      sizes: z.array(BoardSizeSchema).min(1),
      rule: RuleSchema,
      bytes: z.number().int().positive(),
    })
    .nullable()
    .optional(),
});
const envelope = {
  v: z.literal(1),
  id: z.string().min(1).max(128),
};
export const WorkerRequestSchema = z.discriminatedUnion("method", [
  z.object({ ...envelope, method: z.literal("about") }).strict(),
  z.object({ ...envelope, method: z.literal("inspect"), position: PositionSchema }).strict(),
  z
    .object({ ...envelope, method: z.literal("play"), position: PositionSchema, move: MoveSchema })
    .strict(),
  z
    .object({
      ...envelope,
      method: z.literal("analyze"),
      position: PositionSchema,
      limits: LimitsSchema,
      evaluator: z.enum(["nnue", "handcrafted"]).optional(),
    })
    .strict(),
  z
    .object({ ...envelope, method: z.literal("stop"), targetId: z.string().min(1).max(128) })
    .strict(),
]);
export const WorkerResponseSchema = z.discriminatedUnion("ok", [
  z.object({
    ...envelope,
    ok: z.literal(true),
    result: z.discriminatedUnion("kind", [
      PositionResultSchema,
      AnalysisSchema,
      AboutSchema,
      z.object({ kind: z.literal("stopped"), matched: z.boolean() }),
    ]),
  }),
  z.object({
    ...envelope,
    ok: z.literal(false),
    error: z.object({ code: z.string(), message: z.string() }),
  }),
]);
export const CreateGameSchema = z
  .object({
    size: BoardSizeSchema.default(15),
    rule: RuleSchema.default("freestyle"),
    humanColor: ColorSchema.default("black"),
    thinkTimeMs: z.number().int().min(50).max(3000).default(300),
  })
  .strict();
export const VersionSchema = z.object({ version: z.number().int().nonnegative() }).strict();
export const PlaySchema = VersionSchema.extend({ move: MoveSchema });
export const GameSchema = PositionResultSchema.omit({ kind: true }).extend({
  id: z.string(),
  version: z.number().int().nonnegative(),
  humanColor: ColorSchema,
  thinkTimeMs: z.number().int(),
  thinking: z.boolean(),
  analysis: AnalysisSchema.nullable(),
});
export const ApiErrorSchema = z.object({
  error: z.object({ code: z.string(), message: z.string() }),
});

export const MatchConfigSchema = z
  .object({
    mode: z.enum(["human-human", "human-ai", "ai-ai"]).default("human-ai"),
    size: BoardSizeSchema.default(15),
    rule: RuleSchema.default("freestyle"),
    humanColor: ColorSchema.default("black"),
    blackTimeMs: z.number().int().min(100).max(3000).default(300),
    whiteTimeMs: z.number().int().min(100).max(3000).default(300),
    evaluator: z.enum(["nnue", "handcrafted"]).default("nnue"),
  })
  .strict();
export type MatchConfig = z.infer<typeof MatchConfigSchema>;

export type Color = z.infer<typeof ColorSchema>;
export type Rule = z.infer<typeof RuleSchema>;
export type BoardSize = z.infer<typeof BoardSizeSchema>;
export type Move = z.infer<typeof MoveSchema>;
export type Position = z.infer<typeof PositionSchema>;
export type PositionResult = z.infer<typeof PositionResultSchema>;
export type Analysis = z.infer<typeof AnalysisSchema>;
export type Game = z.infer<typeof GameSchema>;
export type CreateGame = z.input<typeof CreateGameSchema>;
export type WorkerRequest = z.infer<typeof WorkerRequestSchema>;
export type WorkerResult = Extract<z.infer<typeof WorkerResponseSchema>, { ok: true }>["result"];
type WithoutEnvelope<T> = T extends unknown ? Omit<T, "v" | "id"> : never;
export type WorkerPayload = WithoutEnvelope<WorkerRequest>;

/** Transport-independent engine port; all positions remain validated by C++. */
export interface Engine {
  request(payload: WorkerPayload, signal?: AbortSignal): Promise<WorkerResult>;
  close(): void;
}

/** Online play over one WebSocket: rooms by code, random matching, resign and undo by consent. */
export const ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
export const RoomCodeSchema = z.string().regex(/^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$/);
export const PlayerNameSchema = z.string().trim().min(1).max(16);
const Game = { rule: RuleSchema, size: BoardSizeSchema };

export const OnlineClientMessageSchema = z.discriminatedUnion("type", [
  // The token, if any, resumes a previous session (and its room) after a reconnect.
  z
    .object({
      type: z.literal("hello"),
      name: PlayerNameSchema,
      token: z.string().max(64).optional(),
    })
    .strict(),
  z.object({ type: z.literal("create"), ...Game }).strict(),
  z.object({ type: z.literal("join"), code: RoomCodeSchema }).strict(),
  z.object({ type: z.literal("match"), ...Game }).strict(),
  // Leaves the matching queue, or a room (resigning a game in progress).
  z.object({ type: z.literal("leave") }).strict(),
  z.object({ type: z.literal("move"), move: MoveSchema }).strict(),
  z.object({ type: z.literal("resign") }).strict(),
  // Asks to take back one's own last move (and the opponent's reply after it).
  z.object({ type: z.literal("undo") }).strict(),
  z.object({ type: z.literal("undoReply"), accept: z.boolean() }).strict(),
]);

export const OnlinePlayerSchema = z.object({ name: z.string(), connected: z.boolean() });
export const OnlineRoomSchema = z.object({
  code: RoomCodeSchema,
  ...Game,
  you: ColorSchema,
  players: z.object({ black: OnlinePlayerSchema.nullable(), white: OnlinePlayerSchema.nullable() }),
  moves: z.array(MoveSchema),
  toMove: ColorSchema,
  status: StatusSchema,
  /** Why a finished game ended; null while it is playing or waiting for an opponent. */
  ending: z.enum(["five", "draw", "resign"]).nullable(),
  /** The color asking to take back moves, if a request is pending. */
  undo: ColorSchema.nullable(),
});

export const OnlineServerMessageSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("welcome"), token: z.string() }),
  z.object({ type: z.literal("lobby") }),
  z.object({ type: z.literal("queued"), ...Game }),
  z.object({ type: z.literal("room"), room: OnlineRoomSchema }),
  z.object({ type: z.literal("error"), message: z.string() }),
]);

export type OnlineClientMessage = z.infer<typeof OnlineClientMessageSchema>;
export type OnlineServerMessage = z.infer<typeof OnlineServerMessageSchema>;
export type OnlineRoom = z.infer<typeof OnlineRoomSchema>;
