"use strict";
const state = { boot: null, selectedModels: new Set(), selectedPresets: new Set(), presets: [], suiteId: null, editing: null, settings: null, activeRun: null, resultRun: null, result: null, resolvedPlan: null, detail: null, view: "configure", pollBusy: false };
const $ = id => document.getElementById(id);
const copy = value => JSON.parse(JSON.stringify(value));
const activePhases = new Set(["queued", "preflight", "generating", "scoring"]);
const labels = { queued: "開始準備", preflight: "接続・資産確認", generating: "生成中", scoring: "採点中", completed: "完了", interrupted: "停止", infrastructure_error: "実行環境のエラー", evaluation_incomplete: "採点未完了" };
const presetInputs = { "preset-name": "name", temperature: "temperature", "top-p": "top_p", "top-k": "top_k", "min-p": "min_p", "presence-penalty": "presence_penalty", "frequency-penalty": "frequency_penalty", "repetition-penalty": "repetition_penalty", "context-tokens": "context_tokens", "output-tokens": "output_tokens", thinking: "thinking" };
let pollTimer, toastTimer;
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "checked" || key === "disabled" || key === "value") node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
function showError(message) { $("global-error").hidden = !message; $("global-error").textContent = message || ""; }
function toast(message) { $("toast").textContent = message; $("toast").classList.add("visible"); clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").classList.remove("visible"), 3500); }
async function api(path, value) {
  const response = await fetch(path, { method: value === undefined ? "GET" : "POST", cache: "no-store", headers: value === undefined ? {} : { "Content-Type": "application/json", "X-Beacon-Token": state.boot.csrf_token }, body: value === undefined ? undefined : JSON.stringify(value) });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "処理に失敗しました。");
  return body;
}
function action(fn) { return async event => { try { showError(""); await fn(event); } catch (error) { showError(error.message || "接続が途切れました。実行状態は更新できていません。"); } }; }
function fmt(value) { return value == null ? "—" : value >= 1000 ? (value / 1000).toLocaleString("ja-JP", { maximumFractionDigits: 1 }) + "K" : Number(value).toLocaleString("ja-JP", { maximumFractionDigits: 2 }); }
function capacity(value) { return value % 1024 === 0 ? (value / 1024).toLocaleString("ja-JP") + "K" : Number(value).toLocaleString("ja-JP"); }
function seconds(value) { if (value == null) return "—"; const n = Math.round(value); return n >= 3600 ? Math.floor(n / 3600) + "時間" + Math.floor(n % 3600 / 60) + "分" : n >= 60 ? Math.floor(n / 60) + "分" + n % 60 + "秒" : value.toFixed(1) + "秒"; }
function date(value) { return value ? new Date(value).toLocaleString("ja-JP", { hour12: false }) : "—"; }
function showView(name) {
  if (!["configure", "runs", "overview"].includes(name)) name = "configure";
  state.view = name;
  document.querySelectorAll(".view").forEach(node => node.classList.toggle("hidden", node.id !== "view-" + name));
  document.querySelectorAll("[data-view]").forEach(node => { const selected = node.dataset.view === name; node.classList.toggle("active", selected); if (selected) node.setAttribute("aria-current", "page"); else node.removeAttribute("aria-current"); });
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  window.scrollTo(0, 0); $(name + "-heading").focus({ preventScroll: true });
  if (name === "runs") refreshProgress().catch(error => showError(error.message));
  if (name === "overview") refreshResults().catch(error => showError(error.message));
}
async function refreshBootstrap(initial = false) {
  const boot = await api("/api/bootstrap");
  state.boot = boot;
  if (initial) {
    state.presets = copy(boot.presets); state.editing = state.presets[0]?.id;
    if (state.editing) state.selectedPresets.add(state.editing);
    if (boot.models[0]) state.selectedModels.add(boot.models[0].id);
    state.suiteId = boot.suites.find(row => row.execution_ready && row.track === "benchmark")?.id || boot.suites.find(row => row.execution_ready && row.track !== "diagnostic")?.id || boot.suites.find(row => row.execution_ready)?.id || null;
    state.settings = copy(boot.run_defaults);
    $("trials-per-case").value = state.settings.trials_per_case; $("base-seed").value = state.settings.seed_policy.base_seed;
    state.activeRun = boot.runs[0]?.run_id || null; state.resultRun = boot.runs[0]?.run_id || null;
    renderPresets(); renderEditor();
  }
  $("connection-state").textContent = "接続済み · 最終更新 " + new Date().toLocaleTimeString("ja-JP", { hour12: false });
  renderSuites(); renderModels(); renderRunPickers(); renderSummary();
}
function renderSuites() {
  $("suite-options").replaceChildren(...state.boot.suites.map(row => el("label", { class: "suite-option" },
    el("input", { type: "radio", name: "suite", value: row.id, checked: row.id === state.suiteId, disabled: !row.execution_ready, onchange: () => { state.suiteId = row.id; renderSummary(); } }),
    el("span", {}, el("strong", {}, row.name), el("small", {}, row.case_count + "ケース · " + (row.language === "ja" ? "日本語" : row.language))),
    row.execution_ready ? el("span", { class: "tag" }, row.track === "diagnostic" ? "接続診断" : row.track === "development" ? "検証用" : "実行可能") : el("p", { class: "inline-note" }, row.unavailable_reason))));
}
function renderModels() {
  if (!state.boot.models.length) { $("model-options").replaceChildren(el("p", { class: "inline-note" }, "モデルが未登録です。上の「モデルを追加」から接続を登録してください。")); return; }
  $("model-options").replaceChildren(...state.boot.models.map(model => {
    const connected = model.connection?.available;
    const button = el("button", { class: "button small", onclick: action(async () => { button.disabled = true; try { model.connection = await api("/api/models/" + encodeURIComponent(model.id) + "/probe", {}); renderModels(); toast(model.connection.message); } finally { button.disabled = false; } }) }, "接続確認");
    return el("div", { class: "model-option" }, el("label", { class: "model-choice" }, el("input", { type: "checkbox", checked: state.selectedModels.has(model.id), "aria-label": model.name + "を比較対象にする", onchange: event => { if (event.target.checked) state.selectedModels.add(model.id); else state.selectedModels.delete(model.id); renderSummary(); } }),
      el("div", {}, el("strong", {}, model.name), el("small", {}, model.api_model), el("small", {}, model.engine.label || model.adapter))),
      el("div", { class: "model-status" }, el("span", { class: connected ? "connected" : "unverified" }, connected === true ? "✓ 接続確認済み" : connected === false ? "接続を確認してください" : "接続未確認"), button),
      el("small", { class: "inline-note" }, "容量 " + capacity(model.context_tokens) + " / 出力 " + capacity(model.output_tokens) + " · エンジンは登録構成を使用"));
  }));
}
function renderPresets() {
  $("preset-options").replaceChildren(...state.presets.map(preset => el("label", { class: "preset-option" }, el("input", { type: "checkbox", checked: state.selectedPresets.has(preset.id), onchange: event => { if (event.target.checked) state.selectedPresets.add(preset.id); else state.selectedPresets.delete(preset.id); renderSummary(); } }), preset.name)));
  $("edit-preset").replaceChildren(...state.presets.map(row => el("option", { value: row.id }, row.name))); $("edit-preset").value = state.editing;
}
function renderEditor() { const preset = state.presets.find(row => row.id === state.editing); if (!preset) return; for (const [id, key] of Object.entries(presetInputs)) $(id).value = Number.isNaN(preset[key]) ? "" : preset[key]; }
function configurationError() {
  const n = state.settings?.trials_per_case;
  if (!Number.isSafeInteger(n) || n < 1) return "試行回数を1以上の整数で入力してください。";
  const seed = state.settings.seed_policy.base_seed;
  if (!Number.isSafeInteger(seed) || seed < 0 || seed > 2147483647) return "基底seedを0〜2147483647の整数で入力してください。";
  if (!state.suiteId) return "実行可能な評価セットを選択してください。";
  if (!state.selectedModels.size || !state.selectedPresets.size) return "モデルとプリセットをそれぞれ1つ以上選択してください。";
  for (const row of state.presets.filter(item => state.selectedPresets.has(item.id))) {
    if (!row.name.trim()) return "プリセット名を入力してください。";
    if (!Number.isFinite(row.temperature) || row.temperature < 0 || row.temperature > 2) return "温度を0〜2で入力してください。";
    if (!Number.isFinite(row.top_p) || row.top_p <= 0 || row.top_p > 1) return "top-pを0より大きく1以下で入力してください。";
    if (!Number.isSafeInteger(row.context_tokens) || !Number.isSafeInteger(row.output_tokens) || row.output_tokens < 1 || row.output_tokens >= row.context_tokens) return "コンテキストと最大出力を確認してください。";
  }
  return null;
}
function renderSummary() {
  if (!state.boot) return;
  const suite = state.boot.suites.find(row => row.id === state.suiteId), n = state.settings.trials_per_case, validCount = Number.isSafeInteger(n) && n > 0;
  const error = configurationError();
  $("plan-models").textContent = state.selectedModels.size; $("plan-presets").textContent = state.selectedPresets.size;
  $("plan-cases").textContent = suite?.case_count ?? "—"; $("plan-repetitions").textContent = validCount ? n.toLocaleString() + "回" : "未設定";
  const total = state.selectedModels.size * state.selectedPresets.size * (suite?.case_count || 0) * n;
  $("plan-trials").textContent = !error && Number.isSafeInteger(total) ? total.toLocaleString() : "—";
  $("plan-formula").textContent = validCount && suite ? state.selectedModels.size + "モデル × " + state.selectedPresets.size + "設定 × " + suite.case_count + "ケース × " + n + "回" : "";
  $("plan-warning").textContent = error || (suite.track === "diagnostic" ? "接続診断です。通常能力の得点には含めません。" : "固定した問題と採点器を使います。実行前に条件を確認できます。");
  $("prepare-plan").disabled = Boolean(error) || !Number.isSafeInteger(total);
  $("trial-error").hidden = validCount; $("trial-error").textContent = validCount ? "" : "試行回数を1以上の整数で入力してください。";
  $("trials-per-case").setAttribute("aria-invalid", String(!validCount));
  document.querySelectorAll("[data-trials]").forEach(button => button.setAttribute("aria-pressed", String(Number(button.dataset.trials) === n)));
}
function renderRunPickers() {
  const runs = state.boot.runs;
  for (const [id, selected] of [["active-run", state.activeRun], ["result-run", state.resultRun]]) {
    $(id).replaceChildren(...(runs.length ? runs.map(row => el("option", { value: row.run_id }, date(row.created_at) + " · " + row.suite.name + " · " + (labels[row.phase] || row.phase))) : [el("option", { value: "" }, "実行はまだありません")]));
    if (selected && runs.some(row => row.run_id === selected)) $(id).value = selected;
  }
}
async function refreshProgress() {
  if (!state.activeRun) { $("progress-content").replaceChildren(emptyState("実行はまだありません", "比較条件を選んで開始すると、進捗をここで確認できます。")); return; }
  const current = await api("/api/runs/" + encodeURIComponent(state.activeRun));
  const panel = el("section", { class: "panel progress-panel" }, el("div", { class: "progress-header" }, el("h2", {}, "現在の実行"), el("span", { class: "phase-badge" + (["infrastructure_error", "evaluation_incomplete"].includes(current.phase) ? " error" : "") }, labels[current.phase] || current.phase)),
    el("p", { class: "inline-note" }, "開始 " + date(current.created_at) + " · 更新 " + date(current.updated_at)),
    el("div", { class: "progress-counts" }, ...[["生成済み", current.completed], ["採点済み", current.scored], ["別の実行から再利用", current.reused]].map(([title, count]) => el("div", { class: "progress-count" }, el("span", {}, title), el("strong", {}, count + " / " + current.planned)))),
    current.active_case ? el("div", { class: "active-case" }, el("strong", {}, current.active_model?.name || ""), el("p", {}, current.active_case.title + " · " + (current.active_case.trial_index + 1) + "回目"), el("p", {}, "今回の要求 " + seconds(current.request_elapsed_seconds) + " · 受信 " + fmt(current.received_characters) + "文字（トークン数の推定ではありません）")) : null,
    el("p", {}, ({ generating: "回答を生成しています。途中の原本も保存しています。", scoring: "保存済みの回答を採点しています。" }[current.phase]) || current.message || "保存と進捗を更新しています。"), el("div", { class: "progress-actions" },
      activePhases.has(current.phase) ? el("button", { class: "button", onclick: action(async () => { await api("/api/runs/" + state.activeRun + "/stop", {}); toast("停止を要求しました。保存が終わるまで状態を確認します。"); }) }, "一時停止") : current.phase !== "completed" ? el("button", { class: "button", onclick: action(async () => { await api("/api/runs/" + state.activeRun + "/resume", {}); await refreshBootstrap(); await refreshProgress(); }) }, "保存回答を使って再開") : null,
      el("button", { class: "button primary", onclick: () => { state.resultRun = state.activeRun; renderRunPickers(); showView("overview"); } }, "回答と結果を見る")));
  $("progress-content").replaceChildren(panel, el("div", { class: "run-list" }, ...state.boot.runs.map(row => el("article", { class: "panel run-card" }, el("div", {}, el("strong", {}, row.suite.name), el("p", {}, date(row.created_at) + " · " + row.models.join(" / ")), el("p", {}, (labels[row.phase] || row.phase) + " · 生成 " + row.completed + "/" + row.planned + " · 採点 " + row.scored + "/" + row.planned)), el("button", { class: "button small", onclick: () => { state.activeRun = row.run_id; renderRunPickers(); refreshProgress().catch(error => showError(error.message)); } }, "表示する")))));
}
function emptyState(title, description) { return el("div", { class: "panel empty-state" }, el("h2", {}, title), el("p", {}, description), el("button", { class: "button primary", onclick: () => showView("configure") }, "比較を作成")); }
function nullableSum(values) { return values.length && values.every(value => value != null) ? values.reduce((sum, value) => sum + value, 0) : null; }
function aggregate(rows) {
  const binary = rows.filter(row => row.grade?.status === "scored" && row.grade.metric_unit === "binary");
  const target = rows.map(row => row.trial.role_totals.target);
  const secondsTotal = nullableSum(rows.map(row => row.trial.task_wall_seconds));
  const output = nullableSum(target.map(row => row.completion_tokens)), requestWall = nullableSum(target.map(row => row.request_wall_seconds));
  return { correct: binary.filter(row => row.grade.passed).length, binary: binary.length, total: nullableSum(target.map(row => row.total_tokens)), prompt: nullableSum(target.map(row => row.prompt_tokens)), output,
    seconds: secondsTotal, requestThroughput: output != null && requestWall > 0 ? output / requestWall : null,
    gradeSeconds: nullableSum(rows.filter(row => row.grade?.status === "scored").map(row => row.grade.grade_wall_seconds)),
    scores: rows.filter(row => row.grade?.status === "scored" && row.grade.metric_unit !== "binary" && row.grade.score != null), reused: rows.filter(row => row.reused).length };
}
async function refreshResults() {
  if (!state.resultRun) { $("results-content").replaceChildren(emptyState("評価結果はまだありません", "実モデルの評価が進むと、保存した回答と採点結果が表示されます。")); return; }
  state.result = await api("/api/runs/" + encodeURIComponent(state.resultRun) + "/results"); renderResults();
}
function renderResults() {
  const result = state.result, rows = result.rows || [];
  if (!rows.length) { $("results-content").replaceChildren(emptyState("回答の生成を待っています", "実行状況で現在の段階を確認できます。")); return; }
  const combinations = result.combinations;
  const head = el("div", { class: "result-scope" }, el("span", { class: "scope-chip" }, result.suite.name), el("span", { class: "subtle" }, result.suite.case_count + "ケース · 各" + result.run_settings.trials_per_case + "回 · " + result.suite.language));
  const first = combinations[0], firstRows = rows.filter(row => row.combination_id === first.id), data = aggregate(firstRows);
  const metrics = [["正誤式の成功", data.binary ? data.correct + " / " + data.binary : "—", "試行", "採点済みの正誤式のみ"], ["回答生成の消費", fmt(data.total), "tokens", "入力 " + fmt(data.prompt) + " ＋ 出力 " + fmt(data.output)], ["課題完了時間", seconds(data.seconds), "", "対話・ツールの時間を含む"], ["要求スループット", fmt(data.requestThroughput), "token/s", "入力処理・待機を含む値"], ["採点時間", seconds(data.gradeSeconds), "", "保存回答の採点にかかった時間"]];
  const cards = el("div", { class: "metric-grid" }, ...metrics.map(([label, value, unit, foot]) => el("div", { class: "metric-card" }, el("div", { class: "metric-label" }, label), el("div", { class: "metric-value" }, value, el("small", {}, unit)), el("div", { class: "metric-foot" }, foot))));
  const table = el("table", { class: "result-table" }, el("thead", {}, el("tr", {}, ...["モデル / 設定", "正誤式の成功", "連続得点", "回答生成の消費", "課題完了時間", "要求スループット"].map(value => el("th", { scope: "col" }, value)))), el("tbody", {}, ...combinations.map(combination => {
    const values = aggregate(rows.filter(row => row.combination_id === combination.id));
    return el("tr", {}, el("td", {}, el("div", { class: "model-name" }, combination.model.name), el("span", { class: "cell-foot" }, combination.preset.name + " · 温度 " + combination.preset.temperature)), el("td", {}, values.binary ? values.correct + " / " + values.binary : "—"), el("td", {}, values.scores.length ? values.scores.map(row => row.title + ": " + fmt(row.grade.score) + " " + row.grade.metric_unit).join(" / ") : "対象外"), el("td", {}, fmt(values.total), el("span", { class: "cell-foot" }, values.reused ? "再利用した回答の計測値を含む" : "入力＋出力")), el("td", {}, seconds(values.seconds)), el("td", {}, fmt(values.requestThroughput) + " tok/s"));
  })));
  const caseTable = el("table", {}, el("thead", {}, el("tr", {}, ...["課題", "モデル / 設定", "試行", "判定", "完了時間", "明細"].map(value => el("th", { scope: "col" }, value)))), el("tbody", {}, ...rows.map(row => {
    const combination = combinations.find(item => item.id === row.combination_id), grade = row.grade;
    const judged = grade?.status === "scored";
    const status = !judged ? ({ infrastructure_error: "採点器エラー", invalid_instrument: "採点条件の不備" }[grade?.status] || "採点待ち") : grade.metric_unit === "binary" ? grade.passed ? "✓ 成功" : "− 不成功" : fmt(grade.score) + " " + grade.metric_unit;
    return el("tr", {}, el("td", {}, row.title, el("span", { class: "cell-foot" }, row.category)), el("td", {}, combination.model.name, el("span", { class: "cell-foot" }, combination.preset.name)), el("td", {}, row.trial_index + 1 + "回目"), el("td", {}, el("span", { class: "status-badge " + (!judged ? "score" : grade.passed ? "pass" : grade.metric_unit === "binary" ? "fail" : "score") }, status)), el("td", {}, seconds(row.trial.task_wall_seconds)), el("td", {}, el("button", { class: "detail-button", "aria-label": row.title + "の回答と根拠を開く", onclick: action(() => openCase(row)) }, "回答と根拠 →")));
  })));
  const contents = [head,
    result.status?.phase !== "completed" ? el("p", { class: "result-note" }, "途中結果です。未生成・未採点を0点として集計していません。") : null,
    result.suite.track === "diagnostic" ? el("p", { class: "result-note" }, "接続・計測・採点器の動作確認です。通常能力のベンチマーク得点ではありません。") : null,
    result.suite.track === "development" ? el("p", { class: "result-note" }, "接続・実行の検証用に抽出した問題です。Quick / Detailedや公式ベンチマーク全体の得点とは分けて表示しています。") : null,
    el("p", { class: "subtle" }, "上部の指標：" + first.model.name + " / " + first.preset.name), cards,
    el("div", { class: "section-title" }, el("h2", {}, "モデル別の結果")), region(table, "モデル別の結果表"),
    el("p", { class: "inline-note" }, "保存済み回答の計測値を集計しています。異なるトークナイザーのtoken/sは同じ作業量を意味しません。純粋なdecode速度は、エンジンが報告した場合のみ明細に表示します。"),
    result.observed_usage_in_this_run ? el("p", { class: "inline-note" }, "この実行で実際に観測した要求：" + Object.values(result.observed_usage_in_this_run).reduce((sum, value) => sum + value.requests, 0) + "件。消費 " + fmt(nullableSum(Object.values(result.observed_usage_in_this_run).map(value => value.total_tokens))) + " tokens（途中で止まった要求を含み、他の実行からの回答再利用分は含みません）。") : null,
    ...result.warnings.map(value => el("p", { class: "inline-note" }, value)),
    el("div", { class: "section-title" }, el("h2", {}, "回答と採点根拠")), region(caseTable, "ケース別の結果表")];
  $("results-content").replaceChildren(...contents.filter(node => node !== null && node !== undefined));
}
function region(child, label) { return el("div", { class: "panel table-scroll", role: "region", "aria-label": label + "。横スクロールできます", tabindex: "0" }, child); }
async function openCase(row) {
  const query = new URLSearchParams({ combination: row.combination_id, case_id: row.case_id, trial: String(row.trial_index) });
  const path = "/api/runs/" + state.resultRun + "/case?" + query;
  state.detail = await api(path + "&view=review");
  $("case-title").textContent = row.title;
  const categories = { office_work: "事務・連携", shopping_planning: "購買計画", travel_planning: "旅程計画", tool_and_policy: "規則とツール", compound_work: "複合業務", crm_sales: "営業実務", optimization: "最適化", code_generation: "コード生成", sales_dialogue: "営業対話", scientific_code: "科学・数値実装" };
  $("case-subtitle").textContent = (categories[row.category] || row.category) + " · " + (row.trial_index + 1) + "回目";
  $("case-detail-content").replaceChildren(BeaconReview.render(state.detail, (kind, call) => api(path + "&view=trace&kind=" + kind + "&call=" + call)));
  $("case-dialog").showModal(); $("case-dialog").scrollTop = 0;
}
async function preparePlan() {
  const button = $("prepare-plan"); button.disabled = true;
  try {
    const payload = { model_ids: [...state.selectedModels], presets: state.presets.filter(row => state.selectedPresets.has(row.id)), suite_id: state.suiteId, run_settings: state.settings };
    const plan = await api("/api/plans", payload); state.resolvedPlan = plan;
    $("plan-description").textContent = plan.combinations.length + "構成 · " + plan.case_count + "ケース × 各" + plan.run_settings.trials_per_case + "回 · 合計" + plan.planned_trials + "試行";
    $("resolved-plan").replaceChildren(...plan.warnings.map(value => el("p", { class: "dialog-note" }, value)), region(el("table", {}, el("thead", {}, el("tr", {}, ...["モデル / エンジン", "生成設定", "コンテキスト / 最大出力"].map(value => el("th", { scope: "col" }, value)))), el("tbody", {}, ...plan.combinations.map(row => el("tr", {}, el("td", {}, row.model.name, el("span", { class: "cell-foot" }, row.model.engine.label || row.model.adapter)), el("td", {}, row.preset.name, el("span", { class: "cell-foot" }, "温度 " + row.preset.temperature + " · top-p " + row.preset.top_p + " · top-k " + row.preset.top_k)), el("td", {}, capacity(row.preset.context_tokens) + " / " + capacity(row.preset.output_tokens)))))), "実行する構成"));
    $("plan-json").textContent = JSON.stringify(plan, null, 2); $("plan-dialog").showModal();
  } finally { renderSummary(); }
}
async function poll() {
  if (state.pollBusy) return; state.pollBusy = true;
  try { await refreshBootstrap(); if (state.view === "runs") await refreshProgress(); if (state.view === "overview") await refreshResults(); showError(""); }
  catch (error) { $("connection-state").textContent = "更新が途切れています。実行の停止とは限りません。"; showError(error.message); }
  finally { state.pollBusy = false; }
}
function schedulePoll() { clearInterval(pollTimer); const n = Number($("poll-seconds").value); if (n > 0) pollTimer = setInterval(poll, n * 1000); }

