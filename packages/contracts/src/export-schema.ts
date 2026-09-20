import { mkdir, writeFile } from "node:fs/promises";
import { z } from "zod";
import {
  GameSchema,
  MatchConfigSchema,
  WorkerRequestSchema,
  WorkerResponseSchema,
} from "./index.js";

const destination = new URL("../schema/", import.meta.url);
await mkdir(destination, { recursive: true });
for (const [name, schema] of Object.entries({
  "worker-request": WorkerRequestSchema,
  "worker-response": WorkerResponseSchema,
  game: GameSchema,
  "match-config": MatchConfigSchema,
})) {
  await writeFile(
    new URL(name + ".json", destination),
    JSON.stringify(z.toJSONSchema(schema), null, 2) + "\n",
  );
}
