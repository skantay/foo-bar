import { channelIcon, esc, icon, langTag, modal, money, scorePill, statusChip, toast } from "../ui.js";

const F = { q: "", lang: "", channel: "", status: "", dataset: "", crit: "", sort: "id", dir: 1 };
const MARK_TEXT = { yes: "выполнен", ok: "нет нарушений", part: "частично", no: "не выполнен", na: "N/A", crit: "грубая ошибка", none: "—" };

export async function mount(el, ctx, _params, top) {
  const qs = new URLSearchParams(location.hash.split("?")[1] || "");
  if (qs.has("crit")) F.crit = qs.get("crit");
  let rows = [];
  let datasets = [];
  const selected = new Set();
  let timer = null;

  ctx.setTitle("Диалоги", "Загрузка, выбор и запуск оценки");
  top.innerHTML = `
    <button class="cqa-btn" data-act="sync">${icon.sync} Из API организаторов</button>
    <button class="cqa-btn" data-act="upload">${icon.upload} Загрузить файл</button>
    <button class="cqa-btn primary" data-act="run">${icon.play} Оценить</button>
    <input type="file" accept=".json,.txt" hidden data-file multiple>`;

  el.innerHTML = `<div class="cqa-card">
    <div data-job></div>
    <div class="cqa-toolbar">
      <input class="cqa-input" style="width:260px" placeholder="Поиск по ID или тексту" data-f="q" value="${esc(F.q)}">
      <select class="cqa-select" data-f="dataset"></select>
      <select class="cqa-select" data-f="lang"><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select>
      <select class="cqa-select" data-f="channel"><option value="">Все каналы</option><option value="agent chat">Чаты</option><option value="voice call">Звонки</option></select>
      <select class="cqa-select" data-f="crit"><option value="">Любой критерий</option>
        ${(ctx.checklist?.criteria || []).map((c) => `<option value="${esc(c.id)}">${c.type === "flag" ? "Есть грубая ошибка" : `Провален: ${esc(c.name)}`}</option>`).join("")}</select>
      <select class="cqa-select" data-f="status"><option value="">Все статусы</option><option value="none">Не оценён</option><option value="passed">Пройден</option><option value="failed">Не пройден</option><option value="crit">Грубая ошибка</option><option value="disputed">Апелляция</option><option value="reviewed">Пересмотрен</option><option value="error">Ошибка</option></select>
      <span class="cqa-grow"></span>
      <span class="cqa-muted" data-count></span>
    </div>
    <div style="overflow:auto"><table class="cqa-table">
      <thead><tr>
        <th style="width:34px"><input type="checkbox" data-all></th>
        <th data-sort="id">Диалог</th><th>Язык</th><th>Канал</th><th>Обращение клиента</th>
        <th data-sort="turns">Реплик</th><th>Критерии</th><th data-sort="score">Балл</th><th>Статус</th><th></th>
      </tr></thead>
      <tbody data-body></tbody>
    </table></div>
  </div>`;

  const $ = (s) => el.querySelector(s);
  ["lang", "channel", "status", "crit"].forEach((k) => ($(`[data-f=${k}]`).value = F[k]));

  async function load() {
    [rows, datasets] = await Promise.all([ctx.api.dialogs(ctx.checklist?.id), ctx.api.datasets()]);
    $("[data-f=dataset]").innerHTML = `<option value="">Все наборы</option>` +
      datasets.map((d) => `<option value="${esc(d.id)}">${esc(d.id)} (${d.count})</option>`).join("");
    $("[data-f=dataset]").value = F.dataset;
    render();
  }

  function filtered() {
    const q = F.q.toLowerCase();
    let r = rows.filter((d) => {
      const ev = d.evaluation;
      if (F.lang && d.language !== F.lang) return false;
      if (F.channel && d.channel !== F.channel) return false;
      if (F.dataset && d.dataset !== F.dataset) return false;
      if (q && !d.id.toLowerCase().includes(q) && !d.preview.toLowerCase().includes(q)) return false;
      if (F.crit && !["no", "part", "crit"].includes(ev?.marks?.find((m) => m.id === F.crit)?.m)) return false;
      if (F.status === "none") return !ev;
      if (F.status === "error") return ev?.status === "error";
      if (F.status === "crit") return ev?.critical_errors?.length;
      if (F.status) return ev?.case_status === F.status;
      return true;
    });
    const key = { id: (d) => d.id, turns: (d) => d.turns, score: (d) => d.evaluation?.total ?? -1 }[F.sort];
    return r.sort((a, b) => (key(a) > key(b) ? 1 : key(a) < key(b) ? -1 : 0) * F.dir);
  }

  function render() {
    const list = filtered();
    ctx.app.navList = list.map((d) => d.id);
    $("[data-count]").textContent = `${list.length} из ${rows.length}`;
    el.querySelectorAll("th[data-sort]").forEach((th) => {
      th.innerHTML = th.textContent.replace(/[↑↓]/g, "").trim() + (th.dataset.sort === F.sort ? (F.dir > 0 ? " ↑" : " ↓") : "");
    });
    $("[data-body]").innerHTML = list.map((d) => {
      const ev = d.evaluation;
      const cls = ev?.critical_errors?.length ? "crit" : ev?.case_status === "failed" ? "fail" : "";
      return `<tr data-id="${esc(d.id)}" class="${cls}">
        <td data-stop><input type="checkbox" data-sel="${esc(d.id)}" ${selected.has(d.id) ? "checked" : ""}></td>
        <td><b class="cqa-mono">${esc(d.id)}</b><div class="cqa-faint" style="font-size:11.5px">${esc(d.dataset)}</div></td>
        <td>${langTag(d.language)}</td>
        <td><span class="cqa-row" style="gap:6px;color:var(--muted)"><span style="width:16px;display:inline-flex">${channelIcon(d.channel)}</span>${d.channel === "voice call" ? "Звонок" : "Чат"}</span></td>
        <td><div class="cqa-preview">${esc(d.preview)}</div></td>
        <td class="cqa-muted">${d.turns}<span class="cqa-faint"> · оп. ${d.operator_turns}</span></td>
        <td><span class="cqa-marks">${(ev?.marks || []).map((m) => `<i class="cqa-dot m-${m.m}" title="${esc(m.name)}: ${esc(m.label ?? MARK_TEXT[m.m])}"></i>`).join("")}</span></td>
        <td>${scorePill(ev?.total)}${ev && ev.ai_total !== ev.total && ev.total != null ? `<div class="cqa-faint" style="font-size:11px">ИИ: ${ev.ai_total}</div>` : ""}</td>
        <td>${statusChip(ev)} ${ev?.critical_errors?.length ? `<span class="cqa-chip crit" title="Грубая ошибка">${icon.flag.replace("<svg", '<svg width="11" height="11"')}</span>` : ""}</td>
        <td style="color:var(--faint)">›</td>
      </tr>`;
    }).join("") || `<tr><td colspan="10" class="cqa-empty">Нет диалогов. Загрузите набор из API организаторов.</td></tr>`;
    $("[data-all]").checked = list.length > 0 && list.every((d) => selected.has(d.id));
    top.querySelector("[data-act=run]").innerHTML = `${icon.play} Оценить${selected.size ? ` (${selected.size})` : ""}`;
  }

  el.addEventListener("input", (e) => {
    const f = e.target.dataset.f;
    if (f) { F[f] = e.target.value; render(); }
  });
  el.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (th) {
      F.dir = F.sort === th.dataset.sort ? -F.dir : 1;
      F.sort = th.dataset.sort;
      return render();
    }
    if (e.target.matches("[data-all]")) {
      filtered().forEach((d) => (e.target.checked ? selected.add(d.id) : selected.delete(d.id)));
      return render();
    }
    if (e.target.matches("[data-sel]")) {
      e.target.checked ? selected.add(e.target.dataset.sel) : selected.delete(e.target.dataset.sel);
      return render();
    }
    if (e.target.closest("[data-stop]")) return;
    const tr = e.target.closest("tr[data-id]");
    if (tr) ctx.nav(`#/dialogs/${encodeURIComponent(tr.dataset.id)}`);
  });

  top.addEventListener("click", (e) => {
    const a = e.target.closest("[data-act]")?.dataset.act;
    if (a === "sync") syncDialog();
    if (a === "upload") top.querySelector("[data-file]").click();
    if (a === "run") runDialog();
  });
  top.querySelector("[data-file]").addEventListener("change", async (e) => {
    let n = 0;
    for (const f of e.target.files) {
      try { n += (await ctx.api.upload(f, "UPLOAD")).imported; } catch (err) { toast(`${f.name}: ${err.message}`, true); }
    }
    e.target.value = "";
    if (n) { toast(`Загружено диалогов: ${n}`); F.dataset = "UPLOAD"; load(); }
  });

  function syncDialog() {
    modal({
      title: "Загрузка из API организаторов",
      body: `<div class="cqa-field"><label class="cqa-label">group_id</label><input class="cqa-input" data-g value="DEV"></div>
        <p class="cqa-muted" style="margin:10px 0 0">Для контрольного набора организаторы сообщат другой group_id. Повторная загрузка обновляет диалоги, не создавая дублей.</p>`,
      actions: [{ label: "Отмена" }, {
        label: "Загрузить", cls: "primary",
        onClick: async (m) => {
          const g = m.querySelector("[data-g]").value.trim();
          const r = await ctx.api.sync(g);
          toast(`Набор ${r.dataset}: загружено ${r.imported} диалогов`);
          F.dataset = r.dataset;
          await load();
        },
      }],
    });
  }

  function runDialog() {
    const cl = ctx.checklist;
    const missing = rows.filter((d) => (!F.dataset || d.dataset === F.dataset) && d.evaluation?.status !== "done").length;
    const scope = F.dataset ? `набора ${F.dataset}` : "всех наборов";
    modal({
      title: "Запуск оценки",
      body: `<p style="margin-top:0">Чек-лист: <b>${esc(cl.name)}</b> · версия ${cl.version} · ${cl.criteria.length} критериев</p>
        <div class="cqa-field" style="gap:8px">
          ${selected.size ? `<label class="cqa-radio"><input type="radio" name="sc" value="sel" checked><span>Выбранные диалоги (${selected.size})</span></label>` : ""}
          <label class="cqa-radio"><input type="radio" name="sc" value="missing" ${selected.size ? "" : "checked"}><span>Только неоценённые по этой версии ${scope} (${missing})</span></label>
          <label class="cqa-radio"><input type="radio" name="sc" value="all"><span>Все диалоги ${scope} — переоценить заново</span></label>
        </div>
        <p class="cqa-muted" style="margin-bottom:0">Модель: <b>${esc(ctx.health?.model)}</b>. Оценки сохраняются с привязкой к версии чек-листа.</p>`,
      actions: [{ label: "Отмена" }, {
        label: "Запустить", cls: "primary",
        onClick: async (m) => {
          const sc = m.querySelector("input[name=sc]:checked").value;
          const body = { checklist_id: cl.id, dataset: F.dataset || null };
          if (sc === "sel") body.dialog_ids = [...selected];
          if (sc === "missing") body.only_missing = true;
          const job = await ctx.api.run(body);
          ctx.app.activeJob = job.id;
          selected.clear();
          toast(`Запущена оценка ${job.total} диалогов`);
          poll();
        },
      }],
    });
  }

  async function poll() {
    clearTimeout(timer);
    const id = ctx.app.activeJob;
    if (!id) return;
    const j = await ctx.api.job(id).catch(() => null);
    if (!j) return;
    const pct = Math.round((j.finished / j.total) * 100);
    $("[data-job]").innerHTML = `<div class="cqa-job">
      ${j.status === "running" ? `<span class="cqa-spin"></span>` : icon.check.replace("<svg", '<svg width="16"')}
      <b>${j.status === "running" ? "Идёт оценка" : "Оценка завершена"}</b>
      <span>${j.finished} / ${j.total}${j.failed.length ? ` · ошибок ${j.failed.length}` : ""}</span>
      <div class="cqa-progress"><i style="width:${pct}%"></i></div>
      <span class="cqa-muted">v${j.checklist_version} · ${esc(j.model)} · ${money(j.cost_usd)}</span>
      ${j.status !== "running" ? `<button class="cqa-btn ghost sm" data-close-job style="margin-left:auto">Скрыть</button>` : ""}
    </div>`;
    $("[data-close-job]")?.addEventListener("click", () => { ctx.app.activeJob = null; $("[data-job]").innerHTML = ""; });
    await load();
    if (j.status === "running") timer = setTimeout(poll, 1500);
    else ctx.refresh();
  }

  await load();
  poll();
  return () => clearTimeout(timer);
}
