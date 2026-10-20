/* АС ПЕЛИКАН — живой прогон: опрос `GET /api/jobs/{id}/events?after=` раз в 2,5 с.
   Интерфейс как у плеера прототипа: on(вид, fn), t — секунды от старта задания, pause().
   Первая пачка (вернулись на вкладку, открыли ссылку на 10-й минуте) приходит с флагом
   `replay`: экран восстанавливает радар и числа разом, без анимации каждого вердикта.
   Задание в очереди отдаёт событие `queued` со снимком (место, ожидаемое начало), переход в
   работу — `running`, снятое автором — `cancelled`. Идёт, а событий этапов нет — `progress` со строками прогресса задания.
   Сбой сети — повтор с растущей паузой до 20 с и событие `offline`; связь вернулась — `online`. */
import A from "./api.js";

  var POLL_MS = 2500, MAX_MS = 20000;

  function Live(id) {
    this.id = id; this.after = 0; this.t = 0; this.status = "";
    this._subs = {}; this._timer = null; this._stopped = false; this._first = true;
    this._base = null; this._elapsed = 0; this._wait = POLL_MS; this._offline = false; this._ended = false;
  }
  Live.prototype.on = function (kind, fn) { (this._subs[kind] = this._subs[kind] || []).push(fn); return this; };
  Live.prototype._emit = function (e) {
    var subs = this._subs[e.t] || [];
    for (var k = 0; k < subs.length; k++) subs[k](e, this);
  };
  /* Секунды прогона по часам сервера плюс сколько прошло с последнего ответа. */
  Live.prototype.now = function () {
    if (this._base == null) return 0;
    return this._elapsed + (performance.now() - this._base) / 1000;
  };
  Live.prototype.play = function () { this._stopped = false; this._poll(); return this; };
  Live.prototype.pause = function () { this._stopped = true; clearTimeout(this._timer); return this; };

  Live.prototype._next = function (ms) {
    var self = this;
    if (this._stopped || this._ended) return;
    clearTimeout(this._timer);
    this._timer = setTimeout(function () { self._poll(); }, ms);
  };
  Live.prototype._poll = function () {
    var self = this;
    if (this._stopped) return;
    A.events(this.id, this.after).then(function (r) {
      if (self._stopped) return;
      if (self._offline) { self._offline = false; self._emit({ t: "online" }); }
      self._wait = POLL_MS;
      // Старт задания — по СТАТУСУ, а не по первому событию этапа: исполнитель, который
      // событий не пишет (старая версия), иначе оставлял экран в «очереди» до перезагрузки.
      var started = r.status === "running" && self.status !== "running";
      self.status = r.status;
      if (r.elapsed != null) { self._elapsed = r.elapsed; self._base = performance.now(); }
      if (started) self._emit({ t: "running" });
      var replay = self._first && r.events.length > 1;
      self._first = false;
      r.events.forEach(function (e) {
        self.lastAt = Math.max(self.lastAt || 0, e.at || 0);
        if (e.t === "stage") self._staged = true;
        if (e.t === "done" || e.t === "failed") return;   // конец — по статусу задания, ниже
        e.replay = replay; self._emit(e);
      });
      self.after = r.next;
      if (r.status === "done") { self._ended = true; self._emit({ t: "done", report: r.report, replay: replay }); return; }
      if (r.status === "failed") { self._ended = true; self._emit({ t: "failed", error: r.error, detail: r.error_detail }); return; }
      if (r.status === "cancelled") { self._ended = true; self._emit({ t: "cancelled" }); return; }
      if (r.status === "queued") {
        return A.job(self.id).then(function (snap) { self._emit({ t: "queued", snap: snap }); self._next(POLL_MS * 2); });
      }
      if (!self._staged) {
        // Идёт, но этапов нет: исполнитель без событий (старая версия) или их запись падает.
        // Тогда — строки прогресса задания (`jobs.progress`, их пишет любой исполнитель).
        return A.job(self.id).then(function (snap) {
          if (!self._staged) self._emit({ t: "progress", lines: snap.progress || [] });
          self._next(POLL_MS * 2);
        });
      }
      self._next(POLL_MS);
    }).catch(function (err) {
      if (self._stopped) return;
      if (err && err.status === 404) { self._ended = true; self._emit({ t: "missing" }); return; }
      if (!self._offline) { self._offline = true; self._emit({ t: "offline", error: err && err.message }); }
      self._wait = Math.min(MAX_MS, self._wait * 2);
      self._next(self._wait);
    });
  };

export function watch(id) { return new Live(id); }
