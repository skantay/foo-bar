import { esc, icon } from "../ui.js";

export async function mount(el, ctx, [id], top) {
  ctx.setTitle(`Диалог ${id}`, "Транскрипт, оценка по критериям и апелляции");
  // порядок — как в последнем открытом списке (с его фильтрами), иначе все диалоги по ID
  const list = ctx.app.navList?.length ? ctx.app.navList : (await ctx.api.dialogs(ctx.checklist?.id)).map((d) => d.id).sort();
  const i = list.indexOf(id);
  const prev = i > 0 ? list[i - 1] : null;
  const next = i >= 0 && i < list.length - 1 ? list[i + 1] : null;
  const go = (d) => d && ctx.nav(`#/dialogs/${encodeURIComponent(d)}`);

  top.innerHTML = `<div class="cqa-nav-pn">
      <button class="cqa-btn" data-prev ${prev ? "" : "disabled"} title="Предыдущий диалог (←)">${icon.back}</button>
      <span class="cqa-muted" style="font-size:12.5px;min-width:64px;text-align:center">${i >= 0 ? `${i + 1} из ${list.length}` : ""}</span>
      <button class="cqa-btn" data-next ${next ? "" : "disabled"} title="Следующий диалог (→)">${icon.back.replace("<svg", '<svg style="transform:rotate(180deg)"')}</button>
    </div>
    <a class="cqa-btn" href="#/dialogs" title="Esc">К списку</a>`;
  top.querySelector("[data-prev]").onclick = () => go(prev);
  top.querySelector("[data-next]").onclick = () => go(next);

  el.innerHTML = `<cqa-case dialog-id="${esc(id)}"></cqa-case>
    <div class="cqa-faint" style="margin-top:14px;font-size:12px">Клавиши: <span class="cqa-kbd">←</span> <span class="cqa-kbd">→</span> соседние диалоги · <span class="cqa-kbd">Esc</span> к списку</div>`;

  const onKey = (e) => {
    if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (document.querySelector(".cqa-modal-bg")) return;
    if (e.key === "ArrowLeft") go(prev);
    if (e.key === "ArrowRight") go(next);
    if (e.key === "Escape") ctx.nav("#/dialogs");
  };
  const onChange = () => ctx.refresh();
  document.addEventListener("keydown", onKey);
  el.addEventListener("cqa-changed", onChange);
  return () => {
    document.removeEventListener("keydown", onKey);
    el.removeEventListener("cqa-changed", onChange);
  };
}
