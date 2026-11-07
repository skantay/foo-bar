import { answerView, esc, fmtScore, fmtTs, icon, speakerName, timeAgo, toast, valueOptions } from "../ui.js";

export async function mount(el, ctx, [selId], top) {
  let tab = "open";
  let list = [];
  ctx.setTitle("Апелляции", "Очередь проверяющего: спорная оценка, комментарий и фрагмент диалога");
  top.innerHTML = `<div class="cqa-tabs" data-tabs><button data-t="open" class="on">На рассмотрении</button><button data-t="resolved">Рассмотренные</button><button data-t="">Все</button></div>`;
  el.innerHTML = `<div class="cqa-appeals">
    <div class="cqa-card" style="overflow:hidden"><div data-list></div></div>
    <div data-panel></div></div>`;
  const $ = (s) => el.querySelector(s);

  async function loadList() {
    list = await ctx.api.appeals(tab || undefined);
    if (selId && !list.some((a) => a.id === selId)) {
      const all = await ctx.api.appeals();
      const hit = all.find((a) => a.id === selId);
      if (hit) { tab = hit.status; list = all.filter((a) => a.status === tab); markTab(); }
    }
    $("[data-list]").innerHTML = list.map((a) => `<div class="cqa-ap-item ${a.id === selId ? "on" : ""}" data-id="${a.id}">
        <div class="cqa-row"><span class="t">${esc(a.criterion_name)}</span>
          <span class="cqa-chip ${a.status === "open" ? "warn" : a.decision === "change" ? "yes" : "na"}" style="margin-left:auto">${a.status === "open" ? "Открыта" : a.decision === "change" ? "Изменена" : "Подтверждена"}</span></div>
        <div class="c">«${esc(a.comment)}»</div>
        <div class="cqa-faint" style="font-size:11.5px;margin-top:4px"><span class="cqa-mono">${esc(a.dialog_id)}</span> · итог ${fmtScore(a.evaluation_total)} · ${timeAgo(a.created_at)}</div>
      </div>`).join("") || `<div class="cqa-empty">Апелляций нет.<br><span class="cqa-faint">Подать апелляцию можно в карточке диалога — кнопка «Оспорить критерий».</span></div>`;
    if (!selId && list[0]) selId = list[0].id;
    if (selId && list.some((a) => a.id === selId)) {
      el.querySelectorAll(".cqa-ap-item").forEach((x) => x.classList.toggle("on", x.dataset.id === selId));
      await panel(list.find((a) => a.id === selId));
    } else $("[data-panel]").innerHTML = "";
  }

  function markTab() { top.querySelectorAll("[data-t]").forEach((b) => b.classList.toggle("on", b.dataset.t === tab)); }

  async function panel(a) {
    const [ev, dialog] = await Promise.all([ctx.api.evaluation(a.evaluation_id), ctx.api.dialog(a.dialog_id)]);
    const c = ev.checklist.criteria.find((x) => x.id === a.criterion_id);
    const ans = ev.answers[c.id];
    const disputed = { value: a.disputed_value, na: a.disputed_na };
    const t = dialog.transcript;
    const ids = new Set(ans.evidence.map((e) => e.turn));
    let lo, hi;
    if (ids.size) { lo = Math.max(0, Math.min(...ids) - 2); hi = Math.min(t.length - 1, Math.max(...ids) + 2); }
    else if (ans.checked_range) { lo = ans.checked_range.from_turn; hi = ans.checked_range.to_turn; }
    else { lo = 0; hi = t.length - 1; }

    const state = { decision: "confirm", value: a.disputed_value, na: a.disputed_na, critical: !!ans.critical_error };
    const resolved = a.status === "resolved";

    $("[data-panel]").innerHTML = `<div class="cqa-card">
      <div class="cqa-card-h"><h3>${esc(c.name)}</h3><span class="cqa-faint cqa-mono">${esc(a.dialog_id)}</span>
        <a class="cqa-btn sm" style="margin-left:auto" href="#/dialogs/${encodeURIComponent(a.dialog_id)}">${icon.ext} Карточка диалога</a></div>
      <div class="cqa-card-b cqa-grid" style="gap:14px">
        <div class="cqa-muted" style="font-size:13px">${esc(c.description)}${c.na_allowed ? `<br><b>N/A:</b> ${esc(c.na_rule)}` : ""}${c.critical ? `<br><b style="color:var(--crit)">Грубая ошибка:</b> ${esc(c.critical_rule)}` : ""}</div>
        <div class="cqa-compare">
          <div class="box"><div class="k">Оспариваемая оценка</div><div class="v">${esc(answerView(c, disputed).label)}</div></div>
          <div style="font-size:20px;color:var(--faint)">→</div>
          <div class="box" style="${resolved ? "border-color:var(--accent)" : "border-style:dashed"}"><div class="k">${resolved ? "Решение проверяющего" : "Итог кейса сейчас"}</div>
            <div class="v">${resolved ? esc(answerView(c, { value: a.new_value, na: a.new_na }).label) : fmtScore(ev.final_result.total)}</div></div>
        </div>
        <div><label class="cqa-label">Комментарий оператора · ${esc(a.author)} · ${timeAgo(a.created_at)}</label>
          <div class="cqa-alert warn">«${esc(a.comment)}»</div></div>
        <div><label class="cqa-label">Обоснование модели</label>
          <div>${esc(ans.na ? ans.na_reason || ans.explanation : ans.explanation)}</div>
          ${ans.evidence.length ? `<div class="cqa-faint" style="font-size:12px;margin-top:4px">Доказательства: ${ans.evidence.map((e) => `[${e.turn}]`).join(", ")}</div>` : ""}</div>
        <div><label class="cqa-label">Фрагмент диалога · реплики ${lo}–${hi}</label>
          <div class="cqa-snippet">${t.slice(lo, hi + 1).map((x, k) => {
            const i = lo + k;
            return `<div class="cqa-turn ${esc(x.speaker)} ${ids.has(i) ? "ev" : ""}"><div class="n">${i}</div><div class="bubble">
              <div class="who">${speakerName[x.speaker] || esc(x.speaker)}<time>${esc(fmtTs(x.timestamp))}</time></div><div class="txt">${esc(x.text)}</div></div></div>`;
          }).join("")}</div></div>
        ${resolved ? `<div class="cqa-alert info">${icon.check}<div><b>${a.decision === "change" ? "Оценка изменена" : "Оценка подтверждена"}</b> · ${esc(a.reviewer)} · ${timeAgo(a.resolved_at)}
          <div>${esc(a.reason)}</div><div style="margin-top:4px">Итог кейса: <b>${fmtScore(a.total_before)} → ${fmtScore(a.total_after)}</b></div></div></div>`
        : `<div class="cqa-card" style="box-shadow:none;background:var(--surface-2)"><div class="cqa-card-b cqa-grid" style="gap:12px">
            <div class="cqa-row"><b>Решение</b><div class="cqa-seg" data-dec>
              <button data-d="confirm" class="on">Подтвердить оценку</button><button data-d="change">Изменить оценку</button></div></div>
            <div data-change hidden class="cqa-grid" style="gap:10px">
              <div class="cqa-row"><span class="cqa-label" style="margin:0;width:110px">Новое значение</span><div class="cqa-seg" data-val>${valueButtons(c)}</div></div>
              ${c.critical ? `<label class="cqa-switch"><input type="checkbox" data-crit ${state.critical ? "checked" : ""}><i></i>Грубая ошибка</label>` : ""}
            </div>
            <div><label class="cqa-label">Причина решения (обязательно)</label><textarea class="cqa-textarea" data-reason placeholder="Например: оператор представился в реплике [9], критерий выполнен"></textarea></div>
            <div class="cqa-row"><button class="cqa-btn primary" data-save>${icon.check} Сохранить финальный результат</button></div>
          </div></div>`}
      </div></div>`;

    if (resolved) return;
    const p = $("[data-panel]");
    const markVal = () => p.querySelectorAll("[data-v]").forEach((b) => {
      const on = b.dataset.v === "na" ? state.na : !state.na && Number(b.dataset.v) === state.value;
      b.classList.toggle("on", on);
    });
    markVal();
    p.querySelector("[data-dec]").onclick = (e) => {
      const b = e.target.closest("[data-d]");
      if (!b) return;
      state.decision = b.dataset.d;
      p.querySelectorAll("[data-d]").forEach((x) => x.classList.toggle("on", x === b));
      p.querySelector("[data-change]").hidden = state.decision !== "change";
    };
    p.querySelector("[data-val]").onclick = (e) => {
      const b = e.target.closest("[data-v]");
      if (!b) return;
      if (b.dataset.v === "na") state.na = true;
      else { state.na = false; state.value = Number(b.dataset.v); }
      markVal();
    };
    p.querySelector("[data-save]").onclick = async (e) => {
      e.target.disabled = true;
      try {
        const body = { decision: state.decision, reason: p.querySelector("[data-reason]").value, reviewer: "Проверяющий QC" };
        if (state.decision === "change") Object.assign(body, { value: state.value, na: state.na, critical_error: !!p.querySelector("[data-crit]")?.checked });
        const r = await ctx.api.decide(a.id, body);
        toast(`Решение сохранено. Итог: ${fmtScore(r.total_before)} → ${fmtScore(r.total_after)}`);
        ctx.refresh();
        await loadList();
      } catch (err) {
        toast(err.message, true);
        e.target.disabled = false;
      }
    };
  }

  top.querySelector("[data-tabs]").onclick = (e) => {
    const b = e.target.closest("[data-t]");
    if (!b) return;
    tab = b.dataset.t;
    selId = null;
    markTab();
    loadList();
  };
  el.querySelector("[data-list]").onclick = (e) => {
    const it = e.target.closest("[data-id]");
    if (!it) return;
    selId = it.dataset.id;
    history.replaceState(null, "", `#/appeals/${selId}`);
    el.querySelectorAll(".cqa-ap-item").forEach((x) => x.classList.toggle("on", x === it));
    panel(list.find((a) => a.id === selId));
  };

  await loadList();
}

function valueButtons(c) {
  const out = valueOptions(c).map((o) => `<button data-v="${o.v}" class="${o.cls}" title="${esc(o.title || "")}">${esc(o.label)}</button>`);
  if (c.na_allowed) out.push(`<button data-v="na" class="na">N/A</button>`);
  return out.join("");
}
