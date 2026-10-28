"use strict";

// All models, cases, answers and metrics below are fictional UI fixtures.
// This prototype has no endpoint client, persistence, or benchmark execution.
const DEMO_MODELS = [
  { id: "demo-model-a", name: "モデルA", family: "vLLM", engine: { backend: "vllm", kv: "bf16", mtp: 3 }, nativeDecode: 38.4, reasoning: 42000, cached: 90000, helperTotal: 58100 },
  { id: "demo-model-b", name: "モデルB", family: "llama.cpp", engine: { backend: "llamacpp", kv: "f16", mtp: 0 }, nativeDecode: 46.2, reasoning: 60000, cached: 80000, helperTotal: 70100 }
];
const BASE_PRESET = { id: "standard", name: "基準設定", temperature: 1, topP: 0.95, contextTokens: 262144, outputTokens: 131072, thinking: "model-native-maximum" };
const SUITES = { quick: { name: "Quick", cases: 20 }, detailed: { name: "Detailed", cases: 100 } };
const DEFAULT_RUN_SETTINGS = { trials_per_case: 1, seed_policy: { base_seed: 42, derivation: "sha256-case-revision-trial-v1", trial_index_origin: 0 }, aggregation_policy: "all-trials" };
const CASES = [
  { id: "demo-code-1", title: "配列の並べ替え", category: "コード", a: true, b: true, secondsA: 160, secondsB: 150, inputA: 30000, inputB: 28000, outputA: 4000, outputB: 6000, question: "渡された配列を指定した順序で並べ替える関数を作成してください。", answerA: "def solve(values):\n    return sorted(values)", answerB: "def solve(values):\n    return list(sorted(values))" },
  { id: "demo-code-2", title: "更新に対応する集計", category: "コード", a: true, b: false, secondsA: 240, secondsB: 210, inputA: 40000, inputB: 37000, outputA: 6000, outputB: 8000, question: "値の更新と区間の集計に対応する方法を示してください。", answerA: "更新と集計を木構造で管理します。空区間の扱いも同じ規則に揃えます。", answerB: "区間ごとの合計値を保存します。更新後も元の合計値を返します。" },
  { id: "demo-plan-1", title: "予算内の工程計画", category: "計画", a: true, b: true, secondsA: 420, secondsB: 360, inputA: 45000, inputB: 42000, outputA: 8000, outputB: 10000, question: "期限・予算・依存関係を満たす工程計画を作成してください。", answerA: "依存する工程を先に完了させ、余裕時間を残して確認工程を配置します。", answerB: "並行できる工程を分け、最後の検査を期限前にまとめます。" },
  { id: "demo-tools-1", title: "規則に沿った依頼処理", category: "ツール", a: false, b: true, secondsA: 360, secondsB: 340, inputA: 40000, inputB: 36000, outputA: 7000, outputB: 9500, question: "必要な条件を確認して、規則に従って依頼を処理してください。", answerA: "依頼された処理を実行しました。追加条件の照合は省略しました。", answerB: "規則と対象記録を照合し、許可された操作を実行後に読み戻して確認しました。" },
  { id: "demo-sales-1", title: "購入条件を確かめる商談", category: "営業対話", a: true, b: false, secondsA: 540, secondsB: 480, inputA: 50000, inputB: 45000, outputA: 10000, outputB: 12000, question: "顧客の条件を確認し、商品が適合するかを確かめて商談を進めてください。", answerA: "重視する条件を確認して適合する機能だけを説明し、顧客と次の確認事項を合意しました。", answerB: "提供されていない機能も利用できると約束して、購入への同意を得ました。" },
  { id: "demo-crm-1", title: "商談情報からの見積確認", category: "営業実務", a: true, b: true, secondsA: 420, secondsB: 390, inputA: 40000, inputB: 36000, outputA: 7000, outputB: 9000, question: "商談記録と価格規則から見積の承認条件を確認してください。", answerA: "商談記録と承認規則を照合し、必要な承認者と未確認事項をまとめました。", answerB: "割引条件と承認権限を確認し、許可された承認経路を示しました。" },
  { id: "demo-science-1", title: "数値計算と境界条件", category: "科学", a: false, b: false, secondsA: 480, secondsB: 430, inputA: 55000, inputB: 48000, outputA: 9000, outputB: 13000, question: "指定された境界条件で数値計算を行ってください。", answerA: "内部領域で計算しましたが、端点の条件は初期値のままです。", answerB: "計算式を適用しました。端点には別の境界規則を使いました。" },
  { id: "demo-opt-1", title: "制約を満たす配置の改善", category: "最適化", a: 71.4, b: 75.8, secondsA: 500, secondsB: 400, inputA: 50000, inputB: 38000, outputA: 11800, outputB: 17000, question: "実行可能な配置を作り、目的値を改善してください。", answerA: "重なりを避けて配置し、局所的な入れ替えで目的値を改善しました。", answerB: "初期配置を複数作り、制約を保つ近傍探索で目的値を改善しました。" }
];

