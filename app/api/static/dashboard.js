"use strict";

// Read-only view of the database: row counts per table, then the rows of the one
// you pick, newest first. Archived rows show archived_at; they are deleted 30 days later.

const STRINGS = {
  en: {
    title: "Data", tables: "Tables", rows: "rows", chat: "Chat", csv: "Tokens CSV",
    of: "of", empty: "No rows.", offline: "Could not reach the server.",
  },
  es: {
    title: "Datos", tables: "Tablas", rows: "filas", chat: "Chat", csv: "CSV de tokens",
    of: "de", empty: "Sin filas.", offline: "No se pudo conectar con el servidor.",
  },
};
const lang = navigator.language.toLowerCase().startsWith("es") ? "es" : "en";
const t = (key) => STRINGS[lang][key];
const PAGE = 50;

const tablesBox = document.getElementById("tables");
const detail = document.getElementById("detail");
const rowsTable = document.getElementById("rows");
const range = document.getElementById("range");
const prev = document.getElementById("prev");
const next = document.getElementById("next");

document.documentElement.lang = lang;
document.getElementById("title").textContent = t("title");
document.getElementById("tables-title").textContent = t("tables");
document.getElementById("chat-link").textContent = t("chat");
document.getElementById("csv-link").textContent = t("csv");

let current = null;
let offset = 0;

async function get(path) {
  let response;
  try {
    response = await fetch(path);
  } catch {
    throw new Error(t("offline"));
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

function showError(message) {
  const element = document.createElement("p");
  element.className = "msg error";
  element.textContent = message;
  tablesBox.replaceChildren(element);
}

async function loadTables() {
  let tables;
  try {
    tables = await get("/api/tables");
  } catch (error) {
    showError(error.message);
    return;
  }
  tablesBox.replaceChildren(...tables.map((table) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.table = table.name;
    button.setAttribute("aria-pressed", String(table.name === current));
    const states = Object.entries(table.by_status).map(([state, n]) => `${state} ${n}`).join(" · ");
    button.innerHTML = `<span class="name"></span> <span class="count"></span><span class="states"></span>`;
    button.querySelector(".name").textContent = table.name;
    button.querySelector(".count").textContent = `${table.rows} ${t("rows")}`;
    button.querySelector(".states").textContent = states;
    button.addEventListener("click", () => open(table.name, 0));
    return button;
  }));
}

async function open(name, start) {
  let page;
  try {
    page = await get(`/api/tables/${encodeURIComponent(name)}?limit=${PAGE}&offset=${start}`);
  } catch (error) {
    showError(error.message);
    return;
  }
  current = name;
  offset = start;
  tablesBox.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.table === name)));
  document.getElementById("detail-title").textContent = name;

  const head = document.createElement("tr");
  for (const column of page.columns) {
    const th = document.createElement("th");
    th.textContent = column;
    head.append(th);
  }
  const body = page.rows.map((row) => {
    const tr = document.createElement("tr");
    for (const value of row) {
      const td = document.createElement("td");
      if (value === null) {
        td.className = "null";
        td.textContent = "—";
      } else {
        td.textContent = String(value);
      }
      tr.append(td);
    }
    return tr;
  });
  if (!body.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = page.columns.length;
    td.textContent = t("empty");
    tr.append(td);
    body.push(tr);
  }
  rowsTable.replaceChildren(head, ...body);

  const last = Math.min(start + page.rows.length, page.total);
  range.textContent = `${page.total ? start + 1 : 0}–${last} ${t("of")} ${page.total}`;
  prev.disabled = start === 0;
  next.disabled = last >= page.total;
  detail.hidden = false;
}

prev.addEventListener("click", () => open(current, Math.max(0, offset - PAGE)));
next.addEventListener("click", () => open(current, offset + PAGE));

loadTables();
