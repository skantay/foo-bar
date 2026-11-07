// <cqa-case dialog-id="..." api-base="..."> — карточка кейса: транскрипт + оценка + апелляции.
// Самодостаточный компонент: его можно встроить отдельно на форму обращения в Dynamics 365.
import { Api } from "../api.js";
import { answerView, audioOffsets, esc, fmtOffset, fmtScore, fmtTs, icon, langTag, markOf, money, playerHtml, remember, remembered, scoreClass, speakerName, timeAgo, toast, wirePlayer } from "../ui.js";

const PII_LABEL = { PHONE: "телефон", IIN: "ИИН", CARD: "карта", IBAN: "IBAN", EMAIL: "email",
  "ИМЯ_ОПЕРАТОРА": "имя оператора", "ИМЯ_КЛИЕНТА": "имя клиента" };

class CqaCase extends HTMLElement {
  static get observedAttributes() { return ["dialog-id", "evaluation-id"]; }

  connectedCallback() {
    this.api = new Api(this.getAttribute("api-base") || this.closest("cqa-app")?.getAttribute("api-base") || "");
    this.load();
  }

  attributeChangedCallback(_n, a, b) { if (this.api && a !== b) this.load(); }

  disconnectedCallback() { clearTimeout(this.timer); }

  async load(evalId) {
    const id = this.getAttribute("dialog-id");
    if (!id) return;
    try {
      const [dialog, active] = await Promise.all([this.api.dialog(id), this.api.activeChecklist()]);
      this.dialog = dialog;
      this.active = active;
      const done = dialog.evaluations.filter((e) => e.status !== "running");
      const pick = evalId || this.getAttribute("evaluation-id")
        || (done.find((e) => e.checklist_id === active.id && e.status === "done") || done.find((e) => e.status === "done") || done[0])?.id;
      this.ev = pick ? await this.api.evaluation(pick) : null;
      this.render();
      const running = dialog.evaluations.find((e) => e.status === "running");
      if (running) this.waitFor();
    } catch (e) {
      this.innerHTML = `<div class="cqa-alert crit">${icon.alert}<div>${esc(e.message)}</div></div>`;
    }
  }

  // ---------- рендер ----------

  render() {
    const d = this.dialog;
    const ev = this.ev;
    this.innerHTML = `<div class="cqa-case">
      <div class="cqa-card cqa-transcript">
        <div class="cqa-card-h"><h3>Транскрипт</h3>${langTag(d.language)}
          <span class="cqa-chip">${d.channel === "voice call" ? "Звонок" : "Чат"}</span>
          <span class="cqa-faint" style="margin-left:auto">${d.transcript.length} реплик</span></div>
        ${d.audio_url ? playerHtml(`${this.api.base}/api/dialogs/${encodeURIComponent(d.id)}/audio`, d.audio_url) : ""}
        <div class="cqa-trbar"><input class="cqa-input" data-search placeholder="Поиск по транскрипту">
          <label class="cqa-switch" title="Показывать реплики бота и системные события"><input type="checkbox" data-ctx checked><i></i>Бот и система</label></div>
        <div class="cqa-card-b" data-tr>${this.renderTranscript()}</div>
      </div>
      <div class="cqa-grid" style="gap:14px">${ev ? this.renderEval() : this.renderEmpty()}</div>
    </div>`;
    this.bind();
  }

  renderTranscript() {
    const t = this.dialog.transcript;
    const offs = this.dialog.audio_url ? audioOffsets(t) : [];
    return t.map((x, i) => {
      const off = offs[i] != null ? fmtOffset(offs[i]) : "";
      return `<div class="cqa-turn ${esc(x.speaker)}" data-turn="${i}" id="cqa-turn-${i}">
        <div class="n">${i}</div>
        <div class="bubble"><div class="who">${speakerName[x.speaker] || esc(x.speaker)}<time title="${esc(x.timestamp)}" data-off>${esc(off || fmtTs(x.timestamp))}</time></div>
        <div class="txt${x.speaker === "bot" && x.text.length > 140 ? " clamp" : ""}" title="${x.speaker === "bot" && x.text.length > 140 ? "Нажмите, чтобы развернуть" : ""}">${esc(x.text)}</div></div></div>`;
    }).join("");
  }

