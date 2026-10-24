"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  app: $("app"), chatList: $("chat-list"), docList: $("doc-list"), docHint: $("doc-hint"),
  messages: $("messages"), welcome: $("welcome"), thread: $("thread"), title: $("chat-title"),
  input: $("input"), send: $("send"), composer: $("composer"), fileInput: $("file-input"),
  dropzone: $("dropzone"), toasts: $("toasts"), status: $("model-status"),
  banner: $("banner"), bannerText: $("banner-text"), bannerBar: $("banner-bar"), bannerRetry: $("banner-retry"),
};
const state = { chats: [], chatId: null, docs: [], streaming: null, status: null, limits: { mb: 50, ext: [] } };

/* ------------------------------------------------------------ API */
async function api(path, { method = "GET", json, form, signal } = {}) {
  const headers = {};
  if (method !== "GET") headers["X-Ragbox"] = "1";
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = form;
  const res = await fetch(path, { method, headers, body, signal });
  if (!res.ok) {
    let detail = res.statusText;
    try { const data = await res.json(); detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail); } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res;
}
const getJSON = async (path) => (await api(path)).json();

function toast(text) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  els.toasts.append(el);
  setTimeout(() => el.remove(), 5000);
}

/* ------------------------------------------------------------ Markdown (safe subset) */
const escapeHTML = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function inline(text) {
  // `text` is already escaped. Code spans are protected from other replacements.
  const codes = [];
  let s = text.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
       .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
       .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
       .replace(/\[(\d{1,2})\](?!\()/g, '<button class="cite" data-n="$1" title="Show source $1">$1</button>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
}

function markdown(src) {
  const lines = escapeHTML(src).split("\n");
  const out = [];
  let i = 0;
  const isTableRow = (l) => /^\s*\|.*\|\s*$/.test(l);
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*```/);
    if (fence) {
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++;
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { const lvl = Math.min(h[1].length + 2, 4); out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`); i++; continue; }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, ""));
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${tag}>`);
      continue;
    }
    if (isTableRow(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) rows.push(cells(lines[i++]));
      out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    if (/^&gt;\s?/.test(line)) {
      const quote = [];
      while (i < lines.length && /^&gt;\s?/.test(lines[i])) quote.push(lines[i++].replace(/^&gt;\s?/, ""));
      out.push(`<blockquote>${inline(quote.join("<br>"))}</blockquote>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,6}\s|[-*+]\s|\d+[.)]\s|&gt;)/.test(lines[i]) && !isTableRow(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    out.push(`<p>${inline(para.join("<br>"))}</p>`);
  }
  return out.join("");
}

/* ------------------------------------------------------------ Chats */
async function loadChats() {
  state.chats = await getJSON("/api/chats");
  const current = state.chats.find((c) => c.id === state.chatId);
  if (current) els.title.textContent = current.title;
  renderChats();
}

function renderChats() {
  els.chatList.replaceChildren(...state.chats.map((chat) => {
    const li = document.createElement("li");
    if (chat.id === state.chatId) li.className = "active";
    const label = document.createElement("button");
    label.className = "label";
    label.textContent = chat.title;
    label.onclick = () => openChat(chat.id);
    li.append(label, deleteButton(`Delete chat “${chat.title}”`, async () => {
      await api(`/api/chats/${chat.id}`, { method: "DELETE" });
      if (chat.id === state.chatId) newChat();
      loadChats();
    }));
    return li;
  }));
}

function deleteButton(label, onConfirm) {
  const btn = document.createElement("button");
  btn.className = "del";
  btn.setAttribute("aria-label", label);
  btn.title = "Delete";
  btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-12M9 7V4h6v3"/></svg>';
  btn.onclick = async (e) => {
    e.stopPropagation();
    if (!confirm(`${label}?`)) return;
    try { await onConfirm(); } catch (err) { toast(err.message); }
  };
  return btn;
}

function newChat() {
  stopStreaming();
  state.chatId = null;
  els.messages.replaceChildren();
  els.welcome.hidden = false;
  els.title.textContent = "New chat";
  renderChats();
  closeSidebar();
  els.input.focus();
}

async function openChat(id) {
  stopStreaming();
  try {
    const chat = await getJSON(`/api/chats/${id}`);
    state.chatId = id;
    els.title.textContent = chat.title;
    els.welcome.hidden = chat.messages.length > 0;
    els.messages.replaceChildren(...chat.messages.map((m) => m.role === "user" ? userMessage(m.content) : assistantMessage(m.content, m.sources)));
    renderChats();
    closeSidebar();
    scrollToEnd(true);
  } catch (err) { toast(err.message); }
}

function userMessage(text) {
  const div = document.createElement("div");
  div.className = "msg user";
  div.textContent = text;
  return div;
}

