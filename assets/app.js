const state = {
  archive: null,
  papers: [],
  query: "",
  selectedLabels: new Set(),
  date: "全部",
};

const fallbackArchive = {
  site: {
    title: "遥感大模型 arXiv 日报 · 归档",
    update_window: "08:00",
    timezone: "Asia/Shanghai",
  },
  generated_at: null,
  latest_date: null,
  total_papers: 0,
  labels: [],
  dates: [],
};

const labelColors = new Map([
  ["开源", "tag code"],
  ["目标检测", "tag"],
  ["语义分割", "tag category"],
  ["实例分割", "tag score"],
  ["变化检测", "tag"],
  ["场景分类", "tag category"],
  ["图像生成与重建", "tag score"],
  ["基础模型", "tag"],
  ["视觉-语言模型", "tag category"],
  ["少样本/域适应", "tag score"],
  ["多模态融合", "tag"],
  ["光学遥感", "tag robot"],
  ["SAR遥感", "tag robot"],
  ["高光谱/多光谱", "tag robot"],
  ["LiDAR/点云", "tag robot"],
  ["无人机/UAV", "tag robot"],
  ["数据集/评测", "tag"],
  ["其他", "tag"],
]);

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function compactDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function compactDateTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function hasCode(paper) {
  return Boolean(paper?.has_code || paper?.code_url);
}

function paperLabels(paper) {
  const labels = Array.isArray(paper.labels) ? [...paper.labels] : [];
  if (hasCode(paper) && !labels.includes("开源")) {
    labels.unshift("开源");
  }
  if (!labels.length) {
    labels.push(paper.topic || "其他");
  }
  return [...new Set(labels.filter(Boolean))];
}

function sortPapers(papers) {
  return [...papers].sort((a, b) => {
    const codeDiff = Number(hasCode(b)) - Number(hasCode(a));
    if (codeDiff) return codeDiff;
    const dateDiff = String(b.published || "").localeCompare(String(a.published || ""));
    if (dateDiff) return dateDiff;
    return Number(b.score || 0) - Number(a.score || 0);
  });
}

async function loadJson(path) {
  const response = await fetch(`${path}?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load ${path}`);
  }
  return response.json();
}

async function loadArchive() {
  try {
    const archive = await loadJson("data/index.json");
    const dailyFiles = await Promise.all(
      (archive.dates || []).map(async (item) => {
        try {
          return await loadJson(item.path);
        } catch (error) {
          console.warn(error);
          return { date: item.date, papers: [] };
        }
      }),
    );
    state.archive = archive;
    state.papers = dailyFiles
      .flatMap((daily) =>
        (daily.papers || []).map((paper) => ({
          ...paper,
          archive_date: daily.date,
        })),
      )
      .map((paper) => ({
        ...paper,
        labels: paperLabels(paper),
      }));
    state.papers = sortPapers(state.papers);
  } catch (error) {
    console.warn(error);
    state.archive = fallbackArchive;
    state.papers = [];
  }
}

function labelSortKey(label) {
  const order = state.archive?.label_order || [];
  const index = order.indexOf(label);
  return index === -1 ? Number.MAX_SAFE_INTEGER : index;
}

function getLabelCounts() {
  const counts = new Map();
  state.papers.forEach((paper) => {
    paperLabels(paper).forEach((label) => {
      counts.set(label, (counts.get(label) || 0) + 1);
    });
  });
  return [...counts.entries()].sort(
    (a, b) => labelSortKey(a[0]) - labelSortKey(b[0]) || b[1] - a[1] || a[0].localeCompare(b[0], "zh-CN"),
  );
}

function getDateCounts() {
  const archiveDates = state.archive?.dates || [];
  if (archiveDates.length) {
    return archiveDates
      .map((item) => [item.date, item.count || 0])
      .filter(([date]) => Boolean(date))
      .sort((a, b) => b[0].localeCompare(a[0]));
  }

  const counts = new Map();
  state.papers.forEach((paper) => {
    const date = paper.archive_date || (paper.published || "").slice(0, 10) || "未知";
    counts.set(date, (counts.get(date) || 0) + 1);
  });
  return [...counts.entries()].sort((a, b) => b[0].localeCompare(a[0]));
}

