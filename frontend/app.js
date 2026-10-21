"use strict";

const POLL_MS = 1500;
const STORAGE_KEY = "l2a.server";
const CLIENT_KEY = "l2a.client";
const STATUS_LABEL = {
  queued: "Đang chờ",
  downloading: "Đang tải",
  converting: "Đang chuyển đổi",
  merging: "Đang gộp",
  done: "Hoàn tất",
  error: "Lỗi",
};
const BUSY = new Set(["queued", "converting", "merging"]);
const LINK_PATTERN = /(youtube\.com|youtu\.be|youtube-nocookie\.com|facebook\.com|fb\.watch|fb\.com)/i;

const $ = (id) => document.getElementById(id);
const state = { jobs: new Map(), merges: new Map(), mergeOrder: [], online: null };

/* ---------- Cấu hình máy chủ ---------- */

function loadServer() {
  const configBase = (window.L2A_CONFIG && window.L2A_CONFIG.apiBase) || "";
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch {
    saved = null;
  }
  // Địa chỉ trong config.js (do start-public cập nhật khi tunnel đổi URL) luôn được ưu tiên,
  // để người dùng cũ không bị kẹt ở địa chỉ tunnel đã chết.
  return {
    apiBase: configBase || (saved && saved.apiBase) || "",
    apiKey: (saved && saved.apiKey) || "",
  };
}

function saveServer(server) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(server));
  } catch {
    /* trình duyệt chặn storage: vẫn dùng được trong phiên này */
  }
}

/** Đọc cấu hình từ link mời dạng ...#server=<url>&key=<key>, lưu lại rồi xoá khỏi thanh địa chỉ. */
function applyInviteLink() {
  const params = new URLSearchParams(location.hash.slice(1));
  const apiBase = params.get("server");
  const apiKey = params.get("key");
  if (apiBase === null && apiKey === null) return;
  server = {
    apiBase: apiBase !== null && /^https?:\/\/\S+$/i.test(apiBase) ? apiBase : server.apiBase,
    apiKey: apiKey !== null ? apiKey : server.apiKey,
  };
  saveServer(server);
  history.replaceState(null, "", location.pathname + location.search);
}

function inviteLink() {
  const params = new URLSearchParams();
  const defaultBase = (window.L2A_CONFIG && window.L2A_CONFIG.apiBase) || "";
  if (server.apiBase && server.apiBase !== defaultBase) params.set("server", server.apiBase);
  if (server.apiKey) params.set("key", server.apiKey);
  const hash = params.toString();
  return `${location.origin}${location.pathname}${hash ? `#${hash}` : ""}`;
}

async function copyInvite() {
  const link = inviteLink();
  try {
    await navigator.clipboard.writeText(link);
    showMessage($("server-msg"), "Đã sao chép link mời — gửi cho người khác là dùng được ngay.", "success");
  } catch {
    showMessage($("server-msg"), link, "success");
  }
}

function loadClientId() {
  const fresh = () =>
    (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9]/g, "");
  try {
    let id = localStorage.getItem(CLIENT_KEY);
    if (!id || !/^[A-Za-z0-9_-]{16,64}$/.test(id)) {
      id = fresh();
      localStorage.setItem(CLIENT_KEY, id);
    }
    return id;
  } catch {
    return fresh(); // không có storage: danh sách chỉ giữ trong phiên này
  }
}

let server = loadServer();
applyInviteLink();
const clientId = loadClientId();

function apiUrl(path, forMedia = false) {
  const base = server.apiBase.replace(/\/+$/, "");
  if (!forMedia) return `${base}${path}`;
  const params = new URLSearchParams({ client: clientId });
  if (server.apiKey) params.set("key", server.apiKey);
  return `${base}${path}?${params}`;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", "X-Client-Id": clientId };
  if (server.apiKey) headers["X-API-Key"] = server.apiKey;
  const res = await fetch(apiUrl(path), { ...options, headers });
  let body;
  try {
    body = await res.json();
  } catch {
    throw new Error(`Máy chủ trả về dữ liệu không hợp lệ (${res.status})`);
  }
  if (!body.success) throw new Error(body.error || `Lỗi máy chủ (${res.status})`);
  return body.data;
}

