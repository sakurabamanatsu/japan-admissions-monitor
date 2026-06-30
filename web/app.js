const state = {
  schools: [],
  timer: null,
  staticMode: new URLSearchParams(location.search).has("static")
    || !["127.0.0.1", "localhost"].includes(location.hostname),
  staticData: null,
};
const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "操作失败");
  return data;
}

async function loadStaticData() {
  if (!state.staticData) {
    const response = await fetch(`data.json?t=${Date.now()}`);
    if (!response.ok) throw new Error("暂时无法读取监控数据");
    state.staticData = await response.json();
  }
  return state.staticData;
}

function escapeHtml(value = "") {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

function formatDate(value) {
  if (!value) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("visible");
  clearTimeout(state.timer);
  state.timer = setTimeout(() => node.classList.remove("visible"), 2400);
}

async function loadStatus() {
  if (state.staticMode) {
    const data = await loadStaticData();
    const counts = data.counts;
    $("#newCount").textContent = counts.new_count;
    $("#schoolCount").textContent = counts.school_count;
    $("#pdfCount").textContent = counts.pdf_count;
    $("#totalCount").textContent = counts.total;
    $("#scanButton").disabled = true;
    $("#scanButton").innerHTML = `<span aria-hidden="true">✓</span> 云端监控中`;
    $("#monitorState").textContent = data.generated_at
      ? `云端更新：${formatDate(data.generated_at)}`
      : "等待首次云端检查";
    $("#scheduleText").textContent = data.schedule_text
      || `每 ${data.interval_minutes} 分钟检查`;
    return;
  }
  const data = await api("/api/status");
  const counts = data.counts;
  $("#newCount").textContent = counts.new_count;
  $("#schoolCount").textContent = counts.school_count;
  $("#pdfCount").textContent = counts.pdf_count;
  $("#totalCount").textContent = counts.total;
  $("#scanButton").disabled = data.scanning;
  $("#scanButton").innerHTML = data.scanning
    ? `<span aria-hidden="true">↻</span> 正在检查`
    : `<span aria-hidden="true">↻</span> 立即检查`;
  $("#monitorState").textContent = data.scanning
    ? `正在检查：${data.current_school || "准备中"}`
    : data.last_finished_at
      ? `上次完成：${formatDate(data.last_finished_at)}`
      : "监控服务已运行";
  $("#scheduleText").textContent = `每 ${data.interval_minutes} 分钟检查`;
  if (data.scanning) setTimeout(loadStatus, 3000);
}

function queryString() {
  const params = new URLSearchParams();
  const values = {
    q: $("#searchInput").value.trim(),
    ownership: $("#ownershipFilter").value,
    region: $("#regionFilter").value,
    category: $("#categoryFilter").value,
  };
  Object.entries(values).forEach(([key, value]) => value && params.set(key, value));
  if ($("#pdfFilter").checked) params.set("pdf", "1");
  if ($("#newFilter").checked) params.set("new", "1");
  return params.toString();
}

async function loadItems() {
  let items;
  if (state.staticMode) {
    const data = await loadStaticData();
    const keyword = $("#searchInput").value.trim().toLowerCase();
    items = data.items.filter((item) => {
      const text = `${item.title} ${item.school} ${item.matched}`.toLowerCase();
      return (!keyword || text.includes(keyword))
        && (!$("#ownershipFilter").value || item.ownership === $("#ownershipFilter").value)
        && (!$("#regionFilter").value || item.region === $("#regionFilter").value)
        && (!$("#categoryFilter").value || item.category === $("#categoryFilter").value)
        && (!$("#pdfFilter").checked || item.is_pdf)
        && (!$("#newFilter").checked || item.is_new);
    });
  } else {
    const data = await api(`/api/items?${queryString()}`);
    items = data.items;
  }
  renderItems(items);
}

function renderItems(items) {
  $("#resultCount").textContent = `${items.length} 条结果`;
  $("#emptyState").hidden = items.length !== 0;
  $("#resultList").hidden = items.length === 0;
  $("#resultList").innerHTML = items.map((item) => `
    <article class="result-item">
      <div class="file-icon ${item.is_pdf ? "pdf" : ""}">${item.is_pdf ? "PDF" : "WEB"}</div>
      <div>
        <a class="item-title" href="${escapeHtml(item.url)}" target="_blank" rel="noopener"
           data-id="${item.id}">${escapeHtml(item.title)}</a>
        <div class="tags">
          ${item.is_new ? '<span class="tag new">新发现</span>' : ""}
          ${item.is_pdf ? `<span class="tag">${item.pdf_year_status === "target" ? "PDF正文确认" : "标题确认"}</span>` : ""}
          <span class="tag">${escapeHtml(item.ownership || "未分类")}</span>
          <span class="tag">${escapeHtml(item.category)}</span>
          <span class="tag">${escapeHtml(item.region)}</span>
        </div>
        <div class="item-meta">${escapeHtml(item.matched || "历史监控记录")}</div>
      </div>
      <div class="item-side">
        <strong>${escapeHtml(item.school)}</strong>
        <span>${formatDate(item.first_seen_at)}</span>
      </div>
    </article>
  `).join("");
  if (!state.staticMode) document.querySelectorAll(".item-title[data-id]").forEach((link) => {
    link.addEventListener("click", () => {
      api("/api/items/read", {
        method: "POST", body: JSON.stringify({ id: Number(link.dataset.id) }),
      }).catch(() => {});
    });
  });
}

async function loadSchools() {
  const data = state.staticMode
    ? await loadStaticData()
    : await api("/api/schools");
  state.schools = data.schools;
  $("#schoolList").innerHTML = data.schools.map((school) => `
    <div class="school-row ${school.active ? "" : "inactive"}">
      <div><strong>${escapeHtml(school.name)}</strong>
        <small>${escapeHtml(school.ownership || "未分类")} · ${escapeHtml(school.region)} · ${school.active ? "监控中" : "已停用"}</small></div>
      <div class="school-row-actions" ${state.staticMode ? "hidden" : ""}>
        <button data-action="toggle" data-name="${escapeHtml(school.name)}">${school.active ? "停用" : "启用"}</button>
        <button data-action="edit" data-name="${escapeHtml(school.name)}">编辑</button>
        <button data-action="delete" data-name="${escapeHtml(school.name)}">删除</button>
      </div>
    </div>
  `).join("");
  if (state.staticMode) {
    $("#schoolForm").hidden = true;
    if (!$("#cloudSchoolNote")) {
      $("#schoolList").insertAdjacentHTML("beforebegin", `
        <div class="cloud-note" id="cloudSchoolNote">
          云端版请在 GitHub 仓库中编辑 <strong>schools.csv</strong> 管理学校。
          <a href="https://github.com/sakurabamanatsu/japan-admissions-monitor/edit/main/schools.csv"
             target="_blank" rel="noopener">打开学校列表</a>
        </div>
      `);
    }
  }
}

function resetForm() {
  $("#schoolForm").reset();
  $("#oldSchoolName").value = "";
  $("#schoolActive").checked = true;
}

async function saveSchool(school) {
  if (state.staticMode) return;
  await api("/api/schools", { method: "POST", body: JSON.stringify(school) });
  await loadSchools();
  resetForm();
  toast("学校设置已保存");
}

$("#scanButton").addEventListener("click", async () => {
  if (state.staticMode) return;
  const data = await api("/api/scan", { method: "POST", body: "{}" });
  toast(data.message);
  loadStatus();
});
$("#schoolsButton").addEventListener("click", () => $("#schoolDialog").showModal());
$("#closeDialog").addEventListener("click", () => $("#schoolDialog").close());
$("#cancelEdit").addEventListener("click", resetForm);
$("#schoolForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveSchool({
    old_name: $("#oldSchoolName").value,
    name: $("#schoolName").value,
    ownership: $("#schoolOwnership").value,
    url: $("#schoolUrl").value,
    active: $("#schoolActive").checked,
  });
});
$("#schoolList").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const school = state.schools.find((entry) => entry.name === button.dataset.name);
  if (!school) return;
  if (button.dataset.action === "edit") {
    $("#oldSchoolName").value = school.name;
    $("#schoolName").value = school.name;
    $("#schoolOwnership").value = school.ownership || "私立";
    $("#schoolUrl").value = school.url;
    $("#schoolActive").checked = school.active;
  } else if (button.dataset.action === "toggle") {
    await saveSchool({ old_name: school.name, ...school, active: !school.active });
  } else if (button.dataset.action === "delete" && confirm(`确定删除“${school.name}”吗？`)) {
    await api(`/api/schools?name=${encodeURIComponent(school.name)}`, { method: "DELETE" });
    await loadSchools();
    toast("学校已删除");
  }
});

let searchDelay;
$("#searchInput").addEventListener("input", () => {
  clearTimeout(searchDelay);
  searchDelay = setTimeout(loadItems, 250);
});
["ownershipFilter", "regionFilter", "categoryFilter", "pdfFilter", "newFilter"].forEach((id) => {
  $(`#${id}`).addEventListener("change", loadItems);
});
$("#resetButton").addEventListener("click", () => {
  $("#searchInput").value = "";
  $("#ownershipFilter").value = "";
  $("#regionFilter").value = "";
  $("#categoryFilter").value = "";
  $("#pdfFilter").checked = false;
  $("#newFilter").checked = false;
  loadItems();
});

Promise.all([loadStatus(), loadSchools(), loadItems()]).catch((error) => toast(error.message));
setInterval(() => {
  if (state.staticMode) state.staticData = null;
  loadStatus().catch(() => {});
  loadItems().catch(() => {});
}, 30000);
