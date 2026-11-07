import { download, esc, fmtScore, icon, toast } from "../ui.js";

// Сверка оценок ИИ с эталоном (Ground Truth): загруженным файлом или решениями проверяющего.
export async function mount(el, ctx, _params, top) {
  ctx.setTitle("Точность", "Сравнение оценок ИИ с эталонными оценками экспертов");
  top.innerHTML = `
    <button class="cqa-btn" data-exp="csv">${icon.down} Экспорт CSV</button>
    <button class="cqa-btn" data-exp="json">${icon.down} Экспорт JSON</button>
    <button class="cqa-btn primary" data-gt>${icon.upload} Загрузить эталон</button>
    <input type="file" accept=".json,.csv" hidden data-file>`;

  async function render() {
    const a = await ctx.api.accuracy();
    const cl = ctx.checklist;
    const crit = Object.entries(a.criteria).filter(([, v]) => v.n);
    const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
    el.innerHTML = `
      <div class="cqa-kpis">
        ${kpi("Совпадение по критериям", pct(a.exact_match), "доля ответов, совпавших с эталоном")}
        ${kpi("Средняя ошибка итога", a.total_mae == null ? "—" : `${fmtScore(a.total_mae)}<small> б.</small>`, "|ИИ − эталон| по итоговому баллу")}
        ${kpi("Диалогов в сверке", a.n_dialogs, `${a.dialogs.length} эталонов загружено`)}
      </div>
      ${!a.dialogs.length ? `<div class="cqa-alert info" style="margin-bottom:16px">${icon.alert}<div>
        Эталонов пока нет. Загрузите файл с оценками экспертов или откройте диалог, проверьте оценку и нажмите <b>«В эталон»</b>.<br>
        Формат JSON: <code class="cqa-mono">[{"conversation_id": "p1-ru-001", "criteria": {"contact": "Да", "consultation": 15, "closing": "N/A", ...}, "total": 87.5}]</code>.
        CSV: колонки <code class="cqa-mono">conversation_id, ${cl.criteria.map((c) => esc(c.id)).join(", ")}</code> и необязательная <code class="cqa-mono">total</code>.</div></div>` : ""}
      <div class="cqa-grid" style="grid-template-columns: 1fr 1.4fr">
        <div class="cqa-card"><div class="cqa-card-h"><h3>По критериям</h3></div><div class="cqa-card-b">
          ${crit.map(([id, v]) => `<div class="cqa-crit-row" style="grid-template-columns:1fr 160px 90px">
            <div><div style="font-weight:600">${esc(v.name)}</div><div class="cqa-faint" style="font-size:12px">${v.match}/${v.n} совпало · ошибка ${fmtScore(v.mae * 100)}% шкалы</div></div>
            <div class="cqa-progress"><i style="width:${(v.accuracy || 0) * 100}%;background:${v.accuracy >= 0.85 ? "var(--yes)" : v.accuracy >= 0.6 ? "var(--part)" : "var(--no)"}"></i></div>
            <b style="text-align:right">${pct(v.accuracy)}</b></div>`).join("") || `<div class="cqa-muted">Нет данных</div>`}
        </div></div>
        <div class="cqa-card"><div class="cqa-card-h"><h3>По диалогам</h3></div>
          <table class="cqa-table"><thead><tr><th>Диалог</th><th>Эталон</th><th>ИИ</th><th>Δ</th><th>Расхождения</th><th></th></tr></thead><tbody>
          ${a.dialogs.map((d) => `<tr data-id="${esc(d.conversation_id)}">
            <td><b class="cqa-mono">${esc(d.conversation_id)}</b></td>
            ${d.evaluated ? `<td>${fmtScore(d.gt_total)}</td><td>${fmtScore(d.ai_total)}</td>
              <td><b style="color:${d.abs_err > 10 ? "var(--no)" : d.abs_err > 3 ? "#a86a00" : "#00835f"}">${fmtScore(d.abs_err)}</b></td>
              <td>${d.mismatches.map((m) => `<span class="cqa-chip no" title="ИИ: ${esc(m.ai)} · эталон: ${esc(m.gt)}">${esc(m.criterion_id)}: ${esc(m.ai)} ≠ ${esc(m.gt)}</span>`).join(" ") || `<span class="cqa-chip yes">полное совпадение</span>`}</td>`
              : `<td colspan="4" class="cqa-muted">не оценён по активной версии чек-листа</td>`}
            <td data-stop><button class="cqa-btn ghost sm danger" data-del="${esc(d.conversation_id)}" title="Удалить эталон">${icon.trash}</button></td>
          </tr>`).join("") || `<tr><td colspan="6" class="cqa-empty">—</td></tr>`}
          </tbody></table></div>
      </div>`;
  }

  el.addEventListener("click", async (e) => {
    const del = e.target.closest("[data-del]");
    if (del) { await ctx.api.deleteGroundTruth(del.dataset.del); return render(); }
    if (e.target.closest("[data-stop]")) return;
    const tr = e.target.closest("tr[data-id]");
    if (tr) ctx.nav(`#/dialogs/${encodeURIComponent(tr.dataset.id)}`);
  });
  top.addEventListener("click", async (e) => {
    const exp = e.target.closest("[data-exp]")?.dataset.exp;
    if (exp === "csv") location.href = ctx.api.exportUrl("csv");
    if (exp === "json") download(`cqa_results_v${ctx.checklist.version}.json`, JSON.stringify(await ctx.api.exportJson(), null, 2));
    if (e.target.closest("[data-gt]")) top.querySelector("[data-file]").click();
  });
  top.querySelector("[data-file]").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";
    if (!f) return;
    try {
      const text = await f.text();
      const records = f.name.endsWith(".csv") ? parseCsv(text) : JSON.parse(text);
      const r = await ctx.api.saveGroundTruth(records);
      toast(`Загружено эталонов: ${r.saved}`);
      render();
    } catch (err) { toast(`Не удалось прочитать эталон: ${err.message}`, true); }
  });

  await render();
}

function parseCsv(text) {
  const lines = text.replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim());
  const sep = lines[0].includes(";") && !lines[0].includes(",") ? ";" : ",";
  const head = lines[0].split(sep).map((h) => h.trim());
  return lines.slice(1).map((l) => {
    const cells = l.split(sep).map((c) => c.trim());
    const row = Object.fromEntries(head.map((h, i) => [h, cells[i]]));
    const { conversation_id, total, ...criteria } = row;
    return { conversation_id, total: total ? Number(total.replace(",", ".")) : null, criteria };
  });
}

const kpi = (k, v, s) => `<div class="cqa-card cqa-kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