function setOnline(online, detail) {
  state.online = online;
  $("server-dot").className = `dot ${online ? "online" : "offline"}`;
  const host = server.apiBase ? server.apiBase.replace(/^https?:\/\//, "") : "cùng trang";
  $("server-label").textContent = online ? `Đã kết nối · ${host}` : "Chưa kết nối máy chủ";
  if (!online && detail) {
    $("server-panel").open = true;
    showMessage($("server-msg"), detail, "error");
  }
}

async function connectServer(event) {
  event.preventDefault();
  const apiBase = $("api-base").value.trim();
  if (apiBase && !/^https?:\/\/[^\s]+$/i.test(apiBase)) {
    showMessage($("server-msg"), "Địa chỉ phải bắt đầu bằng http:// hoặc https://", "error");
    return;
  }
  server = { apiBase, apiKey: $("api-key").value };
  saveServer(server);
  showMessage($("server-msg"), "Đang kết nối...");
  try {
    showRetention(await api("/api/health"));
    setOnline(true);
    showMessage($("server-msg"), "Kết nối thành công.", "success");
    await refresh();
  } catch (err) {
    setOnline(false, `Không kết nối được: ${err.message}`);
  }
}

/* ---------- Tiện ích ---------- */

function showMessage(node, text, kind) {
  node.textContent = text;
  node.className = `msg ${kind || ""}`;
}

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children) node.append(child);
  return node;
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

function downloadButton(href, primary) {
  return el("a", { className: "button", href }, [
    el("button", { className: primary ? "primary" : "", textContent: "Tải về", tabIndex: -1 }),
  ]);
}

/* ---------- Bước 1: gửi link ---------- */

function countLinks() {
  const tokens = $("links").value.split(/[\s,;]+/).filter((t) => LINK_PATTERN.test(t));
  $("link-count").textContent = `${new Set(tokens).size} link`;
}

async function submitLinks() {
  const msg = $("submit-msg");
  const button = $("submit");
  button.disabled = true;
  showMessage(msg, "Đang gửi...");
  try {
    const data = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        links: $("links").value,
        audio_format: $("format").value,
        bitrate: Number($("bitrate").value),
      }),
    });
    const skipped = data.invalid.length ? ` Bỏ qua ${data.invalid.length} link không hợp lệ: ${data.invalid.join(", ")}` : "";
    showMessage(msg, `Đã thêm ${data.jobs.length} link vào hàng xử lý.${skipped}`, data.invalid.length ? "error" : "success");
    $("links").value = "";
    countLinks();
    await safeRefresh();
  } catch (err) {
    showMessage(msg, err.message, "error");
  } finally {
    button.disabled = false;
  }
}

/* ---------- Bước 2: hàng xử lý ---------- */

function buildJobRow(job) {
  const checkbox = el("input", { type: "checkbox", title: "Chọn để gộp" });
  checkbox.addEventListener("change", () => {
    toggleMerge(job.id, checkbox.checked);
    renderJobs([...state.jobs.values()]);
  });
  const row = el("li", { className: "item" }, [
    checkbox,
    el("div", { className: "info" }, [
      el("div", { className: "title" }),
      el("div", { className: "meta" }),
      el("div", { className: "bar" }, [el("span")]),
      el("div", { className: "error-text" }),
    ]),
    el("div", { className: "actions" }),
  ]);
  row.dataset.id = job.id;
  return row;
}

