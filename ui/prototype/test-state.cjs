"use strict";
const assert = require("node:assert/strict");
const { buildPlan, validatePreset, summarizeDemo, DEMO_MODELS, BASE_PRESET } = require("./app.js");
let checks = 0;
function check(name, action) { action(); checks += 1; console.log("ok " + checks + " - " + name); }

check("combination counts are trials, not API calls", () => {
  const presets = [{ ...BASE_PRESET }, { ...BASE_PRESET, id: "cool", temperature: 0.6 }];
  const quick = buildPlan(DEMO_MODELS, presets, "quick");
  assert.equal(quick.planned_trials, 80);
  assert.equal(quick.planned_api_calls, null);
  assert.equal(buildPlan(DEMO_MODELS, presets, "detailed").planned_trials, 1200);
});
check("preview never becomes an executable plan", () => {
  const plan = buildPlan(DEMO_MODELS, [BASE_PRESET], "quick");
  assert.equal(plan.execution_ready, false);
  assert.ok(Object.values(plan.framework_limits).every(value => value === null));
});
check("later preset and engine edits cannot change an existing snapshot", () => {
  const model = JSON.parse(JSON.stringify(DEMO_MODELS[0]));
  const preset = { ...BASE_PRESET };
  const plan = buildPlan([model], [preset], "quick");
  model.engine.mtp = 0;
  preset.temperature = 0.4;
  assert.equal(plan.combinations[0].model.engine.mtp, 3);
  assert.equal(plan.combinations[0].preset.temperature, 1);
  for (const key of ["nativeDecode", "reasoning", "cached", "helperTotal"]) assert.ok(!(key in plan.combinations[0].model));
});
check("invalid or missing selections are rejected", () => {
  assert.throws(() => buildPlan([], [BASE_PRESET], "quick"));
  assert.throws(() => buildPlan(DEMO_MODELS, [], "quick"));
  assert.throws(() => buildPlan([DEMO_MODELS[0], DEMO_MODELS[0]], [BASE_PRESET], "quick"));
  for (const change of [{ temperature: NaN }, { topP: 0 }, { outputTokens: 262144 }, { contextTokens: 1.5 }]) {
    assert.ok(validatePreset({ ...BASE_PRESET, ...change }));
  }
});
check("reasoning and cached tokens are not added twice", () => {
  const a = summarizeDemo("a"), b = summarizeDemo("b");
  assert.equal(a.total, 412800);
  assert.equal(b.total, 394500);
  assert.equal(a.total, a.prompt + a.completion);
  assert.ok(a.reasoning <= a.completion && a.cached <= a.prompt);
  assert.notEqual(a.total, a.prompt + a.completion + a.helperTotal);
});
check("binary quality and optimization remain separate", () => {
  const a = summarizeDemo("a"), b = summarizeDemo("b");
  assert.equal(a.correct, 5); assert.equal(a.binary, 7); assert.equal(a.optimization, 71.4);
  assert.equal(b.correct, 4); assert.equal(b.binary, 7); assert.equal(b.optimization, 75.8);
  assert.equal(a.seconds, 3120); assert.equal(b.seconds, 2760);
});
console.log(JSON.stringify({ status: "passed", ui_state_checks: checks, model_calls: 0 }));
