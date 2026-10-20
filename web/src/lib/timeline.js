/* АС ПЕЛИКАН — этапы прогона и часы. Порядок и id этапов — те же, что у событий `stage`
   бэкенда (`weak/ask.py: STAGES`). `at` — секунды начала этапа: пока своей истории нет —
   оценка по замерам стенда (docs/techdoc.md §5, прогон ≈ 19 мин), потом — медиана последних
   прогонов из `GET /api/jobs/{id}` (`stage_typical`, см. setTypical). */
  var STAGES = [
    { id: "expand",   at: 0,    name: "раскрываю запрос в направления" },
    { id: "science",  at: 15,   name: "ищу по локальному корпусу и называю технологию в работах" },
    { id: "money",    at: 105,  name: "денежный поток: читаю заголовки о раундах, пилотах, сделках" },
    { id: "merge",    at: 330,  name: "свожу кандидатов к ядру, сверяю близнецов" },
    { id: "evidence", at: 370,  name: "меряю след в корпусе, живой поиск, нахожу компании" },
    { id: "assess",   at: 550,  name: "оцениваю зрелость кандидатов" },
    { id: "score",    at: 720,  name: "скоринг моделью роста, проверяю соответствие запросу" },
    { id: "patents",  at: 740,  name: "патентная активность финалистов" },
    { id: "cards",    at: 950,  name: "углубление и карточки" },
    { id: "save",     at: 1130, name: "сохраняю отчёт" }
  ];
  /* Оценки стенда без медиан: руководство и печать показывают их, а не медианы последнего
     открытого задания — `setTypical` меняет только `STAGES`. */
  var BASE = Object.freeze(STAGES.map(function (s) { return Object.freeze({ id: s.id, at: s.at, name: s.name }); }));
  /* Длительность прогона, пока истории нет, — копия `jobs.DEFAULT_RUN_S`; медиана — по стольким
     последним отчётам, сколько берёт `jobs.HISTORY`. */
  var DEFAULT_RUN_S = 1020, HISTORY = 20;
  var api = { STAGES: STAGES, BASE: BASE, TOTAL: 1140, DEFAULT_RUN_S: DEFAULT_RUN_S };

  /* Типичная длительность прогона: `server` — `typical_run_s` ответа истории (то же число, что на
     ожидании); без него — медиана `seconds` сводок (новые сверху); истории нет — `DEFAULT_RUN_S`. */
  api.typicalRun = function (items, server) {
    if (typeof server === "number" && server > 0) return server;
    var s = (items || []).slice().sort(function (a, b) { return a.started < b.started ? 1 : -1; })
      .slice(0, HISTORY).map(function (r) { return r.seconds; })
      .filter(function (x) { return typeof x === "number" && x > 0; }).sort(function (a, b) { return a - b; });
    if (!s.length) return DEFAULT_RUN_S;
    var k = s.length >> 1;
    return s.length % 2 ? s[k] : (s[k - 1] + s[k]) / 2;
  };
  /* «17 минут» / «около 17 минут» — для текстов входа, истории и руководства. */
  api.runMinutes = function (items, server) {
    var m = Math.max(1, Math.round(api.typicalRun(items, server) / 60)), a = m % 100, b = m % 10;
    return m + " " + (a > 10 && a < 20 ? "минут" : b === 1 ? "минуту" : b > 1 && b < 5 ? "минуты" : "минут");
  };
  api.runText = function (items, server) { return "около " + api.runMinutes(items, server); };

  /* Медианы начала этапов с сервера → `at`. Этап без истории (выключен флагом, ещё не
     встречался) сохраняет оценку, но не раньше предыдущего: полоса не идёт вспять. */
  api.setTypical = function (typical, total) {
    if (!typical) return;
    var prev = 0;
    STAGES.forEach(function (s) {
      if (typeof typical[s.id] === "number") s.at = typical[s.id];
      s.at = Math.max(prev, s.at); prev = s.at;
    });
    if (total > prev) api.TOTAL = total; else api.TOTAL = prev + 10;
  };

  api.clock = function (sec) {
    sec = Math.max(0, Math.round(sec));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  };
  api.minutes = function (sec) { return Math.round(sec / 60) + " мин"; };

export default api;
