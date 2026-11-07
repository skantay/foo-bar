import { esc, fmtScore, icon, langTag, scoreClass } from "../ui.js";

export async function mount(el, ctx, _params, top) {
  const s = await ctx.api.stats(ctx.checklist?.id);
  const date = s.checklist.created_at ? new Date(s.checklist.created_at * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) : "";
  ctx.setTitle("Обзор качества", `${s.checklist.name}, версия ${s.checklist.version}${date ? ` от ${date}` : ""}`);
  top.innerHTML = "";

  const passRate = s.evaluated && s.passed != null ? Math.round((s.passed / s.evaluated) * 100) : null;
  // худшие критерии — сверху; без данных (все N/A) — в конце
  const maxBar = Math.max(1, ...s.distribution);
  const crit = Object.entries(s.criteria).sort(([, a], [, b]) => (a.avg_ratio ?? 2) - (b.avg_ratio ?? 2));

  el.innerHTML = `
    ${!s.evaluated ? `<div class="cqa-alert info" style="margin-bottom:16px">${icon.alert}<div>Пока нет оценок по этой версии чек-листа. Откройте <a href="#/dialogs">Диалоги</a> и запустите оценку.</div></div>` : ""}
    <div class="cqa-kpis">
      ${kpi("Средний балл", s.avg_total == null ? "—" : `${fmtScore(s.avg_total)}<small>/100</small>`, `${s.evaluated} из ${s.dialogs} диалогов оценено`, scoreClass(s.avg_total))}
      ${s.passed == null ? "" : kpi("Прошли порог", passRate == null ? "—" : `${passRate}<small>%</small>`, `${s.passed} кейсов`)}
      ${kpi("Грубые ошибки", s.critical, "кейсов с критичным нарушением", s.critical ? "lo" : "")}
      ${kpi("Открытые апелляции", s.open_appeals, `<a href="#/appeals">к очереди проверяющего →</a>`)}
    </div>
    <div class="cqa-card">
      <div class="cqa-card-h"><h3>Проблемные критерии</h3><span class="cqa-faint" style="font-size:12.5px">сначала худшие · нажмите, чтобы открыть диалоги</span>
        <div class="cqa-legend" style="margin-left:auto">
          <span><i style="background:var(--yes)"></i>Выполнено</span><span><i style="background:var(--part)"></i>Частично</span>
          <span><i style="background:var(--no)"></i>Нет</span><span><i style="background:var(--na)"></i>N/A</span></div></div>
      <div class="cqa-card-b">
        ${crit.map(([id, c]) => {
          const tot = c.full + c.partial + c.zero + c.na || 1;
          const pct = (n) => (n / tot) * 100;
          return `<div class="cqa-crit-row link" data-crit="${esc(id)}" title="Показать диалоги, где критерий не выполнен">
            <div style="font-weight:600">${esc(c.name)}</div>
            <div class="cqa-stack" title="Выполнено ${c.full} · частично ${c.partial} · нет ${c.zero} · N/A ${c.na}">
              <i style="width:${pct(c.full)}%;background:var(--yes)"></i><i style="width:${pct(c.partial)}%;background:var(--part)"></i>
              <i style="width:${pct(c.zero)}%;background:var(--no)"></i><i style="width:${pct(c.na)}%;background:var(--na)"></i></div>
            <div style="text-align:right;font-weight:700">${c.avg_ratio == null ? "—" : Math.round(c.avg_ratio * 100) + "%"}</div>
          </div>`;
        }).join("") || `<div class="cqa-muted">Нет данных</div>`}
      </div>
    </div>
    <div class="cqa-card" style="margin-top:16px">
      <div class="cqa-card-h"><h3>Распределение баллов</h3></div>
      <div class="cqa-card-b"><div class="cqa-bars">
        ${s.distribution.map((n, i) => `<div class="b"><em>${n || ""}</em><i style="height:${(n / maxBar) * 100}%;background:${i >= 8 ? "var(--yes)" : i >= 6 ? "var(--part)" : "var(--no)"}"></i><span>${i * 10}</span></div>`).join("")}
      </div></div>
    </div>
    <div class="cqa-card" style="margin-top:16px">
      <div class="cqa-card-h"><h3>По языкам</h3></div>
      <div class="cqa-card-b">
        ${Object.entries(s.by_language).map(([l, v]) => `<div class="cqa-row" style="padding:6px 0">${langTag(l)}<div class="cqa-grow cqa-progress"><i style="width:${v}%"></i></div><b style="width:40px;text-align:right">${fmtScore(v)}</b></div>`).join("") || `<div class="cqa-muted">Нет данных</div>`}
      </div>
    </div>`;
  el.onclick = (e) => {
    const r = e.target.closest("[data-crit]");
    if (r) ctx.nav(`#/dialogs?crit=${encodeURIComponent(r.dataset.crit)}`);
  };
}

const kpi = (k, v, s, cls = "") => `<div class="cqa-card cqa-kpi"><div class="k">${k}</div>
  <div class="v" style="${cls === "hi" ? "color:#00835f" : cls === "lo" ? "color:#c4174a" : cls === "mid" ? "color:#a86a00" : ""}">${v}</div><div class="s">${s}</div></div>`;
