"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { buildPlan, validatePreset, validateRunSettings, summarizeDemo, DEMO_MODELS, BASE_PRESET, DEFAULT_RUN_SETTINGS, SUITES } = require("./app.js");
const copy = value => JSON.parse(JSON.stringify(value));
let checks = 0;
function check(name, action) { action(); checks += 1; console.log("ok " + checks + " - " + name); }

check("both suites default to one repetition without owning trial settings", () => {
  const defaults = JSON.parse(fs.readFileSync(path.join(__dirname, "../../execution/default.v0.draft.json"), "utf8"));
  assert.deepEqual(DEFAULT_RUN_SETTINGS, defaults.settings);
  for (const [id, suite] of Object.entries(SUITES)) {
    assert.ok(!("trials" in suite));
    assert.equal(buildPlan(DEMO_MODELS, [BASE_PRESET], id).run_settings.trials_per_case, 1);
    assert.equal(buildPlan(DEMO_MODELS, [BASE_PRESET], id).planned_trials, 2 * suite.cases);
  }
});
check("repetitions remain independent while models, presets and suites vary", () => {
  const presets = [{ ...BASE_PRESET }, { ...BASE_PRESET, id: "cool", temperature: 0.6 }];
  for (const trials of [1, 3, 5, 7, 1000]) {
    const settings = { ...copy(DEFAULT_RUN_SETTINGS), trials_per_case: trials };
    for (const [id, suite] of Object.entries(SUITES)) {
      const plan = buildPlan(DEMO_MODELS, presets, id, settings);
      assert.equal(plan.planned_trials, 4 * suite.cases * trials);
      assert.equal(plan.run_settings.trials_per_case, trials);
      assert.equal(plan.planned_api_calls, null);
    }
    assert.equal(buildPlan([DEMO_MODELS[0]], [BASE_PRESET], "quick", settings).planned_trials, 20 * trials);
  }
});
check("zero, fractional, missing and nonfinite repetition counts cannot become one", () => {
  for (const trials of [0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, null, "3", true, undefined]) {
    const settings = { ...copy(DEFAULT_RUN_SETTINGS), trials_per_case: trials };
    assert.ok(validateRunSettings(settings));
    assert.throws(() => buildPlan(DEMO_MODELS, [BASE_PRESET], "quick", settings));
  }
  assert.throws(() => buildPlan(DEMO_MODELS, [BASE_PRESET], "quick", { ...copy(DEFAULT_RUN_SETTINGS), trials_per_case: Number.MAX_SAFE_INTEGER }));
});
check("adding repetitions retains the seed policy and includes every trial", () => {
  const first = buildPlan(DEMO_MODELS, [BASE_PRESET], "quick");
  const extra = buildPlan(DEMO_MODELS, [BASE_PRESET], "detailed", { ...copy(DEFAULT_RUN_SETTINGS), trials_per_case: 3 });
  assert.deepEqual(first.run_settings.seed_policy, extra.run_settings.seed_policy);
  assert.equal(extra.run_settings.aggregation_policy, "all-trials");
  assert.ok(validateRunSettings({ ...copy(DEFAULT_RUN_SETTINGS), aggregation_policy: "best-of-n" }));
  for (const base_seed of [-1, 1.5, 2147483648, NaN]) assert.ok(validateRunSettings({ ...copy(DEFAULT_RUN_SETTINGS), seed_policy: { ...DEFAULT_RUN_SETTINGS.seed_policy, base_seed } }));
});
check("later edits cannot change a resolved model, preset or run snapshot", () => {
  const model = copy(DEMO_MODELS[0]), preset = copy(BASE_PRESET), settings = copy(DEFAULT_RUN_SETTINGS);
  const plan = buildPlan([model], [preset], "quick", settings);
  model.engine.mtp = 0; preset.temperature = 0.4; settings.trials_per_case = 7; settings.seed_policy.base_seed = 99;
  assert.equal(plan.combinations[0].model.engine.mtp, 3);
  assert.equal(plan.combinations[0].preset.temperature, 1);
  assert.equal(plan.run_settings.trials_per_case, 1);
  assert.equal(plan.run_settings.seed_policy.base_seed, 42);
  for (const key of ["nativeDecode", "reasoning", "cached", "helperTotal"]) assert.ok(!(key in plan.combinations[0].model));
});
check("preview remains non-executable without extra framework caps", () => {
  const plan = buildPlan(DEMO_MODELS, [BASE_PRESET], "quick");
  assert.equal(plan.execution_ready, false);
  assert.ok(Object.values(plan.framework_limits).every(value => value === null));
});
check("invalid selections and generation conditions are rejected", () => {
  assert.throws(() => buildPlan([], [BASE_PRESET], "quick"));
  assert.throws(() => buildPlan(DEMO_MODELS, [], "quick"));
  assert.throws(() => buildPlan([DEMO_MODELS[0], DEMO_MODELS[0]], [BASE_PRESET], "quick"));
  for (const change of [{ name: " " }, { temperature: NaN }, { topP: 0 }, { outputTokens: 262144 }, { contextTokens: 1.5 }, { thinking: "unknown" }]) assert.ok(validatePreset({ ...BASE_PRESET, ...change }));
});
check("new plans cannot change historical sample results or double count tokens", () => {
  const before = summarizeDemo("a");
  buildPlan(DEMO_MODELS, [BASE_PRESET], "detailed", { ...copy(DEFAULT_RUN_SETTINGS), trials_per_case: 5 });
  assert.deepEqual(summarizeDemo("a"), before);
  const a = summarizeDemo("a"), b = summarizeDemo("b");
  assert.equal(a.total, 412800); assert.equal(b.total, 394500);
  assert.equal(a.total, a.prompt + a.completion);
  assert.ok(a.reasoning <= a.completion && a.cached <= a.prompt);
  assert.notEqual(a.total, a.prompt + a.completion + a.helperTotal);
});
check("binary quality and optimization use distinct units", () => {
  const a = summarizeDemo("a"), b = summarizeDemo("b");
  assert.equal(a.correct, 5); assert.equal(a.binary, 7); assert.equal(a.optimization, 71.4);
  assert.equal(b.correct, 4); assert.equal(b.binary, 7); assert.equal(b.optimization, 75.8);
  assert.equal(a.seconds, 3120); assert.equal(b.seconds, 2760);
});
console.log(JSON.stringify({ status: "passed", ui_state_checks: checks, model_calls: 0 }));