function copy(value) { return JSON.parse(JSON.stringify(value)); }
function presetErrors(preset) {
  const errors = {};
  if (typeof preset.name !== "string" || !preset.name.trim()) errors.name = "プリセット名を入力してください。";
  if (!Number.isFinite(preset.temperature) || preset.temperature < 0 || preset.temperature > 2) errors.temperature = "温度を0〜2で入力してください。";
  if (!Number.isFinite(preset.topP) || preset.topP <= 0 || preset.topP > 1) errors.topP = "top-pを0より大きく1以下で入力してください。";
  if (!Number.isSafeInteger(preset.contextTokens) || preset.contextTokens < 1) errors.contextTokens = "コンテキストを正の整数で入力してください。";
  if (!Number.isSafeInteger(preset.outputTokens) || preset.outputTokens < 1) errors.outputTokens = "一要求の最大出力を正の整数で入力してください。";
  else if (preset.outputTokens >= preset.contextTokens) errors.outputTokens = "入力の余地を残すため、出力をコンテキストより小さくしてください。";
  if (!["model-native-maximum", "off"].includes(preset.thinking)) errors.thinking = "対応する思考設定を選んでください。";
  return errors;
}
function validatePreset(preset) { return Object.values(presetErrors(preset))[0] || null; }
function validateTrialCount(value) { return Number.isSafeInteger(value) && value > 0 ? null : "試行回数を1以上の整数で入力してください。"; }
function validateBaseSeed(value) { return Number.isSafeInteger(value) && value >= 0 && value <= 2147483647 ? null : "基底seedを0〜2147483647の整数で入力してください。"; }
function validateRunSettings(settings) {
  if (!settings || typeof settings !== "object") return "実行設定を指定してください。";
  return validateTrialCount(settings.trials_per_case) || validateBaseSeed(settings.seed_policy?.base_seed)
    || (settings.seed_policy?.derivation !== DEFAULT_RUN_SETTINGS.seed_policy.derivation || settings.seed_policy?.trial_index_origin !== 0 ? "seed方針が未対応です。" : null)
    || (settings.aggregation_policy !== "all-trials" ? "全試行を集計する方針を指定してください。" : null);
}
function buildPlan(models, presets, suiteId, runSettings = DEFAULT_RUN_SETTINGS) {
  if (!models.length || !presets.length) throw new Error("モデルと設定をそれぞれ1つ以上選んでください。");
  if (!SUITES[suiteId]) throw new Error("評価セットが不明です。");
  if (new Set(models.map(x => x.id)).size !== models.length || new Set(presets.map(x => x.id)).size !== presets.length) throw new Error("同じ構成が重複しています。");
  for (const preset of presets) { const error = validatePreset(preset); if (error) throw new Error(error); }
  const error = validateRunSettings(runSettings);
  if (error) throw new Error(error);
  const suite = SUITES[suiteId];
  const plannedTrials = models.length * presets.length * suite.cases * runSettings.trials_per_case;
  if (!Number.isSafeInteger(plannedTrials)) throw new Error("合計試行数が正確に計算できる数値の範囲を超えています。");
  return {
    prototype: true, execution_ready: false, language: "ja", suite_id: suiteId + ".v0.draft",
    cases_per_configuration: suite.cases, run_settings: copy(runSettings),
    planned_trials: plannedTrials,
    planned_api_calls: null,
    framework_limits: { total_output_tokens: null, tool_calls: null, agent_turns: null, framework_deadline_seconds: null },
    native_termination: "preserve-and-record",
    combinations: models.flatMap(model => presets.map(preset => ({
      model: { id: model.id, name: model.name, family: model.family, engine: copy(model.engine) },
      preset: copy(preset)
    }))),
    pending: ["問題ID・日本語版・採点資産の固定", "モデル接続と能力の確認", "backendごとの有効設定の確認"]
  };
}
function summarizeDemo(key) {
  const model = DEMO_MODELS[key === "a" ? 0 : 1];
  const binary = CASES.filter(item => typeof item[key] === "boolean");
  const capital = key.toUpperCase();
  const prompt = CASES.reduce((sum, item) => sum + item["input" + capital], 0);
  const completion = CASES.reduce((sum, item) => sum + item["output" + capital], 0);
  return { correct: binary.filter(item => item[key]).length, binary: binary.length,
    optimization: CASES.find(item => item.category === "最適化")[key], prompt, completion,
    total: prompt + completion, reasoning: model.reasoning, cached: model.cached,
    helperTotal: model.helperTotal, seconds: CASES.reduce((sum, item) => sum + item["seconds" + capital], 0),
    nativeDecode: model.nativeDecode };
}

