// Types generated from the frozen spec/ (run `pnpm generate`). Do not hand-edit generated/.
export type { paths as CorePaths, components as CoreComponents } from "./generated/core";
export type { paths as IntelPaths, components as IntelComponents } from "./generated/intel";
export type { paths as BankPaths, components as BankComponents } from "./generated/bank";

/** Base path of the core API behind the console same-origin proxy. */
export const CORE_API_BASE = "/api/v1";

/** RFC 9457 media type used by every SafePay error response. */
export const PROBLEM_MEDIA_TYPE = "application/problem+json";
