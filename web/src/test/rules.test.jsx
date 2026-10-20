/* Правила требований, которые код базовой линии нарушал (docs/requirements/07 §8.6): клавиши,
   адрес состояния отчёта, подписи и числа. Фикстура — report.json, как в tz.test.jsx. */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppCtx } from "../appctx.jsx";
import Card from "../components/Card.jsx";
import { GlossaryCtx } from "../components/Hint.jsx";
import HistoryList, { monthOf } from "../components/HistoryList.jsx";
import A from "../lib/api.js";
import { candidatesOf, placeOf } from "../lib/format.js";
import T from "../lib/timeline.js";
import Home from "../screens/Home.jsx";
import Report, { reportHash } from "../screens/Report.jsx";
import fixture from "./report.json";

afterEach(() => { cleanup(); location.hash = ""; });
const rep = () => structuredClone(fixture);
const app = () => ({ hist: { items: [], total: 0 }, sorted: [], navTo: vi.fn(), openPrint: vi.fn(), copyLink: vi.fn(), toast: vi.fn(), setCommands: () => {}, mac: false });
const place = () => document.querySelector(".sheet-top .mono").textContent;

async function openReport(r, props = {}) {
  vi.spyOn(A, "report").mockResolvedValue(r);
  render(<AppCtx.Provider value={app()}><Report name={r.name} view="table" {...props} /></AppCtx.Provider>);
  await screen.findByRole("heading", { level: 1, name: r.query });
}

describe("клавиши отчёта (NFR-09, NFR-72)", () => {
  it("↓ листает, а Alt+← остаётся браузеру", async () => {
    await openReport(rep());
    expect(place()).toMatch(/^№ 1 /);
    expect(fireEvent.keyDown(document, { key: "ArrowDown" })).toBe(false);   // preventDefault
    expect(place()).toMatch(/^№ 2 /);
    expect(fireEvent.keyDown(document, { key: "ArrowLeft", altKey: true })).toBe(true);
    expect(place()).toMatch(/^№ 2 /);
  });
  it("Enter и пробел на строке стола открывают карточку", async () => {
    const r = rep();
    await openReport(r);
    const rows = document.querySelectorAll(".reg tbody tr[tabindex]");
    fireEvent.keyDown(rows[2], { key: "Enter" });
    expect(place()).toMatch(/^№ 3 /);
    fireEvent.keyDown(rows[1], { key: " " });
    expect(place()).toMatch(/^№ 2 /);
  });
  it("с раскрытым исключённым листание идёт в порядке таблицы", async () => {
    const r = rep();
    await openReport(r, { cut: true });
    const shown = [...document.querySelectorAll("#cutreg tr[data-label]")].map((tr) => tr.getAttribute("data-label"));
    const last = r.signals[r.signals.length - 1].label;
    fireEvent.click(document.querySelector('.reg tr[data-label="' + CSS.escape(last) + '"]'));
    fireEvent.keyDown(document, { key: "ArrowDown" });
    expect(document.querySelector(".side-card article.card").getAttribute("data-label")).toBe(shown[0]);
  });
});

describe("адрес отчёта (NFR-12, NFR-49)", () => {
  it("выбор сигнала и раскрытие исключённого пишутся в адрес", async () => {
    const r = rep();
    await openReport(r);
    fireEvent.click(document.querySelectorAll(".reg tbody tr[tabindex]")[1]);
    expect(location.hash).toBe("#" + reportHash(r.name, { sig: r.signals[1].label }));
    fireEvent.click(screen.getByRole("button", { name: /Исключено/ }));
    expect(location.hash).toContain("&cut");
  });
  it("адрес с sig и facet открывает тот же вид", async () => {
    const r = rep();
    await openReport(r, { sig: r.signals[2].label, facet: 0 });
    expect(place()).toMatch(/^№ 3 /);
    expect(location.hash).toBe("#" + reportHash(r.name, { sig: r.signals[2].label, facet: 0 }));
  });
});