if (typeof module !== "undefined") module.exports = { buildPlan, validatePreset, validateRunSettings, summarizeDemo, DEMO_MODELS, BASE_PRESET, DEFAULT_RUN_SETTINGS, SUITES, CASES };

if (typeof document !== "undefined") {
  const state = { demo: true, metricModel: "a", selectedModels: new Set(DEMO_MODELS.map(x => x.id)),
    models: copy(DEMO_MODELS), selectedPresets: new Set(["standard"]),
    presets: [copy(BASE_PRESET), { ...copy(BASE_PRESET), id: "low-temperature", name: "温度0.6", temperature: 0.6 }],
    editingPreset: "standard", suite: "quick", runSettings: copy(DEFAULT_RUN_SETTINGS), view: "overview", detail: "answer", activeCase: null };
  const $ = id => document.getElementById(id);
  const fmt = value => (value / 1000).toFixed(value % 1000 ? 1 : 0) + "K";
  const duration = value => Math.floor(value / 60) + "分" + String(value % 60).padStart(2, "0") + "秒";
  let toastTimer;
  function el(tag, attrs = {}, ...children) {
    const item = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") item.className = value;
      else if (key.startsWith("on")) item.addEventListener(key.slice(2), value);
      else if (key === "checked") item.checked = value;
      else if (key === "value") item.value = value;
      else item.setAttribute(key, value);
    }
    for (const child of children.flat()) if (child !== null && child !== undefined) item.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return item;
  }
  function toast(message) { $("toast").textContent = message; $("toast").classList.add("visible"); clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").classList.remove("visible"), 3500); }
  function showView(name) {
    if (!["overview", "configure", "cases"].includes(name)) name = "overview";
    state.view = name;
    document.querySelectorAll(".view").forEach(item => item.classList.toggle("hidden", item.id !== "view-" + name));
    document.querySelectorAll(".nav-item").forEach(item => { const selected = item.dataset.view === name; item.classList.toggle("active", selected); if (selected) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current"); });
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
    window.scrollTo(0, 0);
    $(name === "configure" ? "configure-heading" : name === "cases" ? "cases-heading" : "overview-heading").focus({ preventScroll: true });
  }
  function statusBadge(value) {
    const kind = typeof value === "number" ? "score" : value ? "pass" : "fail";
    return el("span", { class: "status-badge " + kind }, typeof value === "number" ? value.toFixed(1) + " 点" : value ? "✓ 成功" : "− 不成功");
  }
  function renderMetrics() {
    const data = summarizeDemo(state.metricModel);
    const items = [
      ["正誤式の成功", data.correct + " / " + data.binary, "ケース", "最適化の連続点は含めない"],
      ["最適化の得点", data.optimization.toFixed(1), "/ 100", "最適化ケースの連続点"],
      ["対象モデルの消費", fmt(data.total), "tokens", "入力 " + fmt(data.prompt) + " ＋ 出力 " + fmt(data.completion)],
      ["課題完了時間", Math.floor(data.seconds / 60), "分", "対話・ツールの時間を含む"],
      ["生成速度", data.nativeDecode.toFixed(1), "token/s", "エンジン報告の生成速度"]
    ];
    $("metric-grid").replaceChildren(...items.map(([title, value, unit, note]) => el("div", { class: "metric-card" },
      el("div", { class: "metric-label" }, title),
      el("div", { class: "metric-value" }, state.demo ? value : "—", el("small", {}, state.demo ? unit : "未測定")),
      el("div", { class: "metric-foot" }, state.demo ? note : "実測結果はまだありません"))));
    document.querySelector(".scope-meta").textContent = state.demo ? "8ケース · 日本語 · 各1回" : "未測定 · 日本語";
    document.querySelector(".scope-chip").textContent = state.demo ? "サンプル比較" : "実測結果なし";
  }
  function renderComparison() {
    if (!state.demo) { $("comparison-body").replaceChildren(el("tr", {}, el("td", { colspan: "6", class: "empty-cell" }, "実測結果はまだありません。"))); return; }
    $("comparison-body").replaceChildren(...DEMO_MODELS.map((model, index) => {
      const key = index === 0 ? "a" : "b", data = summarizeDemo(key);
      return el("tr", {},
        el("td", {}, el("div", { class: "model-cell" }, el("span", { class: "model-avatar " + key }, index ? "B" : "A"), el("div", {}, el("div", { class: "model-name" }, model.name), el("div", { class: "model-caption" }, model.family + " · " + model.engine.kv.toUpperCase() + " KV · " + (model.engine.mtp ? "MTP" + model.engine.mtp : "MTPなし"))))),
        el("td", { class: "score-good" }, data.correct + " / " + data.binary, el("span", { class: "cell-foot" }, "正誤式ケース")),
        el("td", {}, data.optimization.toFixed(1), el("span", { class: "cell-foot" }, "連続点 / 100")),
        el("td", {}, fmt(data.total), el("span", { class: "cell-foot" }, "補助 " + fmt(data.helperTotal) + " は別")),
        el("td", {}, duration(data.seconds)), el("td", {}, data.nativeDecode.toFixed(1) + " tok/s", el("span", { class: "cell-foot" }, "エンジン報告値")));
    }));
  }
  function renderQuality() {
    if (!state.demo) { $("quality-chart").replaceChildren(el("p", { class: "empty-cell" }, "分野別の結果はまだありません。")); return; }
    const categories = [...new Set(CASES.filter(item => typeof item.a === "boolean").map(item => item.category))];
    $("quality-chart").replaceChildren(...categories.map(category => {
      const rows = CASES.filter(item => item.category === category);
      const a = rows.filter(item => item.a).length, b = rows.filter(item => item.b).length;
      const bars = el("div", { class: "quality-bars" });
      for (const [key, correct] of [["a", a], ["b", b]]) {
        const fill = el("div", { class: "bar-fill model-" + key }); fill.style.width = correct / rows.length * 100 + "%";
        bars.append(el("div", { class: "bar-track", role: "img", "aria-label": category + " モデル" + key.toUpperCase() + " " + correct + "/" + rows.length }, fill));
      }
      return el("div", { class: "quality-row" }, el("span", { class: "quality-label" }, category), bars, el("div", { class: "quality-score" }, el("span", {}, "A " + a + "/" + rows.length), el("span", {}, "B " + b + "/" + rows.length)));
    }));
  }
  function renderTokens() {
    if (!state.demo) { $("token-chart").replaceChildren(el("p", { class: "empty-cell" }, "消費量はまだ測定していません。")); return; }
    $("token-chart").replaceChildren(...DEMO_MODELS.map((model, index) => {
      const data = summarizeDemo(index ? "b" : "a");
      const input = el("div", { class: "token-input" }), output = el("div", { class: "token-output" });
      const largest = Math.max(summarizeDemo("a").total, summarizeDemo("b").total);
      input.style.width = data.prompt / largest * 100 + "%"; output.style.width = data.completion / largest * 100 + "%";
      return el("div", { class: "token-row" }, el("div", { class: "token-heading" }, model.name, el("strong", { class: "token-total" }, fmt(data.total))),
        el("div", { class: "token-bar", role: "img", "aria-label": model.name + " 入力" + data.prompt + "、出力" + data.completion }, input, output),
        el("div", { class: "token-detail" }, el("span", {}, "入力 " + fmt(data.prompt)), el("span", {}, "出力 " + fmt(data.completion))),
        el("div", { class: "token-helper" }, "思考 " + fmt(data.reasoning) + " は出力の内数 · キャッシュ " + fmt(data.cached) + " は入力の内数"),
        el("div", { class: "token-helper" }, "補助モデルの消費 " + fmt(data.helperTotal) + "（別集計）"));
    }));
  }
  function caseRow(item) {
    return el("tr", {}, el("td", {}, el("div", { class: "case-name" }, item.title), el("div", { class: "case-category" }, item.category + " · " + item.id)),
      el("td", {}, statusBadge(item.a)), el("td", {}, statusBadge(item.b)),
      el("td", {}, duration(item.secondsA) + " / " + duration(item.secondsB)),
      el("td", {}, el("button", { class: "detail-button", "aria-label": item.title + "の回答と根拠を開く", onclick: () => openCase(item) }, "回答と根拠 →")));
  }
  function renderCases() {
    const needle = $("case-search").value.trim().toLocaleLowerCase();
    const matches = state.demo ? CASES.filter(item => (item.title + item.category + item.id).toLocaleLowerCase().includes(needle)) : [];
    $("case-count").textContent = state.demo ? matches.length + " / " + CASES.length + " 件の架空例" : "実測結果なし";
    $("all-cases-body").replaceChildren(...(matches.length ? matches.map(caseRow) : [el("tr", {}, el("td", { colspan: "5", class: "empty-cell" }, "該当するケースはありません。"))]));
    $("recent-cases-body").replaceChildren(...(state.demo ? [CASES[0], CASES[3], CASES[4]].map(caseRow) : [el("tr", {}, el("td", { colspan: "5", class: "empty-cell" }, "回答・採点根拠はまだありません。"))]));
  }
  function renderResults() { renderMetrics(); renderComparison(); renderQuality(); renderTokens(); renderCases(); }
  function renderModelOptions() {
    $("model-options").replaceChildren(...state.models.map(model => {
      const checkbox = el("input", { type: "checkbox", checked: state.selectedModels.has(model.id), "aria-label": model.name + "を比較対象にする", onchange: event => { if (event.target.checked) state.selectedModels.add(model.id); else state.selectedModels.delete(model.id); renderSummary(); } });
      const engine = el("select", { "aria-label": model.name + "のエンジン設定", onchange: event => { model.engine.mtp = Number(event.target.value); renderSummary(); } },
        ...(model.family === "vLLM" ? [el("option", { value: "3" }, "BF16 KV / MTP3"), el("option", { value: "0" }, "BF16 KV / MTPなし")] : [el("option", { value: "0" }, "F16 KV / 標準設定")]));
      engine.value = String(model.engine.mtp);
      return el("div", { class: "model-option" }, el("label", { class: "model-choice" }, checkbox, el("div", {}, el("strong", {}, model.name), el("small", {}, model.family + " · 架空の登録例"))),
        el("span", { class: "tag" }, "未接続"), el("label", { class: "engine-field" }, "エンジン設定", engine));
    }));
  }
  function renderPresetOptions() {
    $("preset-options").replaceChildren(...state.presets.map(preset => el("label", { class: "preset-option" },
      el("input", { type: "checkbox", checked: state.selectedPresets.has(preset.id), onchange: event => { if (event.target.checked) state.selectedPresets.add(preset.id); else state.selectedPresets.delete(preset.id); renderSummary(); } }), preset.name)));
    $("edit-preset").replaceChildren(...state.presets.map(preset => el("option", { value: preset.id }, preset.name)));
    $("edit-preset").value = state.editingPreset;
  }
  function renderEditor() {
    const preset = state.presets.find(item => item.id === state.editingPreset);
    for (const [id, key] of [["preset-name", "name"], ["temperature", "temperature"], ["top-p", "topP"], ["context-tokens", "contextTokens"], ["output-tokens", "outputTokens"], ["thinking", "thinking"]]) $(id).value = Number.isNaN(preset[key]) ? "" : preset[key];
    renderDifference();
  }
  function renderDifference() {
    const preset = state.presets.find(item => item.id === state.editingPreset), error = validatePreset(preset);
    const errors = presetErrors(preset);
    for (const [id, key] of [["preset-name", "name"], ["temperature", "temperature"], ["top-p", "topP"], ["context-tokens", "contextTokens"], ["output-tokens", "outputTokens"], ["thinking", "thinking"]]) {
      $(id).setAttribute("aria-invalid", String(Boolean(errors[key])));
    }
    const labels = { temperature: "温度", topP: "top-p", contextTokens: "コンテキスト", outputTokens: "一要求の最大出力", thinking: "思考" };
    const display = (key, value) => key === "thinking" ? (value === "off" ? "思考なし" : "モデル固有の最大設定") : value;
    const changes = Object.entries(labels).filter(([key]) => preset[key] !== BASE_PRESET[key]).map(([key, label]) => label + "：" + display(key, BASE_PRESET[key]) + " → " + display(key, preset[key]));
    $("preset-diff").classList.toggle("changed", Boolean(error || changes.length));
    $("preset-diff").textContent = error || (changes.length ? "基準との差分：" + changes.join(" / ") : "基準設定との差分はありません。");
  }
  function selectedPlan() { return buildPlan(state.models.filter(item => state.selectedModels.has(item.id)), state.presets.filter(item => state.selectedPresets.has(item.id)), state.suite, state.runSettings); }
  function renderSummary() {
    const suite = SUITES[state.suite];
    $("plan-models").textContent = state.selectedModels.size; $("plan-presets").textContent = state.selectedPresets.size;
    const count = state.runSettings.trials_per_case;
    const trialError = validateTrialCount(count), seedError = validateBaseSeed(state.runSettings.seed_policy.base_seed);
    $("trial-error").hidden = !trialError; $("trial-error").textContent = trialError || "";
    $("seed-error").hidden = !seedError; $("seed-error").textContent = seedError || "";
    $("trials-per-case").setAttribute("aria-invalid", String(Boolean(trialError)));
    $("base-seed").setAttribute("aria-invalid", String(Boolean(seedError)));
    document.querySelectorAll("[data-trials]").forEach(button => button.setAttribute("aria-pressed", String(Number(button.dataset.trials) === count)));
    $("plan-suite").textContent = suite.name + " · " + suite.cases + "ケース";
    $("plan-repetitions").textContent = trialError ? "未設定" : count.toLocaleString() + "回";
    $("plan-formula").textContent = trialError ? "試行回数を入力してください。" : state.selectedModels.size + "モデル × " + state.selectedPresets.size + "設定 × " + suite.cases + "ケース × " + count.toLocaleString() + "回";
    try {
      const plan = selectedPlan();
      $("plan-trials").textContent = plan.planned_trials.toLocaleString();
      $("show-plan").disabled = false; $("plan-warning").textContent = "接続と評価資産は未確認です。設定内容の確認のみ行えます。";
    } catch (error) {
      $("plan-trials").textContent = "—"; $("show-plan").disabled = true; $("plan-warning").textContent = error.message;
    }
  }
  function openCase(item) { state.activeCase = item; state.detail = "answer"; $("case-title").textContent = item.title; $("case-subtitle").textContent = item.category + " · " + item.id + " · 日本語の架空ケース"; renderDetail(); $("case-dialog").showModal(); }
  function renderDetail() {
    const item = state.activeCase;
    document.querySelectorAll("[data-detail]").forEach(button => { const active = button.dataset.detail === state.detail; button.classList.toggle("selected", active); button.setAttribute("aria-pressed", String(active)); });
    let content;
    if (state.detail === "answer") {
      content = el("div", {}, el("p", { class: "dialog-note" }, "依頼の例：" + item.question), el("div", { class: "answer-grid" },
        el("article", { class: "answer-block" }, el("h3", {}, "モデルA", "　", statusBadge(item.a)), el("pre", {}, item.answerA)),
        el("article", { class: "answer-block" }, el("h3", {}, "モデルB", "　", statusBadge(item.b)), el("pre", {}, item.answerB))));
    } else if (state.detail === "actions") {
      content = el("ul", { class: "evidence-list" }, el("li", {}, el("strong", {}, "01 · 依頼と資料を読む"), "この行は操作履歴の表示位置を示す架空の例です。"),
        el("li", {}, el("strong", {}, "02 · 回答または環境操作"), "実際の呼び出しでは要求・応答・時刻・状態差分を記録します。"),
        el("li", {}, el("strong", {}, "03 · 完了と保存"), "モデルA " + duration(item.secondsA) + " / モデルB " + duration(item.secondsB) + "（いずれも架空値）"));
    } else {
      content = el("ul", { class: "evidence-list" }, el("li", {}, el("strong", {}, "判定単位"), typeof item.a === "number" ? "最適化の連続点。正誤式の成功率へ加算しません。" : "同じcaseの正誤式判定。表示された成功・不成功は架空の例です。"),
        el("li", {}, el("strong", {}, "根拠と採点版"), "この試作に実際のテスト・原判定はありません。正式画面では固定した採点器と証拠への参照を表示します。"),
        el("li", {}, el("strong", {}, "対象モデルの消費"), "A：入力 " + fmt(item.inputA) + " ＋ 出力 " + fmt(item.outputA) + " / B：入力 " + fmt(item.inputB) + " ＋ 出力 " + fmt(item.outputB)));
    }
    $("case-detail-content").replaceChildren(content, el("p", { class: "detail-note" }, "UI表示用の架空データです。実際のモデル品質・速度を示すものではありません。"));
  }

  document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
  document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => showView(button.dataset.go)));
  document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => $(button.dataset.close).close()));
  document.querySelectorAll("[data-detail]").forEach(button => button.addEventListener("click", () => { state.detail = button.dataset.detail; renderDetail(); }));
  document.querySelectorAll("[data-metric-model]").forEach(button => button.addEventListener("click", () => { state.metricModel = button.dataset.metricModel; document.querySelectorAll("[data-metric-model]").forEach(item => { const selected = item === button; item.classList.toggle("selected", selected); item.setAttribute("aria-pressed", String(selected)); }); renderMetrics(); }));
  $("show-demo").addEventListener("change", event => { state.demo = event.target.checked; renderResults(); });
  $("case-search").addEventListener("input", renderCases);
  $("edit-preset").addEventListener("change", event => { state.editingPreset = event.target.value; renderEditor(); });
  for (const [id, key] of [["preset-name", "name"], ["temperature", "temperature"], ["top-p", "topP"], ["context-tokens", "contextTokens"], ["output-tokens", "outputTokens"], ["thinking", "thinking"]]) {
    $(id).addEventListener("input", event => { const preset = state.presets.find(item => item.id === state.editingPreset); preset[key] = ["name", "thinking"].includes(key) ? event.target.value : event.target.valueAsNumber; if (key === "name") renderPresetOptions(); renderDifference(); renderSummary(); });
  }
  $("trials-per-case").addEventListener("input", event => { state.runSettings.trials_per_case = event.target.valueAsNumber; renderSummary(); });
  $("base-seed").addEventListener("input", event => { state.runSettings.seed_policy.base_seed = event.target.valueAsNumber; renderSummary(); });
  document.querySelectorAll("[data-trials]").forEach(button => button.addEventListener("click", () => {
    state.runSettings.trials_per_case = Number(button.dataset.trials); $("trials-per-case").value = state.runSettings.trials_per_case; renderSummary();
  }));
  document.querySelectorAll('input[name="suite"]').forEach(input => input.addEventListener("change", event => { state.suite = event.target.value; renderSummary(); }));
  $("duplicate-preset").addEventListener("click", () => { const original = state.presets.find(item => item.id === state.editingPreset); const preset = { ...copy(original), id: "custom-" + state.presets.length, name: original.name + "のコピー" }; state.presets.push(preset); state.editingPreset = preset.id; state.selectedPresets.add(preset.id); renderPresetOptions(); renderEditor(); renderSummary(); toast("画面内に設定を複製しました。保存・送信はしていません。"); });
  $("show-plan").addEventListener("click", () => {
    const plan = selectedPlan();
    $("plan-description").textContent = plan.combinations.length + "構成 · " + plan.cases_per_configuration + "ケース × 各" + plan.run_settings.trials_per_case + "回 · 合計" + plan.planned_trials.toLocaleString() + "試行";
    $("plan-configurations").replaceChildren(...plan.combinations.map(({ model, preset }) => el("tr", {},
      el("td", {}, model.name, el("span", { class: "cell-foot" }, model.family + " · " + model.engine.kv.toUpperCase() + " KV · " + (model.engine.mtp ? "MTP" + model.engine.mtp : "MTPなし"))),
      el("td", {}, preset.name, el("span", { class: "cell-foot" }, "温度 " + preset.temperature + " · top-p " + preset.topP)),
      el("td", {}, (preset.contextTokens / 1024) + "K / " + (preset.outputTokens / 1024) + "K"))));
    $("plan-json").textContent = JSON.stringify(plan, null, 2);
    $("plan-technical").open = false;
    $("plan-dialog").showModal();
  });
  document.querySelector(".brand").addEventListener("click", event => { event.preventDefault(); showView("overview"); });
  window.addEventListener("hashchange", () => showView(location.hash.slice(1)));
  renderResults(); renderModelOptions(); renderPresetOptions(); renderEditor(); renderSummary();
  showView(location.hash.slice(1));
}
