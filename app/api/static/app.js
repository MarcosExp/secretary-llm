"use strict";

const STRINGS = {
  en: {
    placeholder: "Message…", newChat: "New chat", thinking: "Thinking…", transcribing: "Transcribing…",
    needsConfirmation: "Needs your confirmation", confirm: "Confirm", reject: "Discard",
    micUnavailable: "Voice needs HTTPS (open the app through its Tailscale address).",
    micDenied: "Microphone permission denied.", tools: "tools", tokens: "tokens",
    offline: "Could not reach the server.", dashboard: "Data", csv: "Tokens CSV",
  },
  es: {
    placeholder: "Mensaje…", newChat: "Nueva conversación", thinking: "Pensando…", transcribing: "Transcribiendo…",
    needsConfirmation: "Necesita tu confirmación", confirm: "Confirmar", reject: "Descartar",
    micUnavailable: "La voz necesita HTTPS (abre la app por su dirección de Tailscale).",
    micDenied: "Permiso de micrófono denegado.", tools: "herramientas", tokens: "tokens",
    offline: "No se pudo conectar con el servidor.", dashboard: "Datos", csv: "CSV de tokens",
  },
};
const lang = navigator.language.toLowerCase().startsWith("es") ? "es" : "en";
const t = (key) => STRINGS[lang][key];

const messages = document.getElementById("messages");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const send = document.getElementById("send");
const mic = document.getElementById("mic");
const newChat = document.getElementById("new-chat");

document.documentElement.lang = lang;
input.placeholder = t("placeholder");
newChat.textContent = t("newChat");
document.getElementById("dashboard-link").textContent = t("dashboard");
document.getElementById("csv-link").textContent = t("csv");

// --- Local state: the visible transcript. The model's history lives on the server. ---

const LOG_LIMIT = 100;
let conversationId = Number(localStorage.getItem("conversationId")) || null;
let log = JSON.parse(localStorage.getItem("log") || "[]");

function remember(entry) {
  log.push(entry);
  log = log.slice(-LOG_LIMIT);
  localStorage.setItem("log", JSON.stringify(log));
}

function setConversation(id) {
  conversationId = id;
  if (id) localStorage.setItem("conversationId", String(id));
  else localStorage.removeItem("conversationId");
}

// --- API (every write carries X-Secretary: see app/api/main.py) ---

