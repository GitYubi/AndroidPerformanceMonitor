import { createCaseRunner, defineNode, z } from "@midscene/test";

// Midscene Test owns step sequencing and stop/continue policy. Only stable IDs
// enter its result objects; resolved parameters stay inside the execution closure.
export async function runCase(testCase, execute, onSkip = () => {}) {
  let state = "passed";
  const steps = new Map(testCase.steps.map(step => [step.id, step]));
  const node = defineNode({
    name: "uiStep",
    inputSchema: z.object({ id: z.string() }),
    async execute({ input }) {
      const step = steps.get(input.id);
      if (
        step.enabled === false ||
        state === "error" ||
        state === "needs_review"
      ) {
        onSkip(step);
        return;
      }
      let result;
      try {
        result = await execute(step);
      } catch {
        result = "error";
      }
      if (result !== "passed") {
        state =
          result === "error"
            ? "error"
            : result === "needs_review"
              ? "needs_review"
              : "failed";
        throw new Error(
          result === "error" ? "UI execution error" : "UI assertion failed"
        );
      }
      return { data: { state: result } };
    },
  });
  const runner = createCaseRunner({ nodes: [node] });
  try {
    await runner.run({
      name: testCase.name || "UI test",
      steps: testCase.steps.map(step => ({
        uiStep: {
          id: step.id,
          $: {
            timeout: step.timeoutMs + 15000,
            "continue-on-error":
              step.type === "assert" && step.onFailure === "continue",
          },
        },
      })),
    });
  } catch (error) {
    if (state === "passed" && !error.result) throw error;
    if (state === "passed") state = "error";
  }
  if (
    state === "passed" &&
    !testCase.steps.some(
      s => s.enabled !== false && ["assert", "explore", "scan"].includes(s.type)
    )
  )
    return "needs_review";
  return state;
}