function jobActions(job) {
  const buttons = [];
  if (job.has_file) buttons.push(downloadButton(apiUrl(`/api/jobs/${job.id}/file`, true), false));
  if (job.status === "error") {
    const retry = el("button", { textContent: "Thử lại" });
    retry.addEventListener("click", () => command(`/api/jobs/${job.id}/retry`, "POST"));
    buttons.push(retry);
  }
  const remove = el("button", { className: "icon", textContent: "✕", title: "Xoá", ariaLabel: "Xoá" });
  remove.addEventListener("click", () => deleteJob(job.id));
  buttons.push(remove);
  return buttons;
}

function updateProgress(info, item) {
  const bar = info.querySelector(".bar");
  bar.hidden = item.status === "done" || item.status === "error";
  bar.classList.toggle("indeterminate", BUSY.has(item.status));
  const percent = item.progress ?? 0;
  bar.firstElementChild.style.transform = BUSY.has(item.status) ? "" : `scaleX(${percent / 100})`;
}

function updateJobRow(row, job) {
  const info = row.querySelector(".info");
  info.querySelector(".title").replaceChildren(
    el("span", { className: `badge ${job.source}`, textContent: job.source === "youtube" ? "YT" : "FB" }),
    job.title || job.url,
  );
  const duration = formatDuration(job.duration);
  const percent = job.status === "downloading" ? ` ${job.progress}%` : "";
  info.querySelector(".meta").replaceChildren(
    el("span", { className: `status ${job.status}`, textContent: `${STATUS_LABEL[job.status]}${percent}` }),
    ` · ${job.audio_format.toUpperCase()}${duration ? ` · ${duration}` : ""} · ${job.url}`,
  );
  updateProgress(info, job);
  info.querySelector(".error-text").textContent = job.error || "";

  const selected = state.mergeOrder.includes(job.id);
  const checkbox = row.querySelector("input[type=checkbox]");
  checkbox.disabled = !job.has_file;
  checkbox.checked = selected;
  row.classList.toggle("is-selected", selected);

  const signature = `${job.status}|${job.has_file}|${server.apiBase}|${server.apiKey}`;
  if (row.dataset.signature === signature) return;
  row.dataset.signature = signature;
  row.querySelector(".actions").replaceChildren(...jobActions(job));
  row.querySelector("audio")?.remove();
  if (job.has_file) {
    row.append(el("audio", { controls: true, preload: "none", src: apiUrl(`/api/jobs/${job.id}/file`, true) }));
  }
}

function syncList(list, items, build, update, newestFirst) {
  const rows = new Map([...list.children].map((r) => [r.dataset.id, r]));
  const ordered = newestFirst ? [...items].reverse() : items;
  ordered.forEach((item, index) => {
    const row = rows.get(item.id) || build(item);
    if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
    update(row, item);
    rows.delete(item.id);
  });
  rows.forEach((row) => row.remove());
}

function renderJobs(jobs) {
  syncList($("jobs"), jobs, buildJobRow, updateJobRow, false);
  $("jobs-empty").hidden = jobs.length > 0;
}

async function safeRefresh() {
  try {
    await refresh();
  } catch {
    /* vòng polling kế tiếp sẽ báo lỗi kết nối */
  }
}

async function command(path, method) {
  try {
    await api(path, { method });
  } catch (err) {
    showMessage($("submit-msg"), err.message, "error");
    return;
  }
  await safeRefresh();
}

async function deleteJob(id) {
  toggleMerge(id, false);
  await command(`/api/jobs/${id}`, "DELETE");
}

function selectAllDone() {
  const ready = [...state.jobs.values()].filter((j) => j.has_file && !state.mergeOrder.includes(j.id));
  state.mergeOrder = [...state.mergeOrder, ...ready.map((j) => j.id)];
  renderAll();
}

async function clearErrors() {
  const failed = [...state.jobs.values()].filter((j) => j.status === "error");
  const results = await Promise.allSettled(failed.map((j) => api(`/api/jobs/${j.id}`, { method: "DELETE" })));
  const errors = results.filter((r) => r.status === "rejected");
  if (errors.length) showMessage($("submit-msg"), `Không xoá được ${errors.length} mục`, "error");
  await safeRefresh();
}