  renderEmpty() {
    return `<div class="cqa-card cqa-card-b cqa-empty">
      <p style="margin-top:0">Диалог ещё не оценён по чек-листу «${esc(this.active.name)}», версия ${this.active.version}.</p>
      <button class="cqa-btn primary" data-run>${icon.play} Оценить сейчас</button></div>`;
  }

  renderEval() {
    const ev = this.ev;
    if (ev.status === "error") {
      return `<div class="cqa-alert crit">${icon.alert}<div><b>Оценка завершилась ошибкой</b><div>${esc(ev.error)}</div>
        <button class="cqa-btn sm" data-run style="margin-top:8px">${icon.sync} Повторить</button></div></div>`;
    }
    const cl = ev.checklist;
    const fr = ev.final_result;
    const ai = ev.ai_result;
    const others = this.dialog.evaluations.filter((e) => e.status === "done");
    const stale = cl.id !== this.active.id;
    const C = 2 * Math.PI * 64;
    const pct = (fr.total ?? 0) / 100;
    const color = { hi: "var(--yes)", mid: "var(--part)", lo: "var(--no)", none: "var(--line)" }[scoreClass(fr.total)];

    return `
      <div class="cqa-card">
        <div class="cqa-card-h">
          <h3>Результат оценки</h3>
          <select class="cqa-select" data-evsel style="height:28px;font-size:12px;margin-left:auto">
            ${others.map((e) => `<option value="${e.id}" ${e.id === ev.id ? "selected" : ""}>версия ${this.verOf(e)} · ${timeAgo(e.created_at)} · ${fmtScore(e.total)}</option>`).join("")}
          </select>
          <button class="cqa-btn sm" data-gt title="Сохранить финальный результат как эталон для замера точности">${icon.check} В эталон</button>
          <button class="cqa-btn sm" data-run title="Переоценить по активному чек-листу">${icon.sync} Переоценить</button>
        </div>
        <div class="cqa-card-b">
          ${stale ? `<div class="cqa-alert warn" style="margin-bottom:12px">${icon.alert}<div>Оценка сделана по версии ${cl.version} чек-листа. Активна версия ${this.active.version} — нажмите «Переоценить», чтобы применить новый чек-лист.</div></div>` : ""}
          <div class="cqa-summary">
            <div class="cqa-gauge">
              <svg width="150" height="150"><circle cx="75" cy="75" r="64" stroke="var(--line-2)" stroke-width="12" fill="none"/>
                <circle cx="75" cy="75" r="64" stroke="${color}" stroke-width="12" fill="none" stroke-linecap="round"
                  stroke-dasharray="${C * pct} ${C}" style="transition:stroke-dasharray .6s"/></svg>
              <div class="val"><div><b>${fmtScore(fr.total)}</b><span>из 100</span></div></div>
            </div>
            <div>
              <div class="cqa-row" style="margin-bottom:6px">
                ${fr.passed == null ? "" : fr.passed ? `<span class="cqa-chip yes">Пройден · порог ${cl.pass_threshold}</span>` : `<span class="cqa-chip no">Не пройден · порог ${cl.pass_threshold}</span>`}
              </div>
              <div style="font-size:13.5px">${esc(ev.summary)}</div>
            </div>
          </div>
          <div class="cqa-steps">
            <div><span>Оценка ИИ</span><b>${fmtScore(ai.total)}</b></div><i>→</i>
            <div><span>Решение проверяющего</span><b>${esc(this.decision())}</b></div><i>→</i>
            <div class="fin"><span>Финальный результат</span><b>${fmtScore(fr.total)}</b></div>
          </div>
          ${fr.critical_errors.length ? `<div class="cqa-alert crit" style="margin-top:12px">${icon.flag}<div><b>Грубая ошибка</b>${cl.critical_mode === "zero" ? " — итог обнулён" : " — отмечена отдельно, итог не обнуляется"}:
            ${fr.critical_errors.map((id) => {
              const name = this.crit(id).name, why = this.final(id).critical_reason || this.final(id).explanation || "";
              return `<div>· ${why.startsWith(name) ? "" : `${esc(name)}: `}${this.linkify(why)}</div>`;
            }).join("")}</div></div>` : ""}
          ${fr.sections.length ? `<div class="cqa-sections">${fr.sections.map((s) => `<div class="cqa-sec"><div class="t">${esc(s.name)}</div>
            <div class="p">${s.na ? "N/A" : fmtScore(s.score)}</div><div class="cqa-progress"><i style="width:${s.score ?? 0}%"></i></div></div>`).join("")}</div>` : ""}
          <table class="cqa-sc">${cl.criteria.map((c, i) => {
            const f = this.final(c.id) || {};
            const a = ev.answers[c.id] || {};
            const row = fr.criteria.find((r) => r.criterion_id === c.id) || {};
            const v = answerView(c, f);
            const open = (ev.appeals || []).some((x) => x.criterion_id === c.id && x.status === "open");
            return `<tr data-jump="${esc(c.id)}" class="s-${markOf(c, f)}"><td class="n">${i + 1}</td>
              <td>${esc(c.name)}${a.votes?.length > 1 && a.agreement < 1 ? `<span class="w" title="Прогоны разошлись: ${esc(a.votes.join(", "))}">${icon.alert.replace("<svg", '<svg width="11" height="11"')} ${Math.round(a.agreement * a.votes.length)}/${a.votes.length}</span>` : ""}
                ${ev.overrides?.[c.id] ? `<span class="cqa-chip na" style="height:18px;margin-left:6px">изменено</span>` : ""}${open ? `<span class="cqa-chip warn" style="height:18px;margin-left:6px">апелляция</span>` : ""}</td>
              <td class="a"><span class="cqa-chip ${v.cls}">${esc(v.label)}</span></td>
              <td class="p">${c.type === "flag" ? "—" : f.na ? "N/A" : `<b>${fmtScore(row.points)}</b> / ${fmtScore(row.effective_weight)}`}</td></tr>`;
          }).join("")}</table>
          ${ev.timing_facts?.length ? `<div class="cqa-alert info" style="margin-top:12px;padding:8px 12px"><div><b>Время ответа</b> (посчитано по меткам)
            ${ev.timing_facts.map((t) => `<div>${this.linkify(t)}</div>`).join("")}</div></div>` : ""}
          ${ev.pii?.enabled ? `<details style="margin-top:10px"><summary class="cqa-muted" style="cursor:pointer;font-size:12.5px">${icon.lock.replace("<svg", '<svg width="12" height="12" style="vertical-align:-1px"')} Персональные данные замаскированы перед отправкой в модель: ${
            Object.entries(ev.pii.counts || {}).map(([k, n]) => `${esc(PII_LABEL[k] || k)} × ${n}`).join(", ") || "не найдено"}</summary>
            ${ev.pii.masked_turns?.length ? `<div style="margin:6px 0 0;font-size:12.5px">Так эти реплики увидела модель:</div>
            <ul style="margin:4px 0 0;font-size:12.5px">${ev.pii.masked_turns.map((t) => `<li>[${t.turn}] ${esc(t.text)}</li>`).join("")}</ul>` : ""}</details>` : ""}
          ${ev.warnings?.length ? `<details style="margin-top:10px"><summary class="cqa-muted" style="cursor:pointer;font-size:12.5px">Предупреждения валидации (${ev.warnings.length})</summary>
            <ul style="margin:6px 0 0;font-size:12.5px">${ev.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
          <div class="cqa-faint" style="font-size:12px;margin-top:10px">Модель: ${esc(ev.model)} · стоимость оценки ${money(ev.cost_usd)} · ${timeAgo(ev.created_at)}</div>
        </div>
      </div>
      <div>${cl.criteria.map((c, i) => this.renderCrit(c, i)).join("")}</div>`;
  }

  verOf(e) {
    return e.checklist_id === this.ev?.checklist_id ? this.ev.checklist_version : e.checklist_id === this.active.id ? this.active.version : e.checklist_version ?? "?";
  }

  // Что решил проверяющий — среднее звено «Оценка ИИ → решение → финал» (ТЗ: «исходная оценка, решение и финальный результат»)
  decision() {
    const appeals = this.ev.appeals || [];
    const n = Object.keys(this.ev.overrides || {}).length;
    if (appeals.some((x) => x.status === "open")) return "апелляция на рассмотрении";
    if (n) return n === 1 ? "изменён 1 критерий" : `изменено критериев: ${n}`;
    if (appeals.length) return "оценка ИИ подтверждена";
    return "апелляций нет";
  }

  crit(id) { return this.ev.checklist.criteria.find((c) => c.id === id); }

  final(id) {
    const a = this.ev.answers[id];
    const ov = this.ev.overrides?.[id];
    return ov ? { ...a, value: ov.value, na: ov.na, critical_error: ov.critical_error } : a;
  }

  renderCrit(c, i) {
    const ev = this.ev;
    const a = ev.answers[c.id] || {};
    const f = this.final(c.id) || {};
    const ov = ev.overrides?.[c.id];
    const row = ev.final_result.criteria.find((r) => r.criterion_id === c.id) || {};
    const v = answerView(c, f);
    const sec = ev.checklist.sections?.find((s) => s.id === c.section_id);
    const appeals = (ev.appeals || []).filter((x) => x.criterion_id === c.id);
    const open = appeals.find((x) => x.status === "open");
    const evTurns = (a.evidence || []).map((e) => e.turn);
    const canAppeal = c.appealable !== false && !open && ev.status === "done";

    return `<div class="cqa-crit s-${markOf(c, f)}" data-crit="${esc(c.id)}" data-ev="${evTurns.join(",")}" data-range="${a.checked_range ? `${a.checked_range.from_turn}-${a.checked_range.to_turn}` : ""}">
      <div class="cqa-crit-h" data-focus>
        <div class="num">${i + 1}</div>
        <div style="min-width:0">
          <div class="ttl">${esc(c.name)}</div>
          <div class="meta">${sec ? `<span>${esc(sec.name)}</span>` : ""}${c.type === "flag" ? "" : `<span>вес ${c.weight}${row.effective_weight && row.effective_weight !== c.weight ? ` → ${row.effective_weight}` : ""}</span>`}
            <span>${c.type === "scale" ? `шкала ${(c.scale.steps?.length ? c.scale.steps : [c.scale.min, c.scale.max]).join(" / ")}` : c.type === "flag" ? "флаг · без баллов" : `да / нет${c.na_allowed ? " / N/A" : ""}`}</span>
            ${c.critical ? `<span style="color:var(--crit)">может быть грубой ошибкой</span>` : ""}</div>
        </div>
        <div class="res">
          <span class="cqa-chip ${v.cls}">${esc(v.label)}</span>
          <span class="cqa-pts">${c.type === "flag" ? "без баллов" : f.na ? "вес перераспределён" : `<b>${fmtScore(row.points)}</b> / ${fmtScore(row.effective_weight)}`}</span>
          ${a.votes?.length > 1 ? `<span class="cqa-faint" style="font-size:11px" title="Ответы независимых прогонов: ${esc(a.votes.join(", "))}">консенсус ${Math.round(a.agreement * a.votes.length)}/${a.votes.length}</span>` : ""}
        </div>
      </div>
      <div class="cqa-crit-b">
        ${f.critical_error ? `<div class="cqa-alert crit" style="margin-bottom:8px;padding:7px 10px">${icon.flag}<div>${esc(f.critical_reason || a.critical_reason || "Грубая ошибка")}</div></div>` : ""}
        ${v.hint ? `<p class="cqa-expl cqa-muted" style="font-size:12.5px">Ступень ${fmtScore(f.value)}: ${esc(v.hint)}</p>` : ""}
        ${a.na ? `<p class="cqa-expl"><span class="cqa-chip na">Причина N/A</span> ${this.linkify(a.na_reason || a.explanation)}</p>` : ""}
        ${a.explanation && !(a.na && a.na_reason === a.explanation) ? `<p class="cqa-expl">${this.linkify(a.explanation)}</p>` : ""}
        ${a.evidence?.length ? `<div class="cqa-evs">${a.evidence.map((e) => `<div class="cqa-ev" data-goto="${e.turn}">
            <button class="cqa-ref">[${e.turn}]</button><div class="cqa-grow"><span class="cqa-faint" style="font-size:11.5px">${speakerName[e.speaker] || ""} · ${esc(fmtTs(e.timestamp))}</span><br><q>${esc(e.quote)}</q>
            ${e.verified ? "" : `<span class="unver"> · цитата заменена текстом реплики</span>`}</div></div>`).join("")}</div>` : ""}
        ${a.checked_range && !a.na ? `<button class="cqa-btn ghost sm" data-range-btn>Проверенный участок: реплики [${a.checked_range.from_turn}]–[${a.checked_range.to_turn}]</button>` : ""}
        ${a.needs_review ? `<span class="cqa-chip warn">Низкая уверенность модели — стоит проверить</span>` : ""}
        ${ov ? `<div class="cqa-diff">${icon.scale.replace("<svg", '<svg width="16"')}<div>Исходная оценка ИИ: <b>${esc(answerView(c, a).label)}</b> → решение проверяющего: <b>${esc(v.label)}</b><br><span class="cqa-muted">${esc(ov.reason)}</span></div></div>` : ""}
        ${appeals.map((x) => `<div class="cqa-hist ${x.status}">
          <b>${x.status === "open" ? "Апелляция на рассмотрении" : x.decision === "confirm" ? "Апелляция отклонена — оценка подтверждена" : "Апелляция удовлетворена — оценка изменена"}</b>
          <span class="cqa-faint"> · ${esc(x.author || "Оператор")} · ${timeAgo(x.created_at)}</span>
          <div>«${esc(x.comment)}»</div>
          ${x.status === "resolved" ? `<div class="cqa-muted">${esc(x.reviewer || "Проверяющий")}: ${esc(x.reason)}${x.total_before != null ? ` · итог ${fmtScore(x.total_before)} → ${fmtScore(x.total_after)}` : ""}</div>`
            : `<a class="cqa-btn sm" style="margin-top:6px" href="#/appeals/${x.id}">Рассмотреть как проверяющий →</a>`}
        </div>`).join("")}
        ${canAppeal ? `<div data-appeal-slot><button class="cqa-btn sm" data-appeal>${icon.scale} Оспорить критерий</button></div>` : ""}
      </div>
    </div>`;
  }

  linkify(text) {
    return esc(text).replace(/\[(\d+)\]/g, (m, n) => `<button class="cqa-ref" data-goto="${n}">[${n}]</button>`);
  }

  // ---------- поведение ----------

  bind() {
    this.querySelector("[data-evsel]")?.addEventListener("change", (e) => this.load(e.target.value));
    const search = this.querySelector("[data-search]");
    const ctxToggle = this.querySelector("[data-ctx]");
    const applyFilter = () => {
      const q = search.value.trim().toLowerCase();
      this.querySelectorAll("[data-tr] .cqa-turn").forEach((t) => {
        const x = this.dialog.transcript[+t.dataset.turn];
        t.classList.toggle("hidden-role", !ctxToggle.checked && (x.speaker === "bot" || x.speaker === "system"));
        t.classList.toggle("nomatch", !!q && !x.text.toLowerCase().includes(q));
        const txt = t.querySelector(".txt");
        txt.innerHTML = q && x.text.toLowerCase().includes(q)
          // режем исходный текст по совпадениям и экранируем куски — иначе «t» попадает внутрь «&lt;»
          ? x.text.split(new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"))
              .map((part, i) => (i % 2 ? `<mark class="cqa-hit">${esc(part)}</mark>` : esc(part))).join("")
          : esc(x.text);
      });
    };
    wirePlayer(this.querySelector(".cqa-transcript"), this.dialog.transcript);
    search.addEventListener("input", applyFilter);
    ctxToggle.addEventListener("change", applyFilter);
    this.addEventListener("click", this.onClick ||= (e) => this.handleClick(e));
  }

  handleClick(e) {
    if (e.target.closest("[data-run]")) return this.rerun(e.target.closest("[data-run]"));
    const clamp = e.target.closest(".txt.clamp");
    if (clamp) return clamp.classList.remove("clamp");
    const jump = e.target.closest("[data-jump]");
    if (jump) {
      const card = this.querySelector(`[data-crit="${CSS.escape(jump.dataset.jump)}"]`);
      card.scrollIntoView({ behavior: "smooth", block: "start" });
      return card.querySelector("[data-focus]").click();
    }
    if (e.target.closest("[data-gt]")) {
      return this.api.gtFromEval(this.ev.id).then(() => toast("Финальный результат сохранён как эталон — см. «Точность»"), (err) => toast(err.message, true));
    }
    const go = e.target.closest("[data-goto]");
    if (go) return this.highlight([+go.dataset.goto], null, go.closest("[data-crit]"));
    if (e.target.closest("[data-range-btn]")) {
      const card = e.target.closest("[data-crit]");
      const [a, b] = card.dataset.range.split("-").map(Number);
      return this.highlight([], [a, b], card);
    }
    if (e.target.closest("[data-appeal]")) return this.appealForm(e.target.closest("[data-crit]"));
    const head = e.target.closest("[data-focus]");
    if (head) {
      const card = head.closest("[data-crit]");
      const turns = card.dataset.ev ? card.dataset.ev.split(",").map(Number) : [];
      const range = card.dataset.range ? card.dataset.range.split("-").map(Number) : null;
      this.highlight(turns, turns.length ? null : range, card);
    }
  }

  highlight(turns, range, card) {
    this.querySelectorAll(".cqa-crit.active").forEach((c) => c.classList.remove("active"));
    card?.classList.add("active");
    this.querySelectorAll(".cqa-turn").forEach((t) => {
      const i = +t.dataset.turn;
      t.classList.toggle("ev", turns.includes(i));
      t.classList.toggle("inrange", !!range && i >= range[0] && i <= range[1]);
      t.classList.remove("flash");
    });
    const first = turns.length ? turns[0] : range?.[0];
    const el = this.querySelector(`#cqa-turn-${first}`);
    if (!el) return;
    const box = this.querySelector("[data-tr]");
    box.scrollTop = el.offsetTop - box.offsetTop - 60;
    if (window.innerWidth < 1100) el.scrollIntoView({ behavior: "smooth", block: "center" });
    void el.offsetWidth;
    el.classList.add("flash");
  }

  appealForm(card) {
    const c = this.crit(card.dataset.crit);
    const slot = card.querySelector("[data-appeal-slot]");
    slot.innerHTML = `<div class="cqa-appeal-box">
      <label class="cqa-label">Оператор, подающий апелляцию</label>
      <input class="cqa-input" data-author placeholder="ФИО оператора" value="${esc(remembered("cqa-operator"))}" style="margin-bottom:8px">
      <label class="cqa-label">Почему вы не согласны с оценкой «${esc(c.name)}»?</label>
      <textarea class="cqa-textarea" data-cmt placeholder="Например: в реплике [5] оператор представился, модель это не учла"></textarea>
      <div class="cqa-row" style="margin-top:8px">
        <button class="cqa-btn primary sm" data-send>Подать апелляцию</button>
        <button class="cqa-btn ghost sm" data-cancel>Отмена</button>
      </div></div>`;
    slot.querySelector("[data-cmt]").focus();
    slot.querySelector("[data-cancel]").onclick = (e) => { e.stopPropagation(); this.render(); };
    slot.querySelector("[data-send]").onclick = async (e) => {
      e.stopPropagation();
      try {
        const author = slot.querySelector("[data-author]").value.trim();
        remember("cqa-operator", author);
        await this.api.appeal({ evaluation_id: this.ev.id, criterion_id: c.id, comment: slot.querySelector("[data-cmt]").value,
          author: author ? `Оператор ${author}` : "Оператор" });
        toast("Апелляция подана — она появилась в очереди проверяющего");
        this.dispatchEvent(new CustomEvent("cqa-changed", { bubbles: true }));
        this.load(this.ev.id);
      } catch (err) { toast(err.message, true); }
    };
  }

  async rerun(btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="cqa-spin"></span> Оцениваю…`;
    try {
      await this.api.run({ dialog_ids: [this.dialog.id], checklist_id: this.active.id });
      this.waitFor(true);
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
    }
  }

  async waitFor(fresh = false) {
    clearTimeout(this.timer);
    if (!this.isConnected) return;
    let d;
    try { d = await this.api.dialog(this.dialog.id); } catch (e) {
      this.timer = setTimeout(() => this.waitFor(fresh), 3000); // сеть мигнула — пробуем ещё
      return;
    }
    if (!this.isConnected) return;
    const running = d.evaluations.find((e) => e.status === "running");
    if (running || (fresh && d.evaluations[0]?.status === "running")) {
      this.querySelectorAll("[data-run]").forEach((b) => { b.disabled = true; b.innerHTML = `<span class="cqa-spin"></span> Оцениваю…`; });
      this.timer = setTimeout(() => this.waitFor(), 1500);
      return;
    }
    toast("Оценка готова");
    this.dispatchEvent(new CustomEvent("cqa-changed", { bubbles: true }));
    this.load(d.evaluations[0]?.id);
  }
}

customElements.define("cqa-case", CqaCase);