if (typeof document !== "undefined") {
  document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
  document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => showView(button.dataset.go)));
  document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => $(button.dataset.close).close()));
  document.querySelector(".brand").addEventListener("click", event => { event.preventDefault(); showView("configure"); });
  $("refresh").addEventListener("click", action(poll)); $("poll-seconds").addEventListener("change", schedulePoll);
  $("trials-per-case").addEventListener("input", event => { state.settings.trials_per_case = event.target.valueAsNumber; renderSummary(); });
  $("base-seed").addEventListener("input", event => { state.settings.seed_policy.base_seed = event.target.valueAsNumber; renderSummary(); });
  document.querySelectorAll("[data-trials]").forEach(button => button.addEventListener("click", () => { state.settings.trials_per_case = Number(button.dataset.trials); $("trials-per-case").value = state.settings.trials_per_case; renderSummary(); }));
  $("edit-preset").addEventListener("change", event => { state.editing = event.target.value; renderEditor(); });
  for (const [id, key] of Object.entries(presetInputs)) $(id).addEventListener("input", event => { const preset = state.presets.find(row => row.id === state.editing); preset[key] = ["name", "thinking"].includes(key) ? event.target.value : event.target.valueAsNumber; if (key === "name") renderPresets(); $("preset-message").textContent = "編集中の設定は、この比較計画へ反映します。"; renderSummary(); });
  $("duplicate-preset").addEventListener("click", () => { const preset = copy(state.presets.find(row => row.id === state.editing)); if (!preset) return; preset.id = "custom-" + crypto.randomUUID().slice(0, 12); preset.name += "のコピー"; state.presets.push(preset); state.editing = preset.id; state.selectedPresets.add(preset.id); renderPresets(); renderEditor(); renderSummary(); });
  $("save-preset").addEventListener("click", action(async () => { const preset = state.presets.find(row => row.id === state.editing); await api("/api/presets", preset); $("preset-message").textContent = "プリセットを保存しました。過去の実行条件は変わりません。"; }));
  $("prepare-plan").addEventListener("click", action(preparePlan));
  $("start-run").addEventListener("click", action(async () => { $("start-run").disabled = true; try { const run = await api("/api/runs", { plan_id: state.resolvedPlan.plan_id }); $("plan-dialog").close(); state.activeRun = state.resultRun = run.run_id; await refreshBootstrap(); showView("runs"); } finally { $("start-run").disabled = false; } }));
  $("active-run").addEventListener("change", action(async event => { state.activeRun = event.target.value; await refreshProgress(); }));
  $("result-run").addEventListener("change", action(async event => { state.resultRun = event.target.value; await refreshResults(); }));
  $("add-model").addEventListener("click", () => { $("model-error").hidden = true; $("model-dialog").showModal(); });
  $("model-form").addEventListener("submit", async event => { event.preventDefault(); const values = Object.fromEntries(new FormData(event.target)); const model = { id: values.id, name: values.name, base_url: values.base_url, api_model: values.api_model, api_key_env: values.api_key_env, context_tokens: Number(values.context_tokens), output_tokens: Number(values.output_tokens), adapter: values.adapter, revision: values.revision, engine: { label: values.engine_label, reasoning_effort: values.reasoning_effort, resource_group: values.resource_group }, cache_policy: "shared-service-uncontrolled" }; try { await api("/api/models", model); state.selectedModels.add(model.id); $("model-dialog").close(); await refreshBootstrap(); toast("モデル接続を登録しました。接続確認を実行できます。"); } catch (error) { $("model-error").hidden = false; $("model-error").textContent = error.message; } });
  window.addEventListener("hashchange", () => showView(location.hash.slice(1)));
  refreshBootstrap(true).then(() => { showView(location.hash.slice(1)); schedulePoll(); }).catch(error => showError("画面サーバーへ接続できません。" + error.message));
}