/* ---------- Bước 3: bàn gộp ---------- */

function toggleMerge(id, selected) {
  const without = state.mergeOrder.filter((x) => x !== id);
  state.mergeOrder = selected ? [...without, id] : without;
  renderMergeOrder();
}

function moveMerge(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= state.mergeOrder.length) return;
  const next = [...state.mergeOrder];
  [next[index], next[target]] = [next[target], next[index]];
  state.mergeOrder = next;
  renderMergeOrder();
}

function mergeOrderItem(job, index, total) {
  const up = el("button", { className: "icon", textContent: "↑", title: "Lên", ariaLabel: "Lên", disabled: index === 0 });
  const down = el("button", { className: "icon", textContent: "↓", title: "Xuống", ariaLabel: "Xuống", disabled: index === total - 1 });
  const remove = el("button", { className: "icon", textContent: "✕", title: "Bỏ khỏi bàn gộp", ariaLabel: "Bỏ khỏi bàn gộp" });
  up.addEventListener("click", () => moveMerge(index, -1));
  down.addEventListener("click", () => moveMerge(index, 1));
  remove.addEventListener("click", () => {
    toggleMerge(job.id, false);
    renderJobs([...state.jobs.values()]);
  });
  return el("li", {}, [
    el("span", { className: "num", textContent: String(index + 1).padStart(2, "0") }),
    el("span", { className: "name", textContent: job.title || job.url }),
    el("span", { className: "len", textContent: formatDuration(job.duration) }),
    up, down, remove,
  ]);
}

function renderMergeOrder() {
  state.mergeOrder = state.mergeOrder.filter((id) => state.jobs.get(id)?.has_file);
  const jobs = state.mergeOrder.map((id) => state.jobs.get(id));
  // Chỉ dựng lại khi danh sách đổi, để polling không làm mất click/focus trên nút ↑ ↓ ✕.
  const signature = jobs.map((j) => `${j.id}:${j.title}:${j.duration}`).join("|");
  const list = $("merge-order");
  if (list.dataset.signature !== signature) {
    list.dataset.signature = signature;
    list.replaceChildren(...jobs.map((job, i) => mergeOrderItem(job, i, jobs.length)));
  }

  const gap = Number($("merge-gap").value) || 0;
  const total = jobs.reduce((sum, j) => sum + (j.duration || 0), 0) + gap * Math.max(jobs.length - 1, 0);
  $("merge-total").textContent = jobs.length ? `${jobs.length} đoạn · ~${formatDuration(total)}` : "";
  $("merge-empty").hidden = jobs.length >= 2;
  $("merge").disabled = jobs.length < 2;
}

async function submitMerge() {
  const msg = $("merge-msg");
  $("merge").disabled = true;
  showMessage(msg, "Đang gửi yêu cầu gộp...");
  try {
    await api("/api/merges", {
      method: "POST",
      body: JSON.stringify({
        job_ids: state.mergeOrder,
        name: $("merge-name").value,
        gap_seconds: Number($("merge-gap").value) || 0,
        audio_format: $("merge-format").value,
        bitrate: Number($("merge-bitrate").value),
      }),
    });
    showMessage(msg, "Đã đưa vào hàng gộp.", "success");
    await safeRefresh();
  } catch (err) {
    showMessage(msg, err.message, "error");
  } finally {
    $("merge").disabled = state.mergeOrder.length < 2;
  }
}

function buildMergeRow(merge) {
  const row = el("li", { className: "item" }, [
    el("span", { className: "badge merge", textContent: "MIX" }),
    el("div", { className: "info" }, [
      el("div", { className: "title" }),
      el("div", { className: "meta" }),
      el("div", { className: "bar" }, [el("span")]),
      el("div", { className: "error-text" }),
    ]),
    el("div", { className: "actions" }),
  ]);
  row.dataset.id = merge.id;
  return row;
}

