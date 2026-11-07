// Тонкий клиент к API. base берётся из атрибута api-base у <cqa-app>/<cqa-case>,
// поэтому в Dynamics 365 достаточно указать адрес сервиса оценки.
export class Api {
  constructor(base = "") {
    this.base = base.replace(/\/$/, "");
  }

  async req(method, path, body, isForm = false) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      if (isForm) opt.body = body;
      else {
        opt.headers["content-type"] = "application/json";
        opt.body = JSON.stringify(body);
      }
    }
    const r = await fetch(this.base + path, opt);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
  }

  get(p) { return this.req("GET", p); }
  post(p, b = {}) { return this.req("POST", p, b); }

  health() { return this.get("/api/health"); }
  datasets() { return this.get("/api/datasets"); }
  dialogs(checklistId) { return this.get(`/api/dialogs${checklistId ? `?checklist_id=${checklistId}` : ""}`); }
  dialog(id) { return this.get(`/api/dialogs/${encodeURIComponent(id)}`); }
  sync(groupId) { return this.post("/api/dialogs/sync", { group_id: groupId }); }
  upload(file, dataset) {
    const f = new FormData();
    f.append("file", file);
    return this.req("POST", `/api/dialogs/upload?dataset=${encodeURIComponent(dataset || "UPLOAD")}`, f, true);
  }

  checklists() { return this.get("/api/checklists"); }
  activeChecklist() { return this.get("/api/checklists/active"); }
  saveChecklist(c) { return this.post("/api/checklists", c); }
  activate(id) { return this.post(`/api/checklists/${id}/activate`); }

  run(body) { return this.post("/api/evaluations/run", body); }
  job(id) { return this.get(`/api/jobs/${id}`); }
  jobs() { return this.get("/api/jobs"); }
  evaluation(id) { return this.get(`/api/evaluations/${id}`); }

  appeals(status) { return this.get(`/api/appeals${status ? `?status=${status}` : ""}`); }
  appeal(body) { return this.post("/api/appeals", body); }
  decide(id, body) { return this.post(`/api/appeals/${id}/decision`, body); }

  accuracy() { return this.get("/api/accuracy"); }
  saveGroundTruth(records) { return this.post("/api/ground-truth", records); }
  gtFromEval(id) { return this.post(`/api/ground-truth/from-evaluation/${id}`); }
  deleteGroundTruth(id) { return this.req("DELETE", `/api/ground-truth/${encodeURIComponent(id)}`); }
  exportUrl(fmt) { return `${this.base}/api/export?fmt=${fmt}`; }
  exportJson() { return this.get("/api/export"); }

  stats(checklistId) { return this.get(`/api/stats${checklistId ? `?checklist_id=${checklistId}` : ""}`); }
}