function assistantMessage(text, sources = []) {
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.innerHTML = '<img class="avatar" src="favicon.svg" alt=""><div><div class="body"></div><div class="msg-tools"></div><div class="sources" hidden></div></div>';
  const view = { wrap, body: wrap.querySelector(".body"), tools: wrap.querySelector(".msg-tools"), list: wrap.querySelector(".sources"), text, sources };
  wrap._view = view;
  renderAssistant(view, false);
  return wrap;
}

function renderAssistant(view, streaming) {
  view.body.innerHTML = view.text ? markdown(view.text) : '<div class="thinking"><span class="spinner"></span>Reading your documents…</div>';
  view.body.classList.toggle("cursor", streaming && !!view.text);
  view.body.querySelectorAll(".cite").forEach((btn) => {
    const n = +btn.dataset.n;
    if (!view.sources.some((s) => s.n === n)) { btn.replaceWith(document.createTextNode(`[${n}]`)); return; }
    btn.onclick = () => showSource(view, n);
  });
  if (!streaming) renderTools(view);
}

function renderTools(view) {
  view.tools.replaceChildren();
  if (view.sources.length) {
    const toggle = document.createElement("button");
    toggle.className = "chip";
    toggle.textContent = `${view.sources.length} source${view.sources.length > 1 ? "s" : ""}`;
    toggle.onclick = () => { view.list.hidden = !view.list.hidden; if (!view.list.hidden) renderSources(view); };
    view.tools.append(toggle);
  }
  if (view.text) {
    const copy = document.createElement("button");
    copy.className = "chip";
    copy.textContent = "Copy";
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(view.text); copy.textContent = "Copied"; setTimeout(() => (copy.textContent = "Copy"), 1500); }
      catch { toast("Copy isn't available in this browser."); }
    };
    view.tools.append(copy);
  }
}

function renderSources(view) {
  view.list.replaceChildren(...view.sources.map((s) => {
    const card = document.createElement("article");
    card.className = "source";
    card.dataset.n = s.n;
    const head = document.createElement("header");
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = `[${s.n}]`;
    const name = document.createElement("span");
    name.textContent = s.page ? `${s.name} · page ${s.page}` : s.name;
    head.append(n, name);
    const p = document.createElement("p");
    p.textContent = s.text;
    card.append(head, p);
    return card;
  }));
}

function showSource(view, n) {
  if (view.list.hidden || !view.list.children.length) { view.list.hidden = false; renderSources(view); }
  const card = view.list.querySelector(`.source[data-n="${n}"]`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 1200);
}

/* ------------------------------------------------------------ Sending + streaming */
function stopStreaming() {
  if (state.streaming) state.streaming.abort();
  state.streaming = null;
  updateSend();
}

function updateSend() {
  const busy = !!state.streaming;
  els.send.classList.toggle("stop", busy);
  els.send.setAttribute("aria-label", busy ? "Stop" : "Send");
  els.send.disabled = !busy && !els.input.value.trim();
}

function scrollToEnd(force = false) {
  const t = els.thread;
  if (force || t.scrollHeight - t.scrollTop - t.clientHeight < 160) t.scrollTop = t.scrollHeight;
}

async function send(text) {
  if (!state.chatId) {
    const chat = await (await api("/api/chats", { method: "POST" })).json();
    state.chatId = chat.id;
  }
  els.welcome.hidden = true;
  els.messages.append(userMessage(text));
  const msg = assistantMessage("", []);
  const view = msg._view;
  renderAssistant(view, true);
  els.messages.append(msg);
  scrollToEnd(true);

  const controller = new AbortController();
  state.streaming = controller;
  updateSend();
  let pending = false;
  const repaint = () => { if (pending) return; pending = true; requestAnimationFrame(() => { pending = false; if (!view.done) { renderAssistant(view, true); scrollToEnd(); } }); };

  try {
    const res = await api(`/api/chats/${state.chatId}/messages`, { method: "POST", json: { content: text }, signal: controller.signal });
    loadChats();
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const event = (block.match(/^event: (.*)$/m) || [])[1];
        const dataLine = (block.match(/^data: (.*)$/m) || [])[1];
        if (!event || dataLine === undefined) continue;
        const data = JSON.parse(dataLine);
        if (event === "sources") view.sources = data;
        else if (event === "token") { view.text += data; repaint(); }
        else if (event === "error") { view.text += (view.text ? "\n\n" : "") + `⚠️ ${data.message}`; }
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") view.text += (view.text ? "\n\n" : "") + `⚠️ ${err.message}`;
  } finally {
    if (state.streaming === controller) state.streaming = null;
    if (!view.text) view.text = "_Stopped._";
    view.done = true;
    renderAssistant(view, false);
    updateSend();
    loadChats();
  }
}

els.composer.addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.streaming) { stopStreaming(); return; }
  const text = els.input.value.trim();
  if (!text) return;
  els.input.value = "";
  autosize();
  send(text).catch((err) => toast(err.message));
});
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); els.composer.requestSubmit(); }
});
const autosize = () => { els.input.style.height = "auto"; els.input.style.height = `${Math.min(els.input.scrollHeight, 220)}px`; updateSend(); };
els.input.addEventListener("input", autosize);

