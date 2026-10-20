/* АС ПЕЛИКАН — клиент API (`pelican.api`, тот же адрес, что и интерфейс). Ошибка запроса —
   исключение с понятным текстом: 422 и 429 несут объяснение сервера (`detail`), сбой сети
   и 5xx — свой текст, без кодов. Отчёты кэшируются в памяти вкладки: готовый отчёт не меняется. */
  function fail(status, text) { var e = new Error(text); e.status = status; return e; }

  function request(method, path, body) {
    var opts = { method: method, headers: { Accept: "application/json" } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (data) {
        if (r.ok) return { status: r.status, data: data };
        var detail = data && typeof data.detail === "string" ? data.detail : "";
        if (r.status === 404) throw fail(404, detail || "не найдено");
        if (r.status === 403 || r.status === 422 || r.status === 429) throw fail(r.status, detail || "запрос не принят");
        throw fail(r.status, "Сервер ответил ошибкой " + r.status + ". Повторите через минуту.");
      });
    }, function () {
      throw fail(0, "Нет связи с сервером. Проверьте сеть и повторите.");
    });
  }

  var reports = {};
  /* Отчёт по имени: результат прогона (`AskResult`) плюс имя — в JSON результата его нет. */
  function report(name) {
    if (reports[name]) return Promise.resolve(reports[name]);
    return request("GET", "/api/reports/" + encodeURIComponent(name)).then(function (r) {
      var rep = r.data; rep.name = name;
      rep.thin = rep.thin || []; rep.excluded = rep.excluded || []; rep.facets = rep.facets || [];
      reports[name] = rep;
      return rep;
    });
  }

  /* Вся история: сводки отчётов, новые сверху (`GET /api/history`). */
  function history(limit) {
    return request("GET", "/api/history?limit=" + (limit || 1000)).then(function (r) { return r.data; });
  }

  /* Новый запрос: {cached: true, report} — готовый отчёт; иначе снимок задания (id, status, position…). */
  function ask(query) {
    return request("POST", "/api/ask", { query: query }).then(function (r) { return r.data; });
  }
  function job(id) { return request("GET", "/api/jobs/" + id).then(function (r) { return r.data; }); }
  function events(id, after) {
    return request("GET", "/api/jobs/" + id + "/events?after=" + (after || 0)).then(function (r) { return r.data; });
  }
  /* Очередь глазами этого адреса: у своих заданий (`mine`) — id, запрос и этап, у чужих — только место. */
  function queue() { return request("GET", "/api/queue").then(function (r) { return r.data; }); }
  /* Отмена своего задания: ожидающее снимается сразу ({status: "cancelled"}), идущее — на границе этапа. */
  function cancel(id) { return request("POST", "/api/jobs/" + id + "/cancel").then(function (r) { return r.data; }); }

  /* Тексты подсказок ⓘ и русские имена источников (`GET /api/glossary`) — один раз на вкладку.
     Не ответил — пустой словарь: подсказок нет, но экран работает. */
  var glossaryOnce = null;
  function glossary() {
    if (!glossaryOnce) glossaryOnce = request("GET", "/api/glossary").then(function (r) { return r.data; }, function () { glossaryOnce = null; return { hints: {}, live_names: {} }; });
    return glossaryOnce;
  }

export default { request: request, report: report, history: history, ask: ask, job: job, events: events, queue: queue, cancel: cancel, glossary: glossary };