async function api(path, options = {}) {
  const headers = { "X-Secretary": "1", ...(options.headers || {}) };
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch {
    throw new Error(t("offline"));
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

// --- Rendering ---

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
}

// Small Markdown subset the agent uses: headings, lists, bold, italics, code, paragraphs.
function markdown(text) {
  const html = [];
  let list = null;
  const closeList = () => { if (list) { html.push(`</${list}>`); list = null; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    let match;
    if ((match = line.match(/^\s*[-*•]\s+(.*)/))) {
      if (list !== "ul") { closeList(); html.push("<ul>"); list = "ul"; }
      html.push(`<li>${inline(match[1])}</li>`);
    } else if ((match = line.match(/^\s*\d+[.)]\s+(.*)/))) {
      if (list !== "ol") { closeList(); html.push("<ol>"); list = "ol"; }
      html.push(`<li>${inline(match[1])}</li>`);
    } else if ((match = line.match(/^#{1,6}\s+(.*)/))) {
      closeList();
      html.push(`<h3>${inline(match[1])}</h3>`);
    } else if (line.trim() === "" || /^-{3,}$/.test(line.trim())) {
      closeList();
    } else {
      closeList();
      html.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return html.join("");
}

function scrollDown() {
  messages.scrollTop = messages.scrollHeight;
}

function addMessage(role, text, { save = true } = {}) {
  const element = document.createElement("div");
  element.className = `msg ${role}`;
  if (role === "assistant") element.innerHTML = markdown(text);
  else element.textContent = text;
  messages.append(element);
  if (save) remember({ role, text });
  scrollDown();
  return element;
}

function addMeta(response) {
  const element = document.createElement("div");
  element.className = "meta";
  const tokens = response.input_tokens + response.output_tokens;
  element.textContent = `${response.tool_calls} ${t("tools")} · ${(tokens / 1000).toFixed(1)}k ${t("tokens")}`;
  messages.append(element);
}

function addTyping(key) {
  const element = document.createElement("div");
  element.className = "typing";
  element.textContent = t(key);
  messages.append(element);
  scrollDown();
  return element;
}

const shownPending = new Set();

function addPending(action) {
  if (shownPending.has(action.id)) return;
  shownPending.add(action.id);
  const card = document.createElement("div");
  card.className = "pending";
  card.innerHTML = `
    <div class="title">${t("needsConfirmation")} (#${action.id})</div>
    <div class="summary"></div>
    <div class="actions">
      <button type="button" data-decision="confirm">${t("confirm")}</button>
      <button type="button" class="secondary" data-decision="reject">${t("reject")}</button>
    </div>`;
  card.querySelector(".summary").textContent = action.summary;
  card.addEventListener("click", async (event) => {
    const decision = event.target.dataset?.decision;
    if (!decision) return;
    card.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      const result = await api(`/api/pending/${action.id}/${decision}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId }),
      });
      card.replaceWith(addMessage("note", result.message));
    } catch (error) {
      card.replaceWith(addMessage("error", error.message, { save: false }));
    }
  });
  messages.append(card);
  scrollDown();
}

function showResponse(response) {
  setConversation(response.conversation_id);
  addMessage("assistant", response.reply);
  addMeta(response);
  response.pending.forEach(addPending);
}

function busy(isBusy) {
  send.disabled = isBusy;
  mic.disabled = isBusy && !mic.classList.contains("recording");
}

// --- Text ---

async function sendText(text) {
  addMessage("user", text);
  const typing = addTyping("thinking");
  busy(true);
  try {
    showResponse(await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, conversation_id: conversationId }),
    }));
  } catch (error) {
    addMessage("error", error.message, { save: false });
  } finally {
    typing.remove();
    busy(false);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || send.disabled) return;
  input.value = "";
  autosize();
  sendText(text);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function autosize() {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}
input.addEventListener("input", autosize);

// --- Voice ---

let recorder = null;

async function sendVoice(blob, extension) {
  const placeholder = addMessage("user", "🎙️ …", { save: false });
  const typing = addTyping("transcribing");
  busy(true);
  const body = new FormData();
  body.append("audio", blob, `recording${extension}`);
  if (conversationId) body.append("conversation_id", String(conversationId));
  try {
    const response = await api("/api/voice", { method: "POST", body });
    placeholder.textContent = response.transcript;
    remember({ role: "user", text: response.transcript });
    showResponse(response);
  } catch (error) {
    placeholder.remove();
    addMessage("error", error.message, { save: false });
  } finally {
    typing.remove();
    busy(false);
  }
}

mic.addEventListener("click", async () => {
  if (recorder && recorder.state === "recording") {
    recorder.stop();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    addMessage("error", t("micUnavailable"), { save: false });
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    addMessage("error", t("micDenied"), { save: false });
    return;
  }
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m));
  recorder = new MediaRecorder(stream, type ? { mimeType: type } : {});
  const chunks = [];
  recorder.addEventListener("dataavailable", (event) => chunks.push(event.data));
  recorder.addEventListener("stop", () => {
    stream.getTracks().forEach((track) => track.stop());
    mic.classList.remove("recording");
    const mimeType = recorder.mimeType || "audio/webm";
    sendVoice(new Blob(chunks, { type: mimeType }), mimeType.includes("mp4") ? ".m4a" : ".webm");
  });
  recorder.start();
  mic.classList.add("recording");
});

// --- Start ---

newChat.addEventListener("click", () => {
  setConversation(null);
  log = [];
  localStorage.removeItem("log");
  messages.replaceChildren();
  shownPending.clear();
  loadPending();
});

async function loadPending() {
  try {
    (await api("/api/pending")).forEach(addPending);
  } catch {
    // Offline: the cards will show up on the next successful request.
  }
}

for (const entry of log) addMessage(entry.role, entry.text, { save: false });
loadPending();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js");
}