/* ------------------------------------------------------------ Documents */
async function loadDocs() {
  try { state.docs = await getJSON("/api/documents"); } catch { return; }
  renderDocs();
}

function renderDocs() {
  els.docHint.hidden = state.docs.length > 0;
  els.docList.replaceChildren(...state.docs.map((doc) => {
    const li = document.createElement("li");
    li.title = doc.name;
    const icon = document.createElement("span");
    icon.className = "doc-icon";
    icon.textContent = doc.ext.replace(".", "").slice(0, 4);
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = doc.name;
    const meta = document.createElement("span");
    meta.className = "doc-meta";
    if (doc.status === "ready") meta.textContent = `${doc.chunks} passage${doc.chunks === 1 ? "" : "s"} · ${formatSize(doc.size)}`;
    else if (doc.status === "error") { meta.className += " err"; meta.textContent = doc.error || "Couldn't read this file"; li.title = meta.textContent; }
    else meta.innerHTML = `<span class="spinner"></span>${doc.status === "queued" ? "Waiting…" : "Indexing…"}`;
    label.append(meta);
    li.append(icon, label, deleteButton(`Remove “${doc.name}”`, async () => {
      await api(`/api/documents/${doc.id}`, { method: "DELETE" });
      loadDocs();
    }));
    return li;
  }));
}

const formatSize = (b) => b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1048576).toFixed(1)} MB`;

async function upload(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const form = new FormData();
  for (const f of files) {
    if (f.size > state.limits.mb * 1048576) { toast(`${f.name} is larger than ${state.limits.mb} MB.`); continue; }
    form.append("files", f, f.name);
  }
  if (!form.has("files")) return;
  try {
    const results = await (await api("/api/documents", { method: "POST", form })).json();
    results.filter((r) => r.error).forEach((r) => toast(`${r.name}: ${r.error}`));
    const added = results.filter((r) => !r.error).length;
    if (added) toast(`${added} file${added > 1 ? "s" : ""} added — indexing…`);
  } catch (err) { toast(err.message); }
  loadDocs();
}

for (const id of ["attach", "add-docs", "drop-card"]) $(id).addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", () => { upload(els.fileInput.files); els.fileInput.value = ""; });

let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
window.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; els.dropzone.hidden = false; });
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("dragleave", (e) => { if (!hasFiles(e)) return; dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) els.dropzone.hidden = true; });
window.addEventListener("drop", (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth = 0; els.dropzone.hidden = true; upload(e.dataTransfer.files); });

/* ------------------------------------------------------------ Model status */
async function pollStatus() {
  let delay = 2000;
  try {
    const s = await getJSON("/api/status");
    state.limits = { mb: s.max_upload_mb, ext: s.extensions };
    const { chat, embed } = s.models;
    const ready = chat.state === "ready" && embed.state === "ready";
    const failed = [chat, embed].find((m) => m.state === "error");
    const waiting = [chat, embed].find((m) => m.state === "unreachable");
    const downloading = [embed, chat].find((m) => m.state === "downloading");
    els.status.className = `status ${ready ? "ok" : failed ? "bad" : ""}`;
    els.status.querySelector("b").textContent = ready ? chat.name : failed ? "Model error" : "Preparing models…";
    els.banner.hidden = ready;
    els.bannerRetry.hidden = !failed;
    if (failed) els.bannerText.textContent = failed.error;
    else if (waiting) els.bannerText.textContent = "Starting the local AI engine…";
    else if (downloading) {
      const pct = Math.round(downloading.progress * 100);
      els.bannerText.textContent = `First launch: downloading ${downloading.name} (${pct}%). This happens only once.`;
      els.bannerBar.style.width = `${pct}%`;
    } else els.bannerText.textContent = "Checking local AI models…";
    if (ready) delay = 15000;
  } catch {
    els.status.className = "status bad";
    els.status.querySelector("b").textContent = "Server unreachable";
  }
  setTimeout(pollStatus, delay);
}
els.bannerRetry.addEventListener("click", () => api("/api/models/retry", { method: "POST" }).catch((e) => toast(e.message)));

setInterval(() => { if (state.docs.some((d) => d.status === "queued" || d.status === "processing")) loadDocs(); }, 1500);

/* ------------------------------------------------------------ Layout, theme */
$("new-chat").addEventListener("click", newChat);
const closeSidebar = () => els.app.classList.remove("sidebar-open");
$("open-sidebar").addEventListener("click", () => els.app.classList.add("sidebar-open"));
$("close-sidebar").addEventListener("click", closeSidebar);
$("theme").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("ragbox-theme", document.documentElement.dataset.theme); } catch {}
});
try { const t = localStorage.getItem("ragbox-theme"); if (t) document.documentElement.dataset.theme = t; } catch {}

loadChats().catch((e) => toast(e.message));
loadDocs();
pollStatus();
els.input.focus();
