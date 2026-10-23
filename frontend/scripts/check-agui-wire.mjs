// Feed backend/scripts/capture_agui_wire.py output through the installed client.
// Uses its real SSE parser, state reducer and terminal state machine.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { HttpAgent } from "@ag-ui/client";

const events = (await readFile(process.argv[2], "utf8"))
  .trim().split("\n").map((line) => JSON.parse(line));
assert.equal(events[0].type, "RUN_STARTED");
assert.equal(events.at(-1).type, "RUN_FINISHED");
assert.equal(events.filter((event) => ["RUN_FINISHED", "RUN_ERROR"].includes(event.type)).length, 1);
assert(events.some((event) => event.type === "TOOL_CALL_RESULT"));
assert(events.some((event) => event.type === "STATE_DELTA" &&
  event.delta.some((op) => op.path === "/selection" && Object.hasOwn(op, "value") && op.value === null)));

const agent = new HttpAgent({
  url: "http://synthetic.invalid/stream",
  threadId: events[0].threadId,
  initialState: { selection: "previous" },
  fetch: async () => new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""), {
    headers: { "content-type": "text/event-stream" },
  }),
});
let finished = 0;
const failures = [];
agent.subscribe({
  onRunFinishedEvent: () => { finished += 1; },
  onRunErrorEvent: ({ event }) => { failures.push(event.message); },
  onRunFailed: ({ error }) => { failures.push(String(error)); },
});
await agent.runAgent({ runId: events[0].runId });
assert.deepEqual(failures, []);
assert.equal(finished, 1);
assert.equal(agent.state.selection, null);
assert(agent.messages.some((message) => message.role === "assistant" && message.content === "Selection cleared."));
console.log(`PASS: ${events.length} real backend events parsed; tool result, null patch, reply and terminal applied.`);
