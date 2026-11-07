// Общие хелперы интерфейса.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const P = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
export const icon = {
  dash: P('<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>'),
  chat: P('<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"/>'),
  phone: P('<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>'),
  scale: P('<path d="M12 3v18M5 7h14M7 7l-3 7a3 3 0 0 0 6 0L7 7Zm10 0-3 7a3 3 0 0 0 6 0l-3-7Z"/>'),
  list: P('<path d="M9 6h11M9 12h11M9 18h11"/><path d="m4 6 1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/>'),
  play: P('<path d="M7 4v16l13-8Z"/>'),
  sync: P('<path d="M21 12a9 9 0 0 1-15 6.7L3 16M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/>'),
  upload: P('<path d="M12 16V4m0 0L7 9m5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>'),
  back: P('<path d="M15 18l-6-6 6-6"/>'),
  flag: P('<path d="M5 21V4m0 0h11l-2 4 2 4H5"/>'),
  plus: P('<path d="M12 5v14M5 12h14"/>'),
  trash: P('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'),
  copy: P('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
  check: P('<path d="m5 12 5 5L20 7"/>'),
  alert: P('<path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/>'),
  ext: P('<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'),
  up: P('<path d="M12 19V5M6 11l6-6 6 6"/>'),
  down: P('<path d="M12 5v14M6 13l6 6 6-6"/>'),
  code: P('<path d="m8 8-4 4 4 4M16 8l4 4-4 4"/>'),
};

export const scoreClass = (v) => (v == null ? "none" : v >= 80 ? "hi" : v >= 60 ? "mid" : "lo");
export const fmtScore = (v) => (v == null ? "—" : Number.isInteger(v) ? String(v) : v.toFixed(1));
export const scorePill = (v) => `<span class="cqa-score ${scoreClass(v)}">${fmtScore(v)}</span>`;
export const langTag = (l) => `<span class="cqa-lang ${esc(l)}">${esc(l)}</span>`;
export const channelIcon = (c) => (c === "voice call" ? icon.phone : icon.chat);
export const speakerName = { operator: "Оператор", client: "Клиент", bot: "Бот", system: "Система" };

export const CASE_STATUS = {
  scored: ["Оценён", "brand"],
  passed: ["Пройден", "yes"],
  failed: ["Не пройден", "no"],
  disputed: ["Апелляция", "warn"],
  reviewed: ["Пересмотрен", "na"],
};
export const statusChip = (ev) => {
  if (!ev) return `<span class="cqa-chip">Не оценён</span>`;
  if (ev.status === "running") return `<span class="cqa-chip brand"><span class="cqa-spin" style="width:10px;height:10px"></span> Оценка…</span>`;
  if (ev.status === "error") return `<span class="cqa-chip no" title="${esc(ev.error)}">Ошибка</span>`;
  const [t, c] = CASE_STATUS[ev.case_status] || ["—", ""];
  return `<span class="cqa-chip ${c}">${t}</span>`;
};

// Ответ по критерию → подпись и цвет
export function answerView(crit, a) {
  if (!a) return { label: "—", cls: "" };
  if (a.na) return { label: "N/A", cls: "na" };
  if (a.value == null) return { label: "—", cls: "" };
  if (crit.type === "flag") return a.value >= 1 ? { label: "Да — нарушение", cls: "crit" } : { label: "Нет", cls: "yes" };
  if (crit.type === "binary") return a.value >= 1 ? { label: "Да", cls: "yes" } : { label: "Нет", cls: "no" };
  const s = crit.scale || { min: 0, max: 1 };
  const label = (s.labels || {})[String(a.value)] || (s.labels || {})[String(Math.round(a.value))];
  const cls = a.value >= s.max ? "yes" : a.value <= s.min ? "no" : "part";
  return { label: `${fmtScore(a.value)}/${s.max}`, cls, hint: label };
}

// Допустимые значения критерия для выбора человеком
export function valueOptions(c) {
  if (c.type === "flag") return [{ v: 1, label: "Да — нарушение", cls: "no" }, { v: 0, label: "Нет", cls: "yes" }];
  if (c.type === "binary") return [{ v: 1, label: "Да", cls: "yes" }, { v: 0, label: "Нет", cls: "no" }];
  const s = c.scale;
  const steps = s.steps?.length ? [...s.steps].sort((a, b) => b - a) : Array.from({ length: s.max - s.min + 1 }, (_, i) => s.max - i);
  return steps.map((v) => ({ v, label: String(v), title: s.labels?.[String(v)] || "", cls: v === s.max ? "yes" : v === s.min ? "no" : "part" }));
}

export function download(name, text, type = "application/json") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function toast(msg, err = false) {
  let box = document.querySelector(".cqa-toasts");
  if (!box) {
    box = document.createElement("div");
    box.className = "cqa-toasts";
    (document.querySelector("cqa-app") || document.body).appendChild(box);
  }
  const t = document.createElement("div");
  t.className = `cqa-toast${err ? " err" : ""}`;
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(() => t.remove(), err ? 6000 : 3200);
}

export function modal({ title, body, actions = [] }) {
  const host = document.querySelector("cqa-app") || document.body;
  const bg = document.createElement("div");
  bg.className = "cqa-modal-bg";
  bg.innerHTML = `<div class="cqa-modal"><div class="cqa-card-h"><h3>${esc(title)}</h3><button class="cqa-btn ghost sm cqa-grow-0" data-x style="margin-left:auto">✕</button></div>
    <div class="cqa-card-b">${body}</div>
    <div class="cqa-modal-f">${actions.map((a, i) => `<button class="cqa-btn ${a.cls || ""}" data-a="${i}">${esc(a.label)}</button>`).join("")}</div></div>`;
  const close = () => bg.remove();
  bg.addEventListener("click", async (e) => {
    if (e.target === bg || e.target.closest("[data-x]")) return close();
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const a = actions[+b.dataset.a];
    if (!a.onClick) return close();
    b.disabled = true;
    try {
      if ((await a.onClick(bg)) !== false) close();
    } catch (err) {
      toast(err.message, true);
    } finally {
      b.disabled = false;
    }
  });
  host.appendChild(bg);
  return bg;
}

export const timeAgo = (ts) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "только что";
  if (s < 3600) return `${Math.floor(s / 60)} мин назад`;
  if (s < 86400) return `${Math.floor(s / 3600)} ч назад`;
  return new Date(ts * 1000).toLocaleString("ru-RU");
};

export const fmtTs = (ts) => {
  if (!ts) return "";
  if (/^\d{4}-\d\d-\d\dT/.test(ts)) {
    const d = new Date(ts);
    return isNaN(d) ? ts : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  return ts;
};

export const money = (v) => (v == null ? "—" : `$${v < 0.1 ? v.toFixed(4) : v.toFixed(2)}`);

// Имя пользователя на этом устройстве — только для удобства (без авторизации); в приватном режиме не сохраняется.
export const remembered = (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } };
export const remember = (k, v) => { try { localStorage.setItem(k, v); } catch { /* хранилище недоступно */ } };
