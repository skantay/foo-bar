import { esc, icon, modal, timeAgo, toast } from "../ui.js";

// Редактор чек-листов. Главное — минимум решений для автора: название, что проверять, тип ответа.
// Всё остальное (секции, округление, памятка, ID) — в «Дополнительно» и имеет разумные значения по умолчанию.

const TYPES = [
  { v: "binary", t: "Да / Нет", d: "выполнено или нет" },
  { v: "scale", t: "Баллы по шкале", d: "несколько ступеней качества" },
  { v: "flag", t: "Нарушение", d: "отметка без баллов" },
];

const TEMPLATES = {
  empty: () => ({ name: "Новый чек-лист", description: "", criteria: [], sections: [] }),
  greeting: () => ({
    name: "Проверка приветствия", description: "", sections: [],
    criteria: [{ id: "greeting", name: "Приветствие", type: "binary", weight: 10, na_allowed: false, na_rule: "", appealable: true,
      description: "Да — в первом ответе оператор поздоровался на языке клиента и назвал своё имя." }],
  }),
};

const DEFAULTS = { rounding: { digits: 2, mode: "half_up" }, critical_mode: "flag", pass_threshold: null, first_response_limit_s: 180, guidelines: "" };

export async function mount(el, ctx, [selId], top) {
  let all = await ctx.api.checklists();
  let base = all.find((v) => v.id === selId) || all.find((v) => v.active) || all[0];
  let draft = clone(base);
  let isNew = false;
  const openFamilies = new Set([base?.family_id]);
  let advOpen = false;

  ctx.setTitle("Чек-листы", "Что и как проверяем у оператора");
  top.innerHTML = `<button class="cqa-btn primary" data-new>${icon.plus} Новый чек-лист</button>`;
  el.innerHTML = `<div class="cqa-cl-layout"><div data-side></div><div data-ed></div></div>`;
  const $ = (s) => el.querySelector(s);

  // ---------- левая колонка: чек-листы и их версии ----------
  function renderSide() {
    const fams = {};
    for (const v of all) (fams[v.family_id] ||= []).push(v);
    const list = Object.values(fams).map((vs) => vs.sort((a, b) => b.version - a.version));
    list.sort((a, b) => (b.some((v) => v.active) - a.some((v) => v.active)) || b[0].created_at - a[0].created_at);
    $("[data-side]").innerHTML = `<div class="cqa-card" style="overflow:hidden">
      ${isNew ? `<div class="cqa-ver on"><b>${esc(draft.name || "Новый чек-лист")}</b><div class="cqa-muted" style="font-size:12.5px">черновик · не сохранён</div></div>` : ""}
      ${list.map((vs) => {
        const latest = vs[0], active = vs.find((v) => v.active), open = openFamilies.has(latest.family_id);
        return `<div>
          <div class="cqa-ver ${!isNew && vs.some((v) => v.id === base.id) ? "on" : ""}" data-v="${latest.id}" data-fam="${latest.family_id}">
            <div class="cqa-row"><b class="cqa-grow">${esc(latest.name)}</b>${active ? `<span class="cqa-chip yes">Активен · v${active.version}</span>` : ""}</div>
            <div class="cqa-muted" style="font-size:12.5px;margin-top:2px">${latest.criteria.length} ${plural(latest.criteria.length, "критерий", "критерия", "критериев")} · ${vs.length} ${plural(vs.length, "версия", "версии", "версий")}</div>
          </div>
          ${open && vs.length > 1 ? `<div class="cqa-vers">${vs.map((v) => `<div class="cqa-vrow ${!isNew && v.id === base.id ? "on" : ""}" data-v="${v.id}">
            v${v.version}${v.active ? " · активна" : ""}<span class="cqa-faint" style="margin-left:auto">${timeAgo(v.created_at)}</span></div>`).join("")}</div>` : ""}
        </div>`;
      }).join("")}
    </div>`;
  }

  // ---------- редактор ----------
  function renderEditor() {
    // шкала {min, max} без ступеней (чек-лист из API) → явные ступени, чтобы их можно было править
    draft.criteria.forEach((c) => {
      if (c.type === "scale" && !c.scale?.steps?.length) c.scale = { ...c.scale, steps: [c.scale?.min ?? 0, c.scale?.max ?? 1] };
    });
    const sum = draft.criteria.filter((c) => c.type !== "flag").reduce((s, c) => s + (+c.weight || 0), 0);
    $("[data-ed]").innerHTML = `
      <div class="cqa-card cqa-card-b" style="margin-bottom:14px">
        <input class="cqa-title-in" data-g="name" value="${esc(draft.name || "")}" placeholder="Название чек-листа">
        <input class="cqa-desc-in" data-g="description" value="${esc(draft.description || "")}" placeholder="Описание — необязательно">
        ${!isNew && base ? `<div class="cqa-row cqa-faint" style="font-size:12.5px;margin-top:8px">
          Версия v${base.version}${base.active ? ` · <span style="color:var(--accent);font-weight:600">активна — по ней идёт оценка</span>` : ` · <button class="cqa-btn sm" data-activate>Сделать активным</button>`}</div>` : ""}
      </div>

      <div class="cqa-row" style="margin:4px 2px 10px"><h3 style="margin:0;font-size:15px">Критерии</h3>
        <span class="cqa-faint" style="font-size:12.5px">${draft.criteria.length ? "вес пересчитывается в % от итога автоматически" : ""}</span></div>
      ${draft.criteria.map((c, i) => critCard(c, i, draft, sum)).join("")}

      <div class="cqa-add">
        <button class="cqa-btn primary" data-add>${icon.plus} Добавить критерий</button>
      </div>

      <details class="cqa-card cqa-adv" ${advOpen ? "open" : ""} data-adv>
        <summary>Дополнительно <span class="cqa-faint">— секции, общие правила, округление, порог. Можно не трогать</span></summary>
        <div class="cqa-card-b cqa-ed-grid">
          <div class="cqa-field c12"><label class="cqa-label">Общие правила оценки</label>
            <div class="cqa-faint" style="font-size:12px;margin-bottom:6px">Пояснения сразу ко всем критериям. Их читают и модель, и эксперты.</div>
            <textarea class="cqa-textarea" style="min-height:90px" data-g="guidelines" placeholder="Например: реплики бота не оцениваются; «Нет» — действие было нужно, но пропущено">${esc(draft.guidelines || "")}</textarea></div>
          <div class="cqa-field c12"><label class="cqa-label">Секции</label>
            <div class="cqa-faint" style="font-size:12px;margin-bottom:6px">Группы критериев для промежуточных баллов по блокам («Начало диалога», «Решение»…). Короткому чек-листу не нужны.</div>
            <div class="cqa-row">${(draft.sections || []).map((s, i) => `<span class="cqa-secchip"><input data-s="${i}" value="${esc(s.name)}"><button class="cqa-btn ghost sm" data-del-sec="${i}">✕</button></span>`).join("")}
              <button class="cqa-btn sm" data-add-sec>${icon.plus} Секция</button></div></div>
          <div class="cqa-field c3"><label class="cqa-label">Порог «пройден», баллов</label><input class="cqa-input" type="number" data-g="pass_threshold" value="${draft.pass_threshold ?? ""}" placeholder="нет"></div>
          <div class="cqa-field c3"><label class="cqa-label">Первая реакция оператора, сек</label><input class="cqa-input" type="number" data-g="first_response_limit_s" value="${draft.first_response_limit_s ?? 180}"></div>
          <div class="cqa-field c3"><label class="cqa-label">Округление итога</label><select class="cqa-select" data-g="rounding.digits">
            ${[0, 1, 2].map((d) => `<option value="${d}" ${+(draft.rounding?.digits ?? 2) === d ? "selected" : ""}>${d} знак${d === 1 ? "" : "а"} после запятой</option>`).join("")}</select></div>
          <div class="cqa-field c3"><label class="cqa-label">Если найдено нарушение</label><select class="cqa-select" data-g="critical_mode">
            <option value="flag" ${draft.critical_mode !== "zero" ? "selected" : ""}>Отметить отдельно</option><option value="zero" ${draft.critical_mode === "zero" ? "selected" : ""}>Обнулить итог</option></select></div>
        </div>
      </details>

      <div class="cqa-savebar"><div class="cqa-grow" style="font-size:13px" data-status></div>
        <button class="cqa-btn ghost" data-reset ${isNew ? "hidden" : ""}>Отменить изменения</button>
        <button class="cqa-btn primary" data-save>${icon.check} ${isNew ? "Создать чек-лист" : "Сохранить новую версию"}</button>
      </div>`;
    refreshBar();
  }

  function critCard(c, i, d, sum) {
    const pct = sum ? Math.round(((+c.weight || 0) / sum) * 100) : 0;
    const steps = c.scale?.steps?.length ? c.scale.steps : [c.scale?.min ?? 0, c.scale?.max ?? 1];
    return `<div class="cqa-ed-crit" data-crit-ed="${i}">
      <div class="cqa-row" style="margin-bottom:10px">
        <span class="cqa-cnum">${i + 1}</span>
        <input class="cqa-cname cqa-grow" data-c="${i}" data-k="name" value="${esc(c.name)}" placeholder="Название критерия">
        <div class="cqa-row" style="gap:2px">
          <button class="cqa-btn ghost sm" data-move="-1" data-i="${i}" title="Выше">${icon.up}</button>
          <button class="cqa-btn ghost sm" data-move="1" data-i="${i}" title="Ниже">${icon.down}</button>
          <button class="cqa-btn ghost sm" data-dup data-i="${i}" title="Дублировать">${icon.copy}</button>
          <button class="cqa-btn ghost sm danger" data-del data-i="${i}" title="Удалить">${icon.trash}</button>
        </div>
      </div>
      <label class="cqa-label">${c.type === "flag" ? "Какое нарушение ищем" : "Что должно быть в диалоге, чтобы поставить «Да»"}</label>
      <textarea class="cqa-textarea" style="min-height:58px" data-c="${i}" data-k="description" placeholder="${c.type === "flag" ? "Да — выявлено …" : "Да — оператор …"}">${esc(c.description || "")}</textarea>

      <div class="cqa-row" style="margin-top:12px;gap:18px;align-items:flex-end">
        <div><label class="cqa-label">Тип ответа</label>
          <div class="cqa-typeseg">${TYPES.map((t) => `<button class="${c.type === t.v ? "on" : ""}" data-type="${t.v}" data-i="${i}"><b>${t.t}</b><span>${t.d}</span></button>`).join("")}</div></div>
        ${c.type === "flag" ? `<span class="cqa-muted" style="font-size:12.5px;padding-bottom:10px">Не влияет на балл — кейс помечается как нарушение</span>` : `<div><label class="cqa-label">Вес</label>
          <div class="cqa-row" style="gap:8px"><input class="cqa-input" style="width:72px" type="number" min="1" data-c="${i}" data-k="weight" value="${c.weight}">
          <span class="cqa-muted" style="font-size:12.5px" data-pct="${i}">≈ ${pct}% итога</span></div></div>`}
      </div>

      ${c.type === "scale" ? `<div class="cqa-steps">
        <label class="cqa-label">Ступени шкалы — от лучшей к худшей</label>
        ${[...steps].sort((a, b) => b - a).map((v) => `<div class="cqa-step">
          <input class="cqa-input" type="number" style="width:70px" data-step-v="${v}" data-i="${i}" value="${v}">
          <input class="cqa-input cqa-grow" data-step-l="${v}" data-i="${i}" value="${esc(c.scale?.labels?.[String(v)] || "")}" placeholder="Когда ставится ${v}">
          ${steps.length > 2 ? `<button class="cqa-btn ghost sm" data-step-del="${v}" data-i="${i}">✕</button>` : ""}</div>`).join("")}
        <button class="cqa-btn sm" data-step-add data-i="${i}">${icon.plus} Ступень</button>
      </div>` : ""}

      ${c.type === "flag" ? "" : `<div style="margin-top:12px">
        <label class="cqa-switch"><input type="checkbox" data-c="${i}" data-k="na_allowed" ${c.na_allowed ? "checked" : ""}><i></i>Бывают диалоги, где критерий неприменим (N/A)</label>
        ${c.na_allowed ? `<input class="cqa-input" style="width:100%;margin-top:8px" data-c="${i}" data-k="na_rule" value="${esc(c.na_rule || "")}" placeholder="N/A — например: клиент ушёл до ответа оператора">` : ""}
      </div>`}

      <details class="cqa-cmore"><summary>Ещё</summary>
        <div class="cqa-row" style="gap:16px;margin-top:8px;align-items:flex-end">
          <div class="cqa-field"><label class="cqa-label">ID (для экспорта)</label><input class="cqa-input cqa-mono" style="width:200px" data-c="${i}" data-k="id" value="${esc(c.id)}"></div>
          ${(d.sections || []).length ? `<div class="cqa-field"><label class="cqa-label">Секция</label><select class="cqa-select" data-c="${i}" data-k="section_id">
            <option value="">—</option>${d.sections.map((s) => `<option value="${esc(s.id)}" ${s.id === c.section_id ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></div>` : ""}
          <label class="cqa-switch" style="padding-bottom:8px"><input type="checkbox" data-c="${i}" data-k="appealable" ${c.appealable !== false ? "checked" : ""}><i></i>Можно оспорить</label>
        </div></details>
    </div>`;
  }

  // ---------- состояние ----------
  const isDirty = () => isNew || JSON.stringify(strip(draft)) !== JSON.stringify(strip(base));
  function validate() {
    const p = [];
    if (!draft.name?.trim()) p.push("Укажите название чек-листа");
    if (!draft.criteria.length) p.push("Добавьте хотя бы один критерий");
    else if (!draft.criteria.some((c) => c.type !== "flag")) p.push("Нужен хотя бы один критерий с баллами (Да / Нет или шкала)");
    draft.criteria.forEach((c, i) => {
      if (!c.name?.trim()) p.push(`Критерий ${i + 1}: укажите название`);
      else if (!c.description?.trim()) p.push(`«${c.name}»: опишите, что проверять`);
      if (c.type !== "flag" && !(+c.weight > 0)) p.push(`«${c.name || i + 1}»: вес должен быть больше 0`);
    });
    const ids = draft.criteria.map((c) => c.id);
    if (new Set(ids).size !== ids.length) p.push("Повторяются ID критериев (см. «Ещё» в карточке критерия)");
    return p;
  }
  function changeSummary() {
    if (isNew) return `${draft.criteria.length} ${plural(draft.criteria.length, "критерий", "критерия", "критериев")}`;
    const prev = Object.fromEntries(base.criteria.map((c) => [c.id, JSON.stringify(c)]));
    const add = draft.criteria.filter((c) => !(c.id in prev)).length;
    const chg = draft.criteria.filter((c) => c.id in prev && prev[c.id] !== JSON.stringify(c)).length;
    const del = base.criteria.filter((c) => !draft.criteria.some((d) => d.id === c.id)).length;
    return [add && `+${add} добавлено`, chg && `${chg} изменено`, del && `${del} удалено`].filter(Boolean).join(" · ") || "общие настройки";
  }
  function refreshBar() {
    const bar = el.querySelector(".cqa-savebar");
    if (!bar) return;
    const problems = validate(), dirty = isDirty();
    bar.querySelector("[data-save]").disabled = !dirty || problems.length > 0;
    bar.querySelector("[data-reset]").disabled = !dirty;
    bar.querySelector("[data-status]").innerHTML = !dirty ? `<span class="cqa-muted">Изменений нет</span>`
      : problems.length ? `<span style="color:#c4174a">${esc(problems[0])}</span>`
      : `<b>Есть несохранённые изменения</b> <span class="cqa-muted">· ${esc(changeSummary())}</span>`;
  }
  const rerender = () => { renderSide(); renderEditor(); };
  const otherCrits = (c) => ({ criteria: draft.criteria.filter((x) => x !== c) });

  function newCriterion(partial = {}) {
    const c = { name: "", description: "", type: "binary", weight: 10, na_allowed: false, na_rule: "", appealable: true, ...partial };
    c.id = uniqueId(draft, partial.id || translit(c.name) || "criterion");
    c.autoId = true;
    if (c.type === "scale" && !c.scale) c.scale = { min: 0, max: 10, steps: [0, 5, 10], labels: {} };
    return c;
  }

  // ---------- события ----------
  el.addEventListener("input", (e) => {
    const t = e.target;
    if (t.dataset.g) setPath(draft, t.dataset.g, t.type === "number" || t.dataset.g === "rounding.digits" ? num(t.value) : t.value);
    if (t.dataset.s) draft.sections[+t.dataset.s].name = t.value;
    if (t.dataset.c) {
      const c = draft.criteria[+t.dataset.c], k = t.dataset.k;
      const v = t.type === "checkbox" ? t.checked : t.type === "number" ? num(t.value) : t.value;
      if (k === "name") {
        c.name = v;
        if (c.autoId) c.id = uniqueId(otherCrits(c), translit(v) || "criterion");  // ID следует за названием, пока его не правили руками
      } else if (k === "id") { c.id = v; delete c.autoId; }
      else setPath(c, k, v);
      if (k === "weight") {
        const sum = draft.criteria.filter((x) => x.type !== "flag").reduce((s, x) => s + (+x.weight || 0), 0);
        el.querySelectorAll("[data-pct]").forEach((s) => { const x = draft.criteria[+s.dataset.pct]; s.textContent = `≈ ${sum ? Math.round(((+x.weight || 0) / sum) * 100) : 0}% итога`; });
      }
      if (t.type === "checkbox" || t.tagName === "SELECT") return renderEditor();
    }
    if (t.dataset.stepL) { const c = draft.criteria[+t.dataset.i]; (c.scale.labels ||= {})[t.dataset.stepL] = t.value; }
    if (t.dataset.g === "name") renderSide();
    refreshBar();
  });
  el.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.stepV) {
      const c = draft.criteria[+t.dataset.i], old = +t.dataset.stepV, nv = num(t.value);
      if (nv == null || c.scale.steps.includes(nv)) return renderEditor();
      c.scale.steps = c.scale.steps.map((x) => (x === old ? nv : x)).sort((a, b) => a - b);
      if (c.scale.labels?.[String(old)] != null) { c.scale.labels[String(nv)] = c.scale.labels[String(old)]; delete c.scale.labels[String(old)]; }
      syncScale(c);
      return renderEditor();
    }
    if (t.tagName === "SELECT" && t.dataset.g) renderEditor();
  });
  el.addEventListener("toggle", (e) => { if (e.target.matches?.("[data-adv]")) advOpen = e.target.open; }, true);

  el.addEventListener("click", async (e) => {
    const t = e.target.closest("button,[data-v]");
    if (!t) return;
    const i = +t.dataset.i;
    if (t.dataset.v) {
      if (isDirty() && !confirm("Есть несохранённые изменения. Перейти без сохранения?")) return;
      const fam = t.dataset.fam;
      if (fam) openFamilies.has(fam) && t.classList.contains("on") ? openFamilies.delete(fam) : openFamilies.add(fam);
      base = all.find((v) => v.id === t.dataset.v);
      draft = clone(base);
      isNew = false;
      history.replaceState(null, "", `#/checklists/${base.id}`);
      return rerender();
    }
    if (t.dataset.type) {
      const c = draft.criteria[i];
      c.type = t.dataset.type;
      if (c.type === "scale" && !c.scale?.steps?.length) c.scale = { min: 0, max: 10, steps: [0, 5, 10], labels: {} };
      if (c.type === "flag") { c.weight = 0; c.na_allowed = false; } else if (!(+c.weight > 0)) c.weight = 10;
      return renderEditor();
    }
    if (t.hasAttribute("data-add")) {
      draft.criteria.push(newCriterion());
      renderEditor();
      return el.querySelector(`[data-crit-ed="${draft.criteria.length - 1}"] .cqa-cname`)?.focus();
    }
    if (t.hasAttribute("data-del")) { draft.criteria.splice(i, 1); return renderEditor(); }
    if (t.hasAttribute("data-dup")) {
      const c = clone(draft.criteria[i]);
      c.id = uniqueId(draft, `${c.id}_2`);
      c.name += " (копия)";
      draft.criteria.splice(i + 1, 0, c);
      return renderEditor();
    }
    if (t.dataset.move) {
      const j = i + +t.dataset.move;
      if (j < 0 || j >= draft.criteria.length) return;
      [draft.criteria[i], draft.criteria[j]] = [draft.criteria[j], draft.criteria[i]];
      return renderEditor();
    }
    if (t.hasAttribute("data-step-add")) {
      const c = draft.criteria[i], s = c.scale.steps;
      c.scale.steps = [...s, Math.max(...s) + (s[1] - s[0] || 1)].sort((a, b) => a - b);
      syncScale(c);
      return renderEditor();
    }
    if (t.dataset.stepDel != null) {
      const c = draft.criteria[i];
      c.scale.steps = c.scale.steps.filter((x) => x !== +t.dataset.stepDel);
      delete c.scale.labels?.[t.dataset.stepDel];
      syncScale(c);
      return renderEditor();
    }
    if (t.hasAttribute("data-add-sec")) { (draft.sections ||= []).push({ id: `sec_${Date.now().toString(36)}`, name: "Новая секция" }); return renderEditor(); }
    if (t.dataset.delSec != null) {
      const s = draft.sections.splice(+t.dataset.delSec, 1)[0];
      draft.criteria.forEach((c) => { if (c.section_id === s.id) c.section_id = ""; });
      return renderEditor();
    }
    if (t.hasAttribute("data-reset")) { draft = clone(base); return renderEditor(); }
    if (t.hasAttribute("data-activate")) {
      await ctx.api.activate(base.id);
      toast(`«${base.name}» v${base.version} теперь активен — по нему идёт оценка`);
      await ctx.refresh();
      all = await ctx.api.checklists();
      base = all.find((v) => v.id === base.id);
      draft = clone(base);
      return rerender();
    }
    if (t.hasAttribute("data-save")) return save();
  });

  async function save() {
    try {
      const body = { ...DEFAULTS, ...strip(draft) };
      body.criteria = body.criteria.map(({ autoId, ...c }) => c);
      if (!isNew) body.family_id = base.family_id;
      const saved = await ctx.api.saveChecklist(body);
      toast(isNew ? `Чек-лист «${saved.name}» создан и активен` : `Сохранена версия v${saved.version} — она активна`);
      isNew = false;
      await ctx.refresh();
      all = await ctx.api.checklists();
      base = all.find((v) => v.id === saved.id);
      draft = clone(base);
      openFamilies.add(base.family_id);
      history.replaceState(null, "", `#/checklists/${base.id}`);
      rerender();
      applyDialog(saved);
    } catch (e) { toast(e.message, true); }
  }

  top.querySelector("[data-new]").onclick = () => {
    if (isDirty() && !isNew && !confirm("Есть несохранённые изменения. Создать новый чек-лист без сохранения?")) return;
    const m = modal({
      title: "Новый чек-лист",
      body: `<div class="cqa-tpls">
        <button data-tpl="empty"><b>Пустой</b><span>начать с нуля и добавить критерии</span></button>
        <button data-tpl="greeting"><b>Только приветствие</b><span>один критерий «Да / Нет» — минимальный пример</span></button>
        ${base ? `<button data-tpl="copy"><b>Копия «${esc(base.name)}»</b><span>все ${base.criteria.length} критериев — поправить под себя</span></button>` : ""}
      </div>`,
      actions: [{ label: "Отмена" }],
    });
    m.querySelector(".cqa-tpls").onclick = (e) => {
      const b = e.target.closest("[data-tpl]");
      if (!b) return;
      const tpl = b.dataset.tpl;
      draft = tpl === "copy" ? { ...strip(clone(base)), name: `${base.name} (копия)` } : { ...DEFAULTS, ...TEMPLATES[tpl]() };
      isNew = true;
      m.remove();
      rerender();
      el.querySelector(".cqa-title-in")?.focus();
    };
  };

  async function applyDialog(saved) {
    const ds = await ctx.api.datasets();
    modal({
      title: `Оценить диалоги по «${saved.name}» v${saved.version}?`,
      body: `<p style="margin-top:0">Чек-лист уже активен. Запустите оценку, чтобы увидеть результаты. Прежние оценки останутся в истории карточек.</p>
        <div class="cqa-field"><label class="cqa-label">Какие диалоги</label><select class="cqa-select" data-ds>
          ${ds.map((d) => `<option value="${esc(d.id)}">${esc(d.id)} — ${d.count} диалогов</option>`).join("")}<option value="">Все наборы</option></select></div>`,
      actions: [{ label: "Позже" }, {
        label: "Запустить оценку", cls: "primary",
        onClick: async (m) => {
          const job = await ctx.api.run({ checklist_id: saved.id, dataset: m.querySelector("[data-ds]").value || null });
          ctx.app.activeJob = job.id;
          toast(`Запущена оценка ${job.total} диалогов`);
          ctx.nav("#/dialogs");
        },
      }],
    });
  }

  if (!base) { draft = { ...DEFAULTS, ...TEMPLATES.empty() }; isNew = true; }
  rerender();
}

function syncScale(c) {
  c.scale.min = Math.min(...c.scale.steps);
  c.scale.max = Math.max(...c.scale.steps);
}

const clone = (o) => JSON.parse(JSON.stringify(o));
const num = (v) => (v === "" || v == null ? null : Number(v));
const strip = ({ id, created_at, active, version, family_id, ...rest }) => rest;
const plural = (n, one, few, many) => (n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many);
const uniqueId = (d, base) => { let id = base, n = 2; while (d.criteria.some((c) => c.id === id)) id = `${base}_${n++}`; return id; };
const TR = { а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh", щ: "sch", ы: "y", э: "e", ю: "yu", я: "ya", ә: "a", ғ: "g", қ: "k", ң: "n", ө: "o", ұ: "u", ү: "u", һ: "h", і: "i" };
const translit = (s) => (s || "").toLowerCase().split("").map((ch) => TR[ch] ?? ch).join("").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 32);
function setPath(o, path, v) {
  const ks = path.split(".");
  let t = o;
  ks.slice(0, -1).forEach((k) => (t = t[k] ??= {}));
  t[ks.at(-1)] = v;
}
