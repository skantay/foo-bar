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
export const fmtScore = (v) => (v == null || v === "" || isNaN(v) ? "—" : Number.isInteger(+v) ? String(+v) : (+v).toFixed(1));
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

// Цвет статуса критерия — один и тот же в реестре, таблице и карточках:
// yes — выполнено, part — частично, no — нет, na — N/A, crit — нарушение / грубая ошибка
export const markOf = (crit, a) => (a?.critical_error ? "crit" : answerView(crit, a).cls || "none");

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

// Силуэт «железной маски в капюшоне» — оригинальный рисунок для оформления в теме хакатона.
// Монохромный: цвет задаётся через currentColor, прорези глаз и решётка — вырезы (fill-rule evenodd).
export const SIGIL = `<svg viewBox="0 0 240 246" aria-hidden="true" focusable="false"><g transform="scale(1 .82)">
  <path fill="currentColor" opacity=".62" d="M6 300l10-46c6-22 26-34 50-38l54-8 54 8c24 4 44 16 50 38l10 46Z"/>
  <path fill="currentColor" opacity=".9" d="M14 300l8-38c6-20 24-30 46-32 18 2 30 14 34 32l2 38Z M226 300l-8-38c-6-20-24-30-46-32-18 2-30 14-34 32l-2 38Z"/>
  <path fill="currentColor" opacity=".82" fill-rule="evenodd" d="M120 6C74 10 44 44 38 96c-5 44-2 92-12 138 20 6 42 6 62 2l32 14 32-14c20 4 42 4 62-2-10-46-7-94-12-138C196 44 166 10 120 6Z
    M120 44l-32 4c-14 4-20 20-20 38v66l8 48 18 32 26 14 26-14 18-32 8-48V86c0-18-6-34-20-38Z"/>
  <path fill="currentColor" d="M120 6c-20 8-34 22-40 40 12-6 26-9 40-9s28 3 40 9c-6-18-20-32-40-40Z
    M58 64c-8 40-8 98-16 160l6 2c6-60 6-116 14-158Z M182 64c8 40 8 98 16 160l-6 2c-6-60-6-116-14-158Z"/>
  <path fill="currentColor" fill-rule="evenodd" d="M120 54l-27 4c-11 4-15 16-15 32v58l7 44 16 28 19 10 19-10 16-28 7-44V90c0-16-4-28-15-32Z
    M80 104l40 10 40-10v6l-40 11-40-11Z
    M86 118l28 7-3 9-24-5Z M154 118l-28 7 3 9 24-5Z
    M118 136h4v32l-2 3-2-3Z
    M84 134l12 54h3l-11-54Z M156 134l-12 54h-3l11-54Z
    M100 192h40l-5 7h-30Z
    M105 203h3v16h-3Z M112 203h3v19h-3Z M118.5 203h3v20h-3Z M125 203h3v19h-3Z M132 203h3v16h-3Z"/>
  <g fill="currentColor" opacity=".55"><circle cx="84" cy="98" r="2.6"/><circle cx="156" cy="98" r="2.6"/><circle cx="82" cy="150" r="2.6"/><circle cx="158" cy="150" r="2.6"/><circle cx="98" cy="214" r="2.3"/><circle cx="142" cy="214" r="2.3"/></g>
</g></svg>`;

