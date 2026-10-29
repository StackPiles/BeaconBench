"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
// A text-only DOM fixture catches HTML insertion and keeps the tests dependency free.
class Element {
  constructor(tag, text = "") { this.tagName = tag; this.text = text; this.children = []; this.attrs = {}; this.listeners = {}; }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener(key, fn) { this.listeners[key] = fn; }
  replaceChildren(...children) { this.children = children; }
  get childNodes() { return this.children; }
  set innerHTML(_) { throw Error("Untrusted HTML must never be inserted"); }
}
global.document = { createElement: tag => new Element(tag), createTextNode: text => new Element("#text", text), createDocumentFragment: () => new Element("#fragment") };
const review = require("./review.js");
const all = node => [node, ...node.children.flatMap(all)];
const text = node => all(node).map(item => item.text).join("");

test("Markdown creates readable paragraphs, tables and full code without executing HTML", () => {
  const code = 'print("<script>unsafe()</script>")\n' + "line\n".repeat(10000);
  const dom = review.markdown("# 問題\n\n**重要** `code`\n\n| 項目 | 値 |\n| --- | --- |\n| a | 1 |\n\n```python\n" + code + "```\n\n<img src=x onerror=unsafe()>\n[jump](javascript:unsafe())");
  const tags = all(dom).map(item => item.tagName);
  assert.ok(tags.includes("table") && tags.includes("strong") && tags.includes("pre"));
  assert.ok(!tags.includes("script") && !tags.includes("img") && !tags.includes("a"));
  assert.ok(text(dom).includes(code.trimEnd()));
  assert.ok(text(dom).includes("<img src=x onerror=unsafe()>"));
});
test("Primary view contains question, answer and explanation, with no trace loaded", () => {
  let loads = 0;
  const dom = review.render({ review: { question: { text: "問題の全文" }, response: { kind: "answer", messages: [{ role: "assistant", content: "回答の全文" }], artifacts: [] },
    grading: [{ status: "scored", passed: true, unit: "binary", fingerprint: "abcdef", sections: [{ kind: "text", title: "理由", text: "採点理由の全文" }] }], source_note: "原本保全" } }, () => { loads++; });
  assert.equal(loads, 0);
  assert.ok(text(dom).includes("問題の全文") && text(dom).includes("回答の全文") && text(dom).includes("採点理由の全文"));
  assert.equal(all(dom).filter(item => item.tagName === "pre").length, 0);
  const advanced = all(dom).find(item => item.className?.includes("review-advanced"));
  assert.ok(advanced && !advanced.attrs.open);
});
test("Verdicts preserve unknowns and continuous scores without invented pass thresholds", () => {
  assert.equal(review.verdict({ status: "scored", unit: "binary" }), "判定不明");
  assert.equal(review.verdict({ status: "scored", score: 0, unit: "native_reward_0_1" }), "0 / 1");
  assert.equal(review.verdict({ status: "scored", score: 7, unit: "salesllm_score_0_10" }), "7 / 10");
  assert.equal(review.verdict({ status: "invalid_instrument", score: 0 }), "採点未完了");
});
test("Unclosed code fences keep the tail of saved output", () => {
  const dom = review.markdown("```cpp\nline1\nlast-line");
  assert.ok(text(dom).includes("last-line"));
});
