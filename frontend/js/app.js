// <cqa-app api-base="..."> — корневой компонент админки: каркас, навигация, роутинг по hash.
import { Api } from "./api.js";
import { esc, icon, toast } from "./ui.js";
import "./components/cqa-case.js";
import * as dashboard from "./pages/dashboard.js";
import * as dialogs from "./pages/dialogs.js";
import * as appeals from "./pages/appeals.js";
import * as checklists from "./pages/checklists.js";
import * as casePage from "./pages/case.js";
import * as accuracyPage from "./pages/accuracy.js";

const ROUTES = [
  { re: /^#\/dialogs\/(.+)$/, page: casePage, nav: "dialogs" },
  { re: /^#\/dialogs\/?(?:\?.*)?$/, page: dialogs, nav: "dialogs" },
  { re: /^#\/appeals\/?(.*)$/, page: appeals, nav: "appeals" },
  { re: /^#\/checklists\/?(.*)$/, page: checklists, nav: "checklists" },
  { re: /^#\/accuracy\/?$/, page: accuracyPage, nav: "accuracy" },
  { re: /^#\/?$/, page: dashboard, nav: "dash" },
];

class CqaApp extends HTMLElement {
  connectedCallback() {
    this.api = new Api(this.getAttribute("api-base") || "");
    this.ctx = {
      api: this.api,
      app: this,
      checklist: null,
      health: null,
      nav: (h) => (location.hash = h),
      setTitle: (t, sub = "") => {
        this.querySelector(".cqa-top h1").textContent = t;
        this.querySelector(".cqa-top .cqa-sub").textContent = sub;
      },
      refresh: () => this.refreshMeta(),
    };
    this.innerHTML = `<div class="cqa-shell">
      <aside class="cqa-side">
        <div class="cqa-logo"><div class="cqa-logo-mark">CQ</div><div><b>Quality Analytics</b><small>Freedom Broker · QC</small></div></div>
        <nav class="cqa-nav">
          <a href="#/" data-nav="dash">${icon.dash} Обзор</a>
          <a href="#/dialogs" data-nav="dialogs">${icon.chat} Диалоги</a>
          <a href="#/appeals" data-nav="appeals">${icon.scale} Апелляции <span class="cqa-badge-n" data-appeals hidden>0</span></a>
          <a href="#/checklists" data-nav="checklists">${icon.list} Чек-листы</a>
          <a href="#/accuracy" data-nav="accuracy">${icon.check} Точность</a>
        </nav>
        <div style="margin-top:auto"></div>
        <div class="cqa-side-job" data-sidejob hidden></div>
        <div class="cqa-side-foot" data-foot style="margin-top:0">…</div>
      </aside>
      <main class="cqa-main">
        <header class="cqa-top"><div><h1></h1><div class="cqa-sub"></div></div><div class="cqa-top-right" data-top></div></header>
        <div class="cqa-page" data-page></div>
      </main>
    </div>`;
    this.pageEl = this.querySelector("[data-page]");
    window.addEventListener("hashchange", () => this.route());
    this.refreshMeta().then(() => this.route());
    this.querySelector("[data-sidejob]").onclick = () => this.ctx.nav("#/dialogs");
    this.pollJobs();
  }

  // Индикатор фоновой оценки виден на любой странице
  async pollJobs() {
    clearTimeout(this.jobTimer);
    try {
      const jobs = await this.api.jobs();
      const j = jobs.find((x) => x.status === "running");
      const box = this.querySelector("[data-sidejob]");
      box.hidden = !j;
      if (j) {
        this.activeJob = this.activeJob || j.id;
        box.innerHTML = `<div class="cqa-row" style="gap:8px"><span class="cqa-spin" style="width:12px;height:12px;border-color:rgba(255,255,255,.2);border-top-color:#3ddc97"></span>
          <b style="color:#fff">Идёт оценка</b><span style="margin-left:auto">${j.finished}/${j.total}</span></div>
          <div class="cqa-progress"><i style="width:${Math.round((j.finished / j.total) * 100)}%"></i></div>`;
      }
      if (this.hadJob && !j) this.refreshMeta();
      this.hadJob = !!j;
      this.jobTimer = setTimeout(() => this.pollJobs(), j ? 2000 : 6000);
    } catch {
      this.jobTimer = setTimeout(() => this.pollJobs(), 8000);
    }
  }

  async refreshMeta() {
    try {
      const [health, checklist, open] = await Promise.all([
        this.api.health(), this.api.activeChecklist(), this.api.appeals("open"),
      ]);
      this.ctx.health = health;
      this.ctx.checklist = checklist;
      const b = this.querySelector("[data-appeals]");
      b.textContent = open.length;
      b.hidden = !open.length;
      this.querySelector("[data-foot]").innerHTML = `
        <div>Модель: <b>${esc(health.model)}</b>${health.llm ? ` · effort ${esc(health.effort)} · ×${health.samples}` : ""}</div>
        <div style="margin-top:4px">Чек-лист: <b>${esc(checklist.name)}</b> · v${checklist.version}</div>
        ${health.llm ? "" : health.backend === "mock"
          ? `<div style="margin-top:6px;color:#ffd27a">Демо-режим: оценки ненастоящие (CQA_BACKEND=mock)</div>`
          : `<div style="margin-top:6px;color:#ffd27a">Модель не подключена: задайте ANTHROPIC_API_KEY или установите Claude Code</div>`}`;
    } catch (e) {
      toast(`API недоступен: ${e.message}`, true);
    }
  }

  async route() {
    const h = location.hash || "#/";
    const r = ROUTES.find((x) => x.re.test(h)) || ROUTES[ROUTES.length - 1];
    const params = h.match(r.re).slice(1).map(decodeURIComponent);
    this.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === r.nav));
    this.cleanup?.();
    this.cleanup = null;
    // Каждая страница получает свежие контейнеры: обработчики, которые она навесила,
    // уходят вместе со старыми узлами и не срабатывают на других страницах.
    const top = this.querySelector("[data-top]").cloneNode(false);
    this.querySelector("[data-top]").replaceWith(top);
    const page = this.pageEl.cloneNode(false);
    this.pageEl.replaceWith(page);
    this.pageEl = page;
    page.innerHTML = `<div class="cqa-card cqa-card-b"><div class="cqa-skel" style="width:40%"></div><div class="cqa-skel" style="width:70%;margin-top:10px"></div></div>`;
    this.querySelector(".cqa-main").scrollTop = 0;
    const token = (this.routeToken = (this.routeToken || 0) + 1);
    try {
      const cleanup = await r.page.mount(page, this.ctx, params, top);
      if (token !== this.routeToken) return cleanup?.();  // пока страница грузилась, пользователь ушёл дальше
      this.cleanup = cleanup;
    } catch (e) {
      this.pageEl.innerHTML = `<div class="cqa-alert crit">${icon.alert}<div>${esc(e.message)}</div></div>`;
    }
  }
}

customElements.define("cqa-app", CqaApp);