function matchesQuery(paper) {
  if (!state.query) return true;
  const haystack = [
    paper.title,
    paper.title_zh,
    paper.summary,
    paper.summary_zh,
    paper.arxiv_id,
    paper.primary_category,
    paper.code_url,
    ...paperLabels(paper),
    ...(paper.authors || []),
    ...(paper.categories || []),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(state.query.toLowerCase());
}

function filteredPapers() {
  const selectedLabels = [...state.selectedLabels];
  const papers = state.papers.filter((paper) => {
    const labels = paperLabels(paper);
    const labelsOk = selectedLabels.every((label) => labels.includes(label));
    const date = paper.archive_date || (paper.published || "").slice(0, 10);
    const dateOk = state.date === "全部" || date === state.date;
    return labelsOk && dateOk && matchesQuery(paper);
  });
  return sortPapers(papers);
}

function renderMetrics() {
  const archive = state.archive || fallbackArchive;
  $("#latestDate").textContent = archive.latest_date || "--";
  $("#paperCount").textContent = String(state.papers.length);
  $("#dateCount").textContent = String((archive.dates || []).length || getDateCounts().length);
  $("#generatedAt").textContent = compactDateTime(archive.generated_at);
}

function renderLabelFilters() {
  const counts = getLabelCounts();
  const total = state.papers.length;
  const options = [["全部", total], ...counts];
  $("#labelFilters").innerHTML = options
    .map(([label, count]) => {
      const active = label === "全部" ? (state.selectedLabels.size === 0 ? "active" : "") : state.selectedLabels.has(label) ? "active" : "";
      return `
        <button class="filter-button ${active}" type="button" data-label="${escapeHtml(label)}">
          <span class="filter-name">${escapeHtml(label)}</span>
          <span class="filter-count">${count}</span>
        </button>
      `;
    })
    .join("");
}

function renderDateFilters() {
  const counts = getDateCounts().filter(([, count]) => count > 0);
  const options = [["全部", state.papers.length], ...counts];
  $("#dateFilters").innerHTML = options
    .map(([date, count]) => {
      const active = state.date === date ? "active" : "";
      return `
        <button class="date-button ${active}" type="button" data-date="${escapeHtml(date)}">
          <span class="date-name">${escapeHtml(date)}</span>
          <span class="date-count">${count}</span>
        </button>
      `;
    })
    .join("");
}

function paperMeta(paper) {
  const authors = (paper.authors || []).slice(0, 6).join(", ");
  const overflow = (paper.authors || []).length > 6 ? " et al." : "";
  const category = paper.primary_category || (paper.categories || [])[0] || "";
  return [
    authors ? `${authors}${overflow}` : "",
    category,
    paper.arxiv_id ? `arXiv:${paper.arxiv_id}` : "",
  ]
    .filter(Boolean)
    .map(escapeHtml)
    .join(" · ");
}

function renderPaper(paper) {
  const labels = paperLabels(paper);
  const tags = [...labels, ...(paper.categories || []).slice(0, 4)]
    .map((tag, index) => {
      const className = labelColors.get(tag) || (index < labels.length ? "tag" : "tag category");
      return `<span class="${className}">${escapeHtml(tag)}</span>`;
    })
    .join("");
  const scoreTag =
    typeof paper.score === "number" ? `<span class="tag score">匹配度 ${paper.score}</span>` : "";
  const pdfUrl = paper.pdf_url || (paper.arxiv_id ? `https://arxiv.org/pdf/${paper.arxiv_id}` : "");
  const ar5ivUrl = paper.arxiv_id ? `https://ar5iv.labs.arxiv.org/html/${paper.arxiv_id}` : "";

  const titleZh = paper.title_zh && paper.title_zh !== paper.title ? paper.title_zh : "";
  const summaryZh = paper.summary_zh && paper.summary_zh !== paper.summary ? paper.summary_zh : "";

  return `
    <article class="paper-card">
      <div class="paper-head">
        <a class="paper-title" href="${escapeHtml(paper.abs_url || "#")}" target="_blank" rel="noreferrer">
          ${escapeHtml(paper.title)}
        </a>
        <span class="paper-date">${escapeHtml(compactDate(paper.published))}</span>
      </div>
      ${titleZh ? `<div class="paper-title-zh">${escapeHtml(titleZh)}</div>` : ""}
      <div class="paper-meta">${paperMeta(paper)}</div>
      <p class="paper-summary">${escapeHtml(paper.summary)}</p>
      ${summaryZh ? `<p class="paper-summary-zh">${escapeHtml(summaryZh)}</p>` : ""}
      <div class="paper-tags">${tags}${scoreTag}</div>
      <div class="paper-links">
        <a class="paper-link" href="${escapeHtml(paper.abs_url || "#")}" target="_blank" rel="noreferrer">
          <i data-lucide="file-text" aria-hidden="true"></i>
          摘要
        </a>
        ${
          pdfUrl
            ? `<a class="paper-link" href="${escapeHtml(pdfUrl)}" target="_blank" rel="noreferrer">
                <i data-lucide="file-down" aria-hidden="true"></i>
                PDF
              </a>`
            : ""
        }
        ${
          ar5ivUrl
            ? `<a class="paper-link" href="${escapeHtml(ar5ivUrl)}" target="_blank" rel="noreferrer">
                <i data-lucide="file-code" aria-hidden="true"></i>
                HTML
              </a>`
            : ""
        }
        ${
          paper.code_url
            ? `<a class="paper-link" href="${escapeHtml(paper.code_url)}" target="_blank" rel="noreferrer">
                <i data-lucide="github" aria-hidden="true"></i>
                Code
              </a>`
            : ""
        }
      </div>
    </article>
  `;
}

function renderResults() {
  const papers = filteredPapers();
  const titleParts = [];
  if (state.selectedLabels.size) titleParts.push([...state.selectedLabels].join(" + "));
  if (state.date !== "全部") titleParts.push(state.date);
  $("#resultTitle").textContent = titleParts.length ? titleParts.join(" · ") : "全部归档";

  const summary = [
    `${papers.length} 篇论文`,
    state.query ? `检索：${state.query}` : "",
    state.selectedLabels.size ? `标签：${[...state.selectedLabels].join(" + ")}` : "",
    state.date !== "全部" ? `日期：${state.date}` : "",
  ].filter(Boolean);
  $("#activeSummary").innerHTML = summary.map((item) => `<span class="summary-chip">${escapeHtml(item)}</span>`).join("");

  if (!state.papers.length) {
    $("#paperList").innerHTML = `
      <div class="empty-state">
        <div>
          <strong>暂无归档数据</strong>
          <span>运行抓取脚本后，这里会显示每日遥感大模型论文归档。</span>
        </div>
      </div>
    `;
  } else if (!papers.length) {
    $("#paperList").innerHTML = `
      <div class="empty-state">
        <div>
          <strong>没有匹配结果</strong>
          <span>调整检索词、方向或日期筛选。</span>
        </div>
      </div>
    `;
  } else {
    $("#paperList").innerHTML = papers.map(renderPaper).join("");
  }

  if (window.lucide) {
    window.lucide.createIcons();
  }
}

function bindEvents() {
  $("#searchInput").addEventListener("input", (event) => {
    state.query = event.target.value.trim();
    renderResults();
  });

  $("#labelFilters").addEventListener("click", (event) => {
    const button = event.target.closest("[data-label]");
    if (!button) return;
    const label = button.dataset.label;
    if (label === "全部") {
      state.selectedLabels.clear();
    } else if (state.selectedLabels.has(label)) {
      state.selectedLabels.delete(label);
    } else {
      state.selectedLabels.add(label);
    }
    renderLabelFilters();
    renderResults();
  });

  $("#dateFilters").addEventListener("click", (event) => {
    const button = event.target.closest("[data-date]");
    if (!button) return;
    state.date = button.dataset.date;
    renderDateFilters();
    renderResults();
  });

  $("#clearFilters").addEventListener("click", () => {
    state.query = "";
    state.selectedLabels.clear();
    state.date = "全部";
    $("#searchInput").value = "";
    renderLabelFilters();
    renderDateFilters();
    renderResults();
  });
}

async function init() {
  await loadArchive();
  renderMetrics();
  renderLabelFilters();
  renderDateFilters();
  renderResults();
  bindEvents();

  if (window.lucide) {
    window.lucide.createIcons();
  }
}

init();
