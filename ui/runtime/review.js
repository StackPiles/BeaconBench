/* Saved text is untrusted. Build DOM nodes; never execute embedded HTML. */
(function (root) {
  "use strict";
  const metricNames = { native_reward_0_1: "報酬（0〜1）", shopping_case_score_0_1: "商品選択（0〜1）", travel_case_acc_0_1: "旅程の達成度（0〜1）", crm_reward_0_1: "回答の一致度（0〜1）", salesllm_score_0_10: "営業スコア（0〜10）", binary: "正誤判定", claw_task_score_0_1: "課題スコア（0〜1）", frontier_native_points: "ネイティブ得点" };
  const fieldNames = { to: "宛先", subject: "件名", body: "本文", content: "内容", reply_to_message_id: "返信元ID", start: "開始", end: "終了", title: "タイトル", description: "説明" };
  function node(tag, attrs = {}, ...children) {
    const result = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") result.className = value;
      else if (key.startsWith("on")) result.addEventListener(key.slice(2), value);
      else result.setAttribute(key, value);
    }
    for (const child of children.flat(Infinity)) if (child != null) result.append(typeof child === "object" ? child : document.createTextNode(String(child)));
    return result;
  }
  function plain(value) {
    if (value == null) return "記録なし";
    if (typeof value === "boolean") return value ? "はい" : "いいえ";
    if (typeof value === "number") return value.toLocaleString("ja-JP", { maximumFractionDigits: 4 });
    if (Array.isArray(value)) return value.map(plain).join("、");
    if (typeof value === "object") return Object.entries(value).map(([key, item]) => key + ": " + plain(item)).join("\n");
    return String(value);
  }
  function inline(text) {
    const part = document.createDocumentFragment();
    for (const token of text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g)) {
      if (token.startsWith("`") && token.endsWith("`")) part.append(node("code", {}, token.slice(1, -1)));
      else if (token.startsWith("**") && token.endsWith("**")) part.append(node("strong", {}, token.slice(2, -2)));
      else part.append(document.createTextNode(token));
    }
    return part;
  }
  function markdown(value) {
    const result = node("div", { class: "review-prose" });
    const lines = String(value || "").replace(/\r\n?/g, "\n").split("\n");
    const cells = line => line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map(item => item.trim().replace(/\\\|/g, "|"));
    const divider = line => /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
    for (let index = 0; index < lines.length;) {
      const line = lines[index];
      if (!line.trim()) { index++; continue; }
      const fence = line.match(/^\s*(`{3,}|~{3,})(.*)$/);
      if (fence) {
        const language = fence[2].trim(); const body = []; index++;
        while (index < lines.length && !new RegExp("^\\s*" + fence[1][0] + "{" + fence[1].length + ",}\\s*$").test(lines[index])) body.push(lines[index++]);
        if (index < lines.length) index++;
        result.append(node("div", { class: "review-code" }, language ? node("div", { class: "code-label" }, language) : null, node("pre", {}, node("code", {}, body.join("\n")))));
        continue;
      }
      if (index + 1 < lines.length && line.includes("|") && divider(lines[index + 1])) {
        const head = cells(line); index += 2; const body = [];
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) body.push(cells(lines[index++]));
        result.append(node("div", { class: "review-table-wrap", role: "region", "aria-label": "本文中の表", tabindex: "0" },
          node("table", {}, node("thead", {}, node("tr", {}, ...head.map(item => node("th", { scope: "col" }, inline(item))))),
            node("tbody", {}, ...body.map(row => node("tr", {}, ...row.map(item => node("td", {}, inline(item)))))))));
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) { result.append(node("h" + Math.min(heading[1].length + 2, 6), {}, inline(heading[2]))); index++; continue; }
      const list = line.match(/^\s*(?:([-*+])|\d+[.)])\s+(.+)$/);
      if (list) {
        const tag = list[1] ? "ul" : "ol"; const rows = [];
        while (index < lines.length) {
          const item = lines[index].match(/^\s*(?:([-*+])|\d+[.)])\s+(.+)$/);
          if (!item || (item[1] ? "ul" : "ol") !== tag) break;
          rows.push(node("li", {}, inline(item[2]))); index++;
        }
        result.append(node(tag, {}, ...rows)); continue;
      }
      if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) { result.append(node("hr")); index++; continue; }
      const paragraph = [line]; index++;
      while (index < lines.length && lines[index].trim() && !/^\s*(`{3,}|~{3,}|#{1,6}\s|[-*+]\s|\d+[.)]\s)/.test(lines[index]) && !(index + 1 < lines.length && divider(lines[index + 1]))) paragraph.push(lines[index++]);
      result.append(node("p", {}, inline(paragraph.join("\n"))));
    }
    return result;
  }
  function badge(value) { return node("span", { class: "review-check " + (value === true ? "yes" : value === false ? "no" : "unknown") }, value === true ? "✓ 適合" : value === false ? "× 不適合" : "— 不明"); }
  function factList(items) { return node("dl", { class: "review-facts" }, ...items.map(item => node("div", {}, node("dt", {}, item.label), node("dd", {}, plain(item.value))))); }
  function disclosure(title, body, className = "") { return node("details", { class: "review-disclosure " + className }, node("summary", {}, title), body); }
  function gradeSection(section) {
    if (section.collapsible) return disclosure(section.title, factList(section.items));
    const content = node("section", { class: "grade-section" }, node("h4", {}, section.title));
    if (section.kind === "facts") content.append(factList(section.items));
    else if (section.kind === "checks") {
      const list = items => node("ul", { class: "review-checks" }, ...items.map(item => node("li", {},
        node("div", { class: "check-title" }, node("span", {}, item.label), badge(item.passed)), item.detail ? markdown(item.detail) : null)));
      if (section.items.length > 8) {
        const passed = section.items.filter(item => item.passed === true);
        const others = section.items.filter(item => item.passed !== true);
        content.append(node("p", { class: "review-caption" }, "全" + section.items.length + "項目 · 適合 " + passed.length + " · 不適合 " + section.items.filter(item => item.passed === false).length + " · 不明 " + section.items.filter(item => item.passed == null).length));
        if (others.length) content.append(list(others));
        if (passed.length) content.append(disclosure("適合した " + passed.length + " 項目も見る", list(passed)));
      } else content.append(list(section.items));
    }
    else {
      if (section.score != null) content.append(node("p", { class: "criterion-score" }, "スコア ", node("strong", {}, plain(section.score))));
      if (section.text) content.append(markdown(section.text));
      if (section.rubric) content.append(disclosure(section.rubric_partial ? "記録された評価基準（抜粋）" : "評価基準", markdown(section.rubric)));
    }
    return content;
  }
  function verdict(grade) {
    if (!grade) return "採点待ち";
    if (grade.status !== "scored") return "採点未完了";
    if (grade.unit === "binary") return grade.passed === true ? "合格" : grade.passed === false ? "不合格" : "判定不明";
    return grade.score == null ? "採点済み" : plain(grade.score) + (grade.unit === "salesllm_score_0_10" ? " / 10" : grade.unit?.endsWith("_0_1") ? " / 1" : " 点");
  }
  function render(data, loadRaw) {
    const review = data.review; const firstGrade = review?.grading?.at(-1);
    if (!review) return node("p", { class: "review-empty" }, "回答はまだ保存されていません。");
    const problem = node("section", { class: "review-panel review-question", id: "review-question", "aria-labelledby": "review-question-title" },
      node("div", { class: "review-section-heading" }, node("span", { class: "section-number" }, "01"), node("h3", { id: "review-question-title" }, "問題内容")),
      node("div", { class: "review-question-text", tabindex: "0", role: "region", "aria-label": "問題本文（全文）" }, markdown(review.question.text || "保存記録に問題本文がありません。")));
    if (review.question.instructions) problem.append(disclosure("共通の指示・利用できる機能", markdown(review.question.instructions)));
    const response = node("section", { class: "review-panel review-response", id: "review-answer", "aria-labelledby": "review-answer-title" },
      node("div", { class: "review-section-heading" }, node("span", { class: "section-number" }, "02"), node("h3", { id: "review-answer-title" }, "回答内容"),
        node("span", { class: "review-caption" }, review.response.kind === "conversation" ? "対話の全文" : "保存されたモデルの回答")));
    const messages = node("div", { class: "review-messages", tabindex: "0", role: "region", "aria-label": "回答の全文" });
    for (const [index, message] of review.response.messages.entries()) {
      const dialogue = review.response.kind === "conversation";
      messages.append(node("article", { class: "review-message " + message.role }, dialogue ? node("div", { class: "message-role" }, message.role === "assistant" ? "評価対象モデル" : "相手役", node("span", {}, String(index + 1).padStart(2, "0"))) : null,
        message.content ? markdown(message.content) : node("p", { class: "review-empty" }, "本文のない発言（操作などの詳細は記録を参照）")));
    }
    response.append(messages);
    for (const [index, artifact] of review.response.artifacts.entries()) {
      const title = artifact.title.includes("save_draft") ? "メール草案の保存内容" : "作成・変更の内容";
      const body = node("div");
      for (const [key, value] of Object.entries(artifact.fields)) body.append(node("div", { class: "artifact-field" }, node("h5", {}, fieldNames[key] || key), markdown(plain(value))));
      const card = disclosure(title + " " + (index + 1) + (artifact.fields.subject ? " · " + artifact.fields.subject : ""), body, "review-artifact");
      if (index === 0) card.setAttribute("open", "");
      response.append(card);
    }
    const grading = node("section", { class: "review-panel review-grading", id: "review-grading", "aria-labelledby": "review-grade-title" },
      node("div", { class: "review-section-heading" }, node("span", { class: "section-number" }, "03"), node("h3", { id: "review-grade-title" }, "採点根拠")));
    for (const grade of review.grading) grading.append(node("div", { class: "review-grade-version" },
      node("div", { class: "review-verdict " + (grade.unit === "binary" && grade.passed === false ? "negative" : "") }, node("strong", {}, verdict(grade)), node("span", {}, metricNames[grade.unit] || grade.metric || "保存された採点結果")),
      grade.comparison_eligible === false ? node("p", { class: "review-contract-note" }, "評価条件を要確認 · この得点は比較の集計から除外しています。") : null,
      grade.evaluation_review?.kind === "sales_target" ? node("p", { class: "review-contract-note" }, "商品・行動条件の検査：" + ({consistent:"整合（自動判定）",violation:"違反候補（自動判定）",unknown:"判定保留"}[grade.evaluation_review.verdict]) + "。引用と会話を確認してください。取引前進の主得点とは別に評価しています。") : null,
      ...grade.sections.map(gradeSection), node("p", { class: "review-caption grade-fingerprint" }, "採点版 " + grade.fingerprint.slice(0, 12))));
    if (!review.grading.length) grading.append(node("p", { class: "review-empty" }, "採点はまだ完了していません。"));
    const advancedBody = node("div", { class: "review-advanced-body" });
    const select = node("select", { "aria-label": "詳細情報の種類" }, ...[["reasoning", "思考"], ["requests", "要求・計測・操作"], ["trial", "課題全体の計測"], ["grade", "採点原本"], ["answer", "回答の保存原本"]].map(([value, label]) => node("option", { value }, label)));
    const roles = { target: "評価対象", customer: "相手役", judge: "採点役", intent_judge: "購入意向の採点役", language_judge: "言語の採点役", converter: "変換役" };
    const callSelect = node("select", { "aria-label": "表示する要求" }, ...(review.calls || []).map(call => node("option", { value: call.index }, "要求 " + (call.index + 1) + " · " + (roles[call.role] || call.role))));
    const output = node("div", { class: "review-raw-output" });
    const cache = new Map(); let activated = false, loading = 0;
    async function showRaw() {
      const sequence = ++loading; const kind = select.value; const call = callSelect.value || "0";
      callSelect.hidden = !["reasoning", "requests"].includes(kind);
      if (!callSelect.hidden && !(review.calls || []).length) { output.replaceChildren(node("p", {}, "要求の記録はありません。")); return; }
      if (kind === "trial") { output.replaceChildren(node("pre", {}, JSON.stringify(review.trial, null, 2))); return; }
      output.replaceChildren(node("p", { role: "status" }, "保存済みの詳細を読み込んでいます…"));
      try {
        const key = kind + ":" + call;
        if (!cache.has(key)) cache.set(key, loadRaw(kind, call));
        const raw = await cache.get(key);
        if (sequence !== loading) return;
        output.replaceChildren();
        if (kind === "answer") output.append(node("pre", {}, raw.answer || "回答本文は空です。"));
        else if (kind === "grade") for (const grade of raw.grades) output.append(node("pre", {}, JSON.stringify({ result: grade.result, decisions: grade.decisions }, null, 2)));
        else if (kind === "reasoning") output.append(node("pre", {}, raw.reasoning || "この要求では思考本文が返されていません。"));
        else output.append(node("pre", {}, JSON.stringify(raw, null, 2)));
        if (!output.childNodes.length) output.append(node("p", {}, "該当する記録はありません。"));
      } catch (error) { cache.delete(kind + ":" + call); if (sequence === loading) output.replaceChildren(node("p", { role: "alert" }, error.message), node("button", { class: "button small", onclick: showRaw }, "再試行")); }
    }
    select.addEventListener("change", showRaw); callSelect.addEventListener("change", showRaw);
    advancedBody.append(node("div", { class: "review-trace-controls" }, select, callSelect), node("p", { class: "review-caption" }, "長い記録は要求ごとに読み込みます。"), output);
    const advanced = disclosure("詳細情報 · 思考・要求・計測・原本", advancedBody, "review-advanced"); advanced.id = "review-advanced";
    advanced.addEventListener("toggle", () => { if (advanced.open && !activated) { activated = true; showRaw(); } });
    const nav = node("nav", { class: "review-nav", "aria-label": "ケース明細の目次" }, ...[["review-question", "問題"], ["review-answer", "回答"], ["review-grading", "採点根拠"], ["review-advanced", "詳細情報"]].map(([id, label]) => node("button", { onclick: () => { if (id === "review-advanced") advanced.open = true; document.getElementById(id).scrollIntoView({ block: "start", behavior: "smooth" }); } }, label)), node("span", { class: "review-nav-score" }, verdict(firstGrade)));
    return node("div", { class: "case-review" }, nav, node("div", { class: "review-layout" }, node("div", { class: "review-main" }, problem, response), grading), advanced, node("p", { class: "review-source-note" }, review.source_note));
  }
  const api = { markdown, plain, verdict, render };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BeaconReview = api;
})(typeof window === "undefined" ? globalThis : window);
