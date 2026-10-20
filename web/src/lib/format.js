/* АС ПЕЛИКАН — форматы и мелкие помощники. Данные — строго поля Signal/Source/Check из
   `weak/ask.py` (JSON отчёта, `page.to_json`). */

export function pct(x) { return Math.round((x || 0) * 100) + " %"; }

const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
export function fmtDate(iso) {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(iso);
  if (!m) return iso;
  return (m[3] ? parseInt(m[3], 10) + " " : "") + MONTHS[parseInt(m[2], 10) - 1] + " " + m[1];
}
/* Время запуска — по часам читателя: API отдаёт ISO с поясом (база хранит UTC). */
export function fmtStarted(iso) {
  const d = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso || "") ? new Date(iso) : null;
  if (d && !isNaN(d)) {
    const hh = d.getHours(), mm = d.getMinutes();
    return d.getDate() + " " + MONTHS[d.getMonth()] + ", " + (hh < 10 ? "0" : "") + hh + ":" + (mm < 10 ? "0" : "") + mm;
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || "");
  return m ? parseInt(m[3], 10) + " " + MONTHS[parseInt(m[2], 10) - 1] + ", " + m[4] + ":" + m[5] : "";
}
export function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}
export function reportsWord(n) { return n + " " + plural(n, "отчёт", "отчёта", "отчётов"); }
export function noRefs(s) { return String(s || "").replace(/\s*(\[\d+\])+/g, ""); }
/* Ссылка источника — только http(s): адрес приходит из внешних лент, и `javascript:` в href
   исполнился бы по клику. */
export function safeUrl(u) { return /^https?:\/\//i.test(String(u || "")) ? u : null; }

export const RELEVANCE = { 3: "прямо о предмете", 2: "смежное: то, на что предмет опирается", 1: "могла бы применяться", 0: "другая отрасль" };

const EXCLUDED_ORDER = ["mature", "standard", "hype", "noise", "too_broad", "offtopic"];
const EXCLUDED_LABEL = { mature: "зрелое", standard: "стандарт", hype: "хайп", noise: "шум", too_broad: "слишком общее", offtopic: "не о предмете" };
export function excludedKey(s) { return s.kind === "emerging" ? "offtopic" : s.kind; }
/* Сводка исключённого по классу в порядке показа: [{key, label, n}]. */
export function excludedSummary(list) {
  const cnt = {};
  list.forEach((s) => { const k = excludedKey(s); cnt[k] = (cnt[k] || 0) + 1; });
  return EXCLUDED_ORDER.filter((k) => cnt[k]).map((k) => ({ key: k, label: EXCLUDED_LABEL[k], n: cnt[k] }));
}

/* Число оценённых кандидатов — из прогона (`AskResult.candidates`): сумма списков занизила бы
   его на неоценённых. Один счёт на экран и печать (BR-06, BR-48). */
export function candidatesOf(rep) {
  return rep.candidates ?? rep.signals.length + (rep.thin || []).length + (rep.watch || []).length + (rep.excluded || []).length;
}
/* Место сигнала в отчёте — одна подпись на экран и печать: в топе, исключено, вне топа. */
export function placeOf(rep, s) {
  const k = rep.signals.indexOf(s);
  if (k >= 0) return "№ " + (k + 1) + " из " + rep.signals.length + " в топе";
  return (rep.excluded || []).includes(s) ? "исключено" : "вне топа";
}

export function pref(k, v) {
  try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch { return null; }
  return null;
}
export const reduceMotion = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
export const MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export function printURL(name) { return "print.html#report=" + encodeURIComponent(name) + "&print"; }
/* Один сигнал документом (ТЗ: «просмотр инсайта открывает страницу, похожую на документ-отчёт»). */
export function docURL(name, label) { return "print.html#report=" + encodeURIComponent(name) + "&sig=" + encodeURIComponent(label); }