// Сцены темы хакатона (оригинальные силуэты): фигура в плаще во весь рост и она же на троне.
export const STAND = `<svg viewBox="0 0 200 440" aria-hidden="true" focusable="false">
  <path fill="currentColor" fill-rule="evenodd" d="M100 6c18 6 37 24 37 50 0 18-6 32-15 41 20 2 37 10 43 26 9 76 18 186 33 307-30-10-62-4-98 6-36-10-68-16-98-6 15-121 24-231 33-307 6-16 23-24 43-26-9-9-15-23-15-41 0-26 19-44 37-50Z
    M100 32c-14 0-22 12-22 26v14c0 15 10 25 22 27 12-2 22-12 22-27V58c0-14-8-26-22-26Z
    M98 240 90 432h20l-8-192Z"/>
  <path fill="currentColor" fill-rule="evenodd" d="M100 35c-12 0-19 10-19 23v14c0 13 8 22 19 24 11-2 19-11 19-24V58c0-13-7-23-19-23Z
    M84 58l13 3-1 5-11-3Z M116 58l-13 3 1 5 11-3Z M99 67h2v13h-2Z M92 84h16l-3 4H95Z"/>
  <path fill="currentColor" opacity=".35" d="M58 126c-6 90-12 190-22 296l14-4c8-102 14-200 18-290Z M142 126c6 90 12 190 22 296l-14-4c-8-102-14-200-18-290Z"/>
  <g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
    <path stroke-width="2" opacity=".55" d="M60 116c8-10 24-14 38-10M140 116c-8-10-24-14-38-10M56 126c10 4 22 4 32 0M144 126c-10 4-22 4-32 0"/>
    <path stroke-width="1.6" opacity=".7" d="M84 120 100 128 116 120"/>
    <path stroke-width="1.4" opacity=".4" d="M52 150c-6 90-12 180-22 272M148 150c6 90 12 180 22 272M70 170c-2 80-6 170-12 254M130 170c2 80 6 170 12 254"/>
    <path stroke-width="2" opacity=".6" d="M76 212h48M76 220h48"/>
    <path stroke-width="1.6" opacity=".6" d="M72 142c-4 40-2 84 8 120M128 142c4 40 2 84-8 120M74 250c4 4 10 6 16 4M126 250c-4 4-10 6-16 4"/>
    <path stroke-width="1.6" opacity=".55" d="M92 330l8 8 8-8"/>
  </g>
  <g fill="currentColor"><circle cx="84" cy="120" r="4"/><circle cx="116" cy="120" r="4"/><rect x="95" y="210" width="10" height="12" rx="2"/>
    <path opacity=".9" d="M76 258c2 10 8 16 14 16s8-6 6-14c-6 2-14 0-20-2Z M124 258c-2 10-8 16-14 16s-8-6-6-14c6 2 14 0 20-2Z"/></g>
</svg>`;
export const THRONE = `<svg viewBox="0 0 360 310" aria-hidden="true" focusable="false">
  <path fill="currentColor" opacity=".45" d="M92 230 98 62l16-36 16 34 22-42 20 36 8-44 8 44 20-36 22 42 16-34 16 36 6 168Z"/>
  <path fill="currentColor" opacity=".6" d="M54 166h70v18H54Z M236 166h70v18h-70Z M58 184h16v70H58Z M286 184h16v70h-16Z M40 252h280v24H40Z"/>
  <path fill="currentColor" opacity=".4" d="M18 276h324v28H18Z"/>
  <path fill="currentColor" fill-rule="evenodd" d="M180 34c18 6 31 20 31 39 0 14-6 25-14 31 25 4 41 18 45 42l-2 16 20 4v14l-36-2-6-14c2 24 8 46 20 56l6 32H116l6-32c12-10 18-32 20-56l-6 14-36 2v-14l20-4-2-16c4-24 20-38 45-42-8-6-14-17-14-31 0-19 13-33 31-39Z
    M180 54c-11 0-17 9-17 20v11c0 11 8 19 17 21 9-2 17-10 17-21V74c0-11-6-20-17-20Z"/>
  <path fill="currentColor" fill-rule="evenodd" d="M180 57c-9 0-14 8-14 17v11c0 10 6 16 14 18 8-2 14-8 14-18V74c0-9-5-17-14-17Z
    M168 74l10 3-1 4-8-2Z M192 74l-10 3 1 4 8-2Z M179 81h2v9h-2Z M174 93h12l-2 3h-8Z"/>
  <path fill="currentColor" opacity=".3" d="M150 198c8 16 18 26 30 30 12-4 22-14 30-30l-4 54h-52Z"/>
  <g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
    <path stroke-width="1.6" opacity=".55" d="M118 210V96q10-22 20 0v114M162 210V84q18-28 36 0v126M222 210V96q10-22 20 0v114"/>
    <path stroke-width="2" opacity=".6" d="M146 192h68M146 199h68"/>
    <path stroke-width="1.4" opacity=".45" d="M150 120c-6 30-8 60-6 80M210 120c6 30 8 60 6 80M160 206c-4 16-8 30-14 44M200 206c4 16 8 30 14 44"/>
    <path stroke-width="1.8" opacity=".55" d="M154 120c8-8 18-12 26-12s18 4 26 12M166 116l14 8 14-8"/>
  </g>
  <g fill="currentColor"><circle cx="114" cy="24" r="3"/><circle cx="246" cy="24" r="3"/><circle cx="150" cy="16" r="3"/><circle cx="210" cy="16" r="3"/><circle cx="180" cy="8" r="3.5"/>
    <circle cx="166" cy="116" r="3"/><circle cx="194" cy="116" r="3"/><rect x="175" y="190" width="10" height="11" rx="2"/>
    <path d="M100 162c-6 2-10 8-8 14h20c2-6-4-14-12-14Z M260 162c6 2 10 8 8 14h-20c-2-6 4-14 12-14Z"/>
    <circle cx="60" cy="166" r="5"/><circle cx="300" cy="166" r="5"/>
    <path d="M140 246h26l2 8h-30Z M194 246h26l2 8h-30Z"/></g>
</svg>`;

