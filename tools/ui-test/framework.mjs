import { setImmediate as yieldTurn } from "node:timers/promises";
import { buildStepPlan, isAssertion } from "./protocol.mjs";

export async function runCase(testCase, execute, onSkip = () => {}) {
  const { children } = buildStepPlan(testCase.steps);
  const executions = new Map();
  let state = "passed";
  let verified = false;

  function skip(steps, iterations) {
    for (const step of steps) {
      onSkip(step, iterations);
      skip(children(step.id), iterations);
      skip(children(step.id, "else"), iterations);
    }
  }

  async function run(steps, variables, iterations) {
    for (const step of steps) {
      if (step.enabled === false) {
        skip([step], iterations);
        continue;
      }
      const occurrence = (executions.get(step.id) ?? 0) + 1;
      executions.set(step.id, occurrence);
      let result;
      try {
        result = await execute(step, variables, occurrence, iterations);
      } catch {
        result = "error";
      }
      if (step.type === "if" && ["passed", "failed"].includes(result)) {
        const branch = result === "passed" ? null : "else";
        skip(children(step.id, branch === null ? "else" : null), iterations);
        if (!(await run(children(step.id, branch), variables, iterations)))
          return false;
        continue;
      }
      if (result !== "passed") {
        state =
          result === "error" || result === "needs_review" ? result : "failed";
        if (
          state === "failed" &&
          isAssertion(step.type) &&
          step.onFailure === "continue"
        )
          continue;
        return false;
      }
      if (isAssertion(step.type) || ["explore", "scan"].includes(step.type))
        verified = true;
      if (
        step.type === "else" &&
        !(await run(children(step.id), variables, iterations))
      )
        return false;
      if (step.type === "for") {
        const body = children(step.id);
        const { count, indexVariable } = step.options;
        for (let index = 1; body.length && index <= count; index++) {
          await yieldTurn();
          const scope = indexVariable
            ? { ...variables, [indexVariable]: String(index) }
            : variables;
          if (!(await run(body, scope, [...iterations, index]))) return false;
        }
      }
    }
    return true;
  }

  await run(children(), {}, []);
  return state === "passed" && !verified ? "needs_review" : state;
}