function updateMergeRow(row, merge) {
  const info = row.querySelector(".info");
  info.querySelector(".title").textContent = `${merge.name}.${merge.audio_format}`;
  info.querySelector(".meta").replaceChildren(
    el("span", { className: `status ${merge.status}`, textContent: STATUS_LABEL[merge.status] }),
    ` · ${merge.job_ids.length} đoạn${merge.gap_seconds ? ` · cách ${merge.gap_seconds}s` : ""}`,
  );
  updateProgress(info, merge);
  info.querySelector(".error-text").textContent = merge.error || "";

  const signature = `${merge.status}|${merge.has_file}|${server.apiBase}|${server.apiKey}`;
  if (row.dataset.signature === signature) return;
  row.dataset.signature = signature;
  const remove = el("button", { className: "icon", textContent: "✕", title: "Xoá", ariaLabel: "Xoá" });
  remove.addEventListener("click", () => command(`/api/merges/${merge.id}`, "DELETE"));
  const fileUrl = apiUrl(`/api/merges/${merge.id}/file`, true);
  const actions = merge.has_file ? [downloadButton(fileUrl, true), remove] : [remove];
  row.querySelector(".actions").replaceChildren(...actions);
  row.querySelector("audio")?.remove();
  if (merge.has_file) row.append(el("audio", { controls: true, preload: "none", src: fileUrl }));
}

function renderMerges(merges) {
  syncList($("merges"), merges, buildMergeRow, updateMergeRow, true);
  $("merges-empty").hidden = merges.length > 0;
}

/* ---------- Đồng bộ ---------- */

function renderAll() {
  renderJobs([...state.jobs.values()]);
  renderMergeOrder();
  renderMerges([...state.merges.values()]);
}

let refreshSeq = 0;
let appliedSeq = 0;

async function refresh() {
  const seq = ++refreshSeq;
  const [jobs, merges] = await Promise.all([api("/api/jobs"), api("/api/merges")]);
  if (seq < appliedSeq) return; // một refresh mới hơn đã áp dụng — bỏ dữ liệu cũ
  appliedSeq = seq;
  state.jobs = new Map(jobs.map((j) => [j.id, j]));
  state.merges = new Map(merges.map((m) => [m.id, m]));
  renderAll();
}

async function poll() {
  try {
    await refresh();
    if (state.online !== true) setOnline(true);
  } catch (err) {
    if (state.online !== false) setOnline(false, `Không kết nối được máy chủ: ${err.message}`);
  }
  setTimeout(poll, POLL_MS);
}

function showRetention(health) {
  if (health && health.ttl_hours) {
    $("retention").textContent = `File được giữ trên máy chủ khoảng ${Math.round(health.ttl_hours)} giờ — nhớ tải về.`;
  }
}

function init() {
  $("api-base").value = server.apiBase;
  $("api-key").value = server.apiKey;
  // Trang đã được start-public gắn sẵn địa chỉ máy chủ -> không bắt người dùng nhìn thấy/nhập nữa.
  $("api-base-field").hidden = Boolean(window.L2A_CONFIG && window.L2A_CONFIG.apiBase);
  $("server-form").addEventListener("submit", connectServer);
  $("copy-invite").addEventListener("click", copyInvite);
  api("/api/health").then(showRetention).catch(() => null);
  $("links").addEventListener("input", countLinks);
  $("links").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submitLinks();
  });
  $("submit").addEventListener("click", submitLinks);
  $("select-all").addEventListener("click", selectAllDone);
  $("clear-errors").addEventListener("click", clearErrors);
  $("merge").addEventListener("click", submitMerge);
  $("merge-gap").addEventListener("input", renderMergeOrder);
  poll();
}

init();