// Сцена для меню (по референсу команды, свой силуэт): фигура в плаще со спины среди роботов-гигантов.
export const ARMY = `<svg viewBox="0 0 260 420" aria-hidden="true" focusable="false">
  <g fill="currentColor" opacity=".22">
    <path d="M72 150l18-2 4 8-1 12-6 5H77l-6-5-1-12Z M76 175h12v6H76Z M62 184c0-3 4-5 8-5h24c4 0 8 2 9 5l3 18-6 3-3 60H64l-3-60-5-3Z"/>
    <path d="M188 142l-18-2-4 8 1 12 6 5h10l6-5 1-12Z M184 167h-12v6h12Z M198 176c0-3-4-5-8-5h-24c-4 0-8 2-9 5l-3 18 6 3 3 60h32l3-60 5-3Z"/>
  </g>
  <g fill="currentColor" opacity=".4">
    <path d="M14 84l34-4 8 14-1 22-10 9H25l-11-9-2-22Z M26 125h18v10H26Z
      M-10 152c0-10 10-17 22-17h40c12 0 22 6 26 16l6 26-12 6-4 70-10 50H2l-10-50Z
      M76 150l18 8 4 70-10 34-10-4 2-32Z"/>
    <path d="M246 68l-34-4-8 14 1 22 10 9h20l11-9 2-22Z M234 109h-18v10h18Z
      M270 136c0-10-10-17-22-17h-40c-12 0-22 6-26 16l-6 26 12 6 4 80 10 50h66l10-50Z
      M184 134l-18 8-4 78 10 34 10-4-2-40Z"/>
  </g>
  <g fill="none" stroke="currentColor" stroke-width="1.2" opacity=".35">
    <path d="M6 160h62M10 176h54M20 94h28M196 144h62M200 160h54M212 78h28M2 200l30 8 30-8M198 184l30 8 30-8"/>
  </g>
  <g fill="#8ff0c3"><circle cx="26" cy="102" r="2.4"/><circle cx="42" cy="102" r="2.4"/><circle cx="218" cy="86" r="2.4"/><circle cx="234" cy="86" r="2.4"/>
    <circle cx="78" cy="160" r="1.4"/><circle cx="86" cy="160" r="1.4"/><circle cx="174" cy="152" r="1.4"/><circle cx="182" cy="152" r="1.4"/></g>
  <g fill="currentColor">
    <path d="M130 212c8 6 15 18 15 32 0 8-2 14-6 18 10 2 18 6 22 12 4 18 8 46 14 72 6 26 14 44 30 56-24 10-50 12-75 10-25 2-51 0-75-10 16-12 24-30 30-56 6-26 10-54 14-72 4-6 12-10 22-12-4-4-6-10-6-18 0-14 7-26 15-32Z"/>
    <path d="M158 270c8-2 16 0 20 6l26 12 10 2 6-2c2 0 3 2 1 3l-4 2 7 1c2 0 2 3 0 3l-7 0 5 4c1 2-1 3-3 2l-6-4 2 6c0 2-2 2-3 1l-4-6c-4 2-8 1-10-2l-26-10c-8-3-14-8-14-18Z"/>
    <path d="M102 270c-8-2-16 0-20 6l-26 12-10 2-6-2c-2 0-3 2-1 3l4 2-7 1c-2 0-2 3 0 3l7 0-5 4c-1 2 1 3 3 2l6-4-2 6c0 2 2 2 3 1l4-6c4 2 8 1 10-2l26-10c8-3 14-8 14-18Z"/>
  </g>
  <g fill="none" stroke="currentColor" stroke-linecap="round" opacity=".5">
    <path stroke-width="1.2" d="M130 226v34M122 276c-4 40-8 80-14 124M138 276c4 40 8 80 14 124M130 280v124"/>
    <path stroke-width="1.4" d="M186 280l-2 6M194 284l-2 6M74 280l2 6M66 284l2 6"/>
  </g>
</svg>`;

// Щит Freedom (по логотипу компании) — для фонового водяного знака. Два тона: светлая часть с «F» и тёмная.
export const BRAND_MARK = `<svg viewBox="0 0 1108 1280" aria-hidden="true" focusable="false">
<path class="d" d="M0 0h1108v300c0 560-250 860-554 980C250 1160 0 860 0 300Z"/>
<path class="l" d="M0 0h1108v80c0 150-80 232-238 232H427v256h446v82c0 150-56 230-232 230H427v338C180 1062 0 800 0 300Z"/></svg>`;
