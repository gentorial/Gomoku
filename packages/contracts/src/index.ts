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
    .object({ size: BoardSizeSchema, rule: RuleSchema, bytes: z.number().int().positive() })
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