describe("подписи и числа", () => {
  it("место сигнала одно на экран и печать: вне топа — не «исключено»", () => {
    const r = rep();
    expect(placeOf(r, r.signals[0])).toBe("№ 1 из " + r.signals.length + " в топе");
    expect(placeOf(r, r.thin[0])).toBe("вне топа");
    expect(placeOf(r, r.excluded[0])).toBe("исключено");
    expect(candidatesOf(r)).toBe(r.candidates);
  });
  it("«держится» — из числа прошлых прогонов отчёта, а не из константы", () => {
    const s = { ...rep().signals[0], held: 3 };
    render(<Card sig={s} runs={5} />);
    expect(screen.getByText("держится в 3 из 5 прошлых прогонов")).toBeTruthy();
  });
  it("«k из n» проверок — только по доле уверенности: без условий входа и подписей (UC-05)", () => {
    const checks = [
      { name: "a", passed: true, detail: "", entry: true },
      { name: "b", passed: true, detail: "", scored: false },
      { name: "c", passed: true, detail: "" },
      { name: "d", passed: false, detail: "" },
    ];
    render(<Card sig={{ ...rep().signals[0], checks }} fold />);
    expect(screen.getByText("1 из 2")).toBeTruthy();
  });
  it("пометки доверенности и непроверенных новостей видны в карточке (BR-20, BR-27)", () => {
    render(<Card sig={{ ...rep().signals[0], corroborated: false, news_unmeasured: true }} />);
    expect(screen.getByText(/^пониженная доверенность/)).toBeTruthy();
    expect(screen.getByText(/^новости не проверены/)).toBeTruthy();
    cleanup();
    render(<Card sig={{ ...rep().signals[0], corroborated: true, news_unmeasured: false }} />);
    expect(screen.queryByText(/пониженная доверенность|новости не проверены/)).toBeNull();
  });
  it("подсказки ⓘ карточки — из глоссария (BR-30)", () => {
    const hints = { confidence: "C", stage: "S", why: "W", companies: "K", evidence: "E", relevance: "R", predictors: "P" };
    const sig = { ...rep().signals[0], kind: "emerging", relevance: 3, companies: ["x"], predictors: ["p"] };
    render(<GlossaryCtx.Provider value={{ hints, live_names: {} }}><Card sig={sig} /></GlossaryCtx.Provider>);
    for (const t of Object.values(hints)) expect(screen.getAllByText(t).length).toBeGreaterThan(0);
  });
  it("длительность прогона — медиана истории, без неё — DEFAULT_RUN_S", () => {
    expect(T.typicalRun([])).toBe(1020);
    expect(T.runText([])).toBe("около 17 минут");
    const items = [600, 1200, 900].map((seconds, i) => ({ seconds, started: "2026-10-0" + (i + 1) }));
    expect(T.typicalRun(items)).toBe(900);
    expect(T.runMinutes(items)).toBe("15 минут");
  });
  it("длительность с сервера (typical_run_s истории) важнее медианы показанных отчётов", () => {
    const items = [600, 1200, 900].map((seconds, i) => ({ seconds, started: "2026-10-0" + (i + 1) }));
    expect(T.typicalRun(items, 1500)).toBe(1500);
    expect(T.runMinutes(items, 1500)).toBe("25 минут");
    expect(T.typicalRun(items, undefined)).toBe(900);
  });
  it("руководство и печать видят этапы без медиан последнего задания", () => {
    const before = T.BASE.map((s) => s.at);
    T.setTypical({ science: 999 }, 2000);
    expect(T.BASE.map((s) => s.at)).toEqual(before);
    expect(T.STAGES.find((s) => s.id === "science").at).toBe(999);
  });
});

describe("главная (NFR-16, UC-01)", () => {
  it("пока запрос отправляется, кнопка и примеры отключены и подписаны", () => {
    render(<AppCtx.Provider value={{ ...app(), sending: true, startJob: vi.fn() }}><Home /></AppCtx.Provider>);
    const send = screen.getByRole("button", { name: /Отправляю/ });
    expect(send.disabled).toBe(true);
    expect(screen.getByRole("button", { name: /технологии в ИИ/ }).disabled).toBe(true);
    expect(document.body.textContent).toContain("Новый запрос идёт около 17 минут");
  });
});

describe("история (NFR-09, UC-09)", () => {
  const items = [{ name: "20261012-092107", query: "защита информации от ии", started: "2026-10-12T09:44:17+00:00", signals: 15, excluded: 14, seconds: 1389 }];
  it("«/» не перехватывает ввод в другом поле", () => {
    render(<AppCtx.Provider value={{ navTo: vi.fn() }}><HistoryList items={items} /><input aria-label="палитра" /></AppCtx.Provider>);
    const other = screen.getByLabelText("палитра");
    other.focus();
    expect(fireEvent.keyDown(document, { key: "/" })).toBe(true);
    expect(document.activeElement).toBe(other);
  });
  it("месяц — по часам читателя, как дата строки", () => {
    const iso = "2026-10-31T23:30:00+00:00", d = new Date(iso);
    const months = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
    expect(monthOf(iso)).toBe(months[d.getMonth()] + " " + d.getFullYear());
  });
});

describe("отказ загрузки (UX: обещание повтора)", () => {
  it("сбой сети не обещает повтора сам — даёт кнопку «Повторить»; 404 её не даёт", async () => {
    vi.spyOn(A, "report").mockRejectedValueOnce(Object.assign(new Error("Нет связи с сервером. Проверьте сеть и повторите."), { status: 0 }));
    render(<AppCtx.Provider value={app()}><Report name="20261019-170000" view="table" /></AppCtx.Provider>);
    expect(await screen.findByRole("button", { name: /Повторить/ })).toBeTruthy();
    expect(document.body.textContent).not.toContain("повторит сама");
    cleanup();
    vi.spyOn(A, "report").mockRejectedValueOnce(Object.assign(new Error("не найдено"), { status: 404 }));
    render(<AppCtx.Provider value={app()}><Report name="20261019-170001" view="table" /></AppCtx.Provider>);
    await screen.findByRole("heading", { name: "Такого отчёта нет" });
    expect(screen.queryByRole("button", { name: /Повторить/ })).toBeNull();
  });
});
