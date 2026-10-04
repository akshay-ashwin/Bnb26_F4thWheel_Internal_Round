import type { components, operations } from "./generated/schema";

type Schemas = components["schemas"];

/** JSON body of a successful response, taken from the generated OpenAPI operations. */
export type Ok<Op extends keyof operations, Status extends number = 200> =
  operations[Op]["responses"] extends Record<
    Status,
    { content: { "application/json": infer Body } }
  >
    ? Body
    : never;

/** JSON request body of an operation. */
export type Body<Op extends keyof operations> = operations[Op] extends {
  requestBody: { content: { "application/json": infer B } };
}
  ? B
  : never;

export type Drop = Schemas["DropOut"];
export type DropListItem = Schemas["DropListItem"];
export type Me = Schemas["MeOut"];
export type MeEntry = Schemas["MeEntry"];
export type MeAllocation = Schemas["MeAllocation"];
export type Phase = Drop["phase"];
export type Mode = Drop["mode"];
export type EntryStatus = MeEntry["status"];
export type ErrorCode = Schemas["ErrorCode"];
export type ErrorEnvelope = Schemas["ErrorResponse"];
export type Metrics = Schemas["MetricsOut"];
export type Integrity = Schemas["IntegrityOut"];
export type DrawProof = Schemas["DrawProofOut"];
export type AbuseConfig = Schemas["AbuseConfigOut"];
export type SimLatest = Schemas["SimLatestOut"];
export type PhaseAction = Schemas["PhaseIn"]["action"];
