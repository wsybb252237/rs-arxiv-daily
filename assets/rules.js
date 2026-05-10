let originalConfig = null;

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(message, type = "neutral") {
  $("#ruleStatus").className = `rule-status ${type}`;
  $("#ruleStatus").textContent = message;
}

function prettyJson(value) {
  return JSON.stringify(value, null, 2);
}

function parseEditor() {
  return JSON.parse($("#configEditor").value);
}

function countWeakRules(config) {
  return config.matching?.weak_anchor_rules?.length || 0;
}

function renderSummary(config) {
  const matching = config.matching || {};
  const cards = [
    ["arXiv 分类", config.categories?.length || 0],
    ["强锚点", matching.strong_anchor_terms?.length || 0],
    ["弱锚点规则", countWeakRules(config)],
    ["强相关词", config.strong_terms?.length || 0],
    ["普通关键词", config.keywords?.length || 0],
    ["排除词", config.exclude_terms?.length || 0],
    ["标签", config.labels?.length || 0],
    ["最低分", matching.minimum_score ?? config.minimum_score ?? "--"],
  ];

  $("#rulesTitle").textContent = config.site?.title || "config.json";
  $("#ruleSummary").innerHTML = cards
    .map(
      ([label, value]) => `
        <article class="rule-card">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </article>
      `,
    )
    .join("");
}

function validateConfig() {
  try {
    const config = parseEditor();
    const missing = [];
    if (!Array.isArray(config.categories)) missing.push("categories");
    if (!config.matching || typeof config.matching !== "object") missing.push("matching");
    if (!Array.isArray(config.strong_terms)) missing.push("strong_terms");
    if (!Array.isArray(config.keywords)) missing.push("keywords");
    if (!Array.isArray(config.labels)) missing.push("labels");

    if (missing.length) {
      setStatus(`结构可解析，但缺少字段：${missing.join(", ")}`, "warn");
    } else {
      setStatus("JSON 有效，关键字段完整。", "ok");
    }
    renderSummary(config);
    return config;
  } catch (error) {
    setStatus(`JSON 无效：${error.message}`, "error");
    return null;
  }
}

function resetEditor() {
  $("#configEditor").value = prettyJson(originalConfig);
  renderSummary(originalConfig);
  setStatus("已恢复当前 config.json。", "neutral");
}

function downloadConfig() {
  const config = validateConfig();
  if (!config) return;

  const blob = new Blob([`${prettyJson(config)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "config.json";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function initRules() {
  try {
    const response = await fetch(`config.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    originalConfig = await response.json();
    resetEditor();
  } catch (error) {
    originalConfig = {};
    $("#configEditor").value = "{}";
    setStatus(`加载 config.json 失败：${error.message}`, "error");
  }

  $("#validateRules").addEventListener("click", validateConfig);
  $("#resetRules").addEventListener("click", resetEditor);
  $("#downloadRules").addEventListener("click", downloadConfig);
  $("#configEditor").addEventListener("input", () => {
    setStatus("配置已修改，尚未校验。", "warn");
  });

  if (window.lucide) {
    window.lucide.createIcons();
  }
}

initRules();
