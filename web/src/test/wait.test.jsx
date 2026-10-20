/* Экран ожидания проигрывает события прогона (`GET /api/jobs/{id}/events`): этап, направления,
   числа, вердикты — и по концу задания уходит в отчёт. */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AppCtx } from "../appctx.jsx";
import A from "../lib/api.js";
import Wait from "../screens/Wait.jsx";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("этап, направления, вердикты и переход в готовый отчёт", async () => {
  vi.spyOn(A, "job").mockResolvedValue({ id: 7, status: "running", query: "платежи агентов", stage_typical: {}, typical_run_s: 1140 });
  vi.spyOn(A, "events").mockResolvedValue({
    status: "done", report: "20261019-170000", elapsed: 900, next: 6,
    events: [
      { t: "stage", id: "expand", n: 1, of: 10, name: "раскрываю запрос в направления", at: 1 },
      { t: "facets", facets_ru: ["платежи", "скоринг"], at: 20 },
      { t: "counts", candidates: 3, categories_removed: 0, at: 300 },
      { t: "verdict", label: "agent mandates", title: "Мандаты агентов", kind: "emerging", stage: "пилот", facet: 0, why: "Раунд [1].", at: 600 },
      { t: "verdict", label: "open banking", title: "Открытый банкинг", kind: "mature", kind_label: "зрелое", stage: "раннее внедрение", why: "Стандарт.", at: 610 },
    ],
  });
  const app = { navTo: vi.fn(), toast: vi.fn(), loadHistory: vi.fn().mockResolvedValue([]), copyLink: vi.fn(), setCommands: () => {}, startJob: vi.fn() };
  render(<AppCtx.Provider value={app}><Wait id={7} /></AppCtx.Provider>);
  expect(await screen.findByText("Мандаты агентов")).toBeTruthy();
  expect(screen.getByText("Открытый банкинг")).toBeTruthy();
  expect(document.querySelector("#facets").textContent).toContain("платежи");   // и чипом, и сектором радара
  expect(screen.getByText("Раунд.")).toBeTruthy();   // ссылки [n] в ленте сняты
  expect(screen.getByText("2 из 3, сигналов 1, снято 1")).toBeTruthy();
  // axe button-name (NFR-72): у каждой точки радара в дереве доступности есть имя; безымянная — скрыта
  const dots = [...document.querySelectorAll("button.dot")];
  expect(dots.length).toBe(3);
  expect(dots.filter((b) => b.getAttribute("aria-hidden") !== "true").every((b) => b.getAttribute("aria-label"))).toBe(true);
  expect(dots.filter((b) => b.getAttribute("aria-hidden") === "true")).toHaveLength(1);
  // replace: «Назад» с отчёта не должен возвращать на ожидание, которое снова откроет отчёт
  await waitFor(() => expect(app.navTo).toHaveBeenCalledWith("report=20261019-170000", { view: "radar", replace: true }));
});

it("уход из очереди в работу виден без событий этапа и без перезагрузки", async () => {
  // экран открыт, пока задание в очереди; опрос уже видит «в работе», а событий нет —
  // так ведёт себя исполнитель, который событий не пишет
  vi.spyOn(A, "job").mockResolvedValue({ id: 8, status: "queued", query: "финтех", position: 1, eta_start_s: 60, typical_run_s: 1260 });
  vi.spyOn(A, "events").mockResolvedValue({ status: "running", elapsed: 12, next: 0, events: [] });
  const app = { navTo: vi.fn(), toast: vi.fn(), loadHistory: vi.fn(), copyLink: vi.fn(), setCommands: () => {}, startJob: vi.fn() };
  render(<AppCtx.Provider value={app}><Wait id={8} /></AppCtx.Provider>);
  expect(await screen.findByText("Идёт поиск сигналов")).toBeTruthy();
  expect(screen.queryByText(/В очереди/)).toBeNull();
});

it("«впереди N» не считает идущий, когда его нет (UC-02 шаг 5)", async () => {
  vi.spyOn(A, "events").mockResolvedValue({ status: "queued", elapsed: 0, next: 0, events: [] });
  const app = { navTo: vi.fn(), toast: vi.fn(), loadHistory: vi.fn(), copyLink: vi.fn(), setCommands: () => {}, startJob: vi.fn() };
  const cases = [
    [{ position: 2, running_ahead: true }, "впереди 1 запрос и идущий"],
    [{ position: 1, running_ahead: true }, "впереди идущий запрос"],
    [{ position: 1, running_ahead: false }, "впереди 1 запрос, "],
    [{ position: 0, running_ahead: false }, "впереди никого"],
  ];
  for (const [pos, text] of cases) {
    vi.spyOn(A, "job").mockResolvedValue({ id: 11, status: "queued", query: "финтех", eta_start_s: 60, typical_run_s: 1260, ...pos });
    render(<AppCtx.Provider value={app}><Wait id={11} /></AppCtx.Provider>);
    expect((await screen.findByText(/^В очереди: /)).textContent).toContain(text);
    cleanup();
  }
});

it("без событий этапов показывает последнюю строку прогресса и честную подпись", async () => {
  const snap = { id: 9, status: "running", query: "финтех", typical_run_s: 1260,
    progress: [" 381 с · второй круг: ещё 207 заголовков", " 581 с · свожу 69 кандидатов к ядру технологии"] };
  vi.spyOn(A, "job").mockResolvedValue(snap);
  vi.spyOn(A, "events").mockResolvedValue({ status: "running", elapsed: 653, next: 0, events: [] });
  const app = { navTo: vi.fn(), toast: vi.fn(), loadHistory: vi.fn(), copyLink: vi.fn(), setCommands: () => {}, startJob: vi.fn() };
  render(<AppCtx.Provider value={app}><Wait id={9} /></AppCtx.Provider>);
  expect(await screen.findByText("свожу 69 кандидатов к ядру технологии")).toBeTruthy();
  expect(screen.getByText(/Подробных этапов нет/)).toBeTruthy();
  expect(screen.getByText(/по времени/)).toBeTruthy();
});

it("после обрыва связи «дольше обычного» сохраняет штриховку статуса (NFR-08)", async () => {
  vi.spyOn(A, "job").mockResolvedValue({ id: 10, status: "running", query: "финтех", stage_typical: {}, typical_run_s: 1140 });
  const stage = { t: "stage", id: "cards", n: 9, of: 10, name: "углубление и карточки", at: 1400 };
  vi.spyOn(A, "events")
    .mockResolvedValueOnce({ status: "running", elapsed: 1500, next: 1, events: [stage] })
    .mockRejectedValueOnce(new Error("Нет связи с сервером."))
    .mockResolvedValue({ status: "running", elapsed: 1510, next: 1, events: [] });
  const app = { navTo: vi.fn(), toast: vi.fn(), loadHistory: vi.fn(), copyLink: vi.fn(), setCommands: () => {}, startJob: vi.fn() };
  render(<AppCtx.Provider value={app}><Wait id={10} /></AppCtx.Provider>);
  expect(await screen.findByText("Идёт дольше обычного")).toBeTruthy();
  // второй опрос — через POLL_MS = 2,5 с; при параллельных файлах jsdom 4 с впритык
  expect(await screen.findByText("Нет связи с сервером, повторяю", {}, { timeout: 7000 })).toBeTruthy();
  expect(await screen.findByText("Идёт дольше обычного", {}, { timeout: 7000 })).toBeTruthy();
  expect(document.querySelector(".status").classList.contains("over")).toBe(true);
}, 15000);

it("пока связи нет, «дольше обычного» не перебивает «нет связи» (PQA-019)", async () => {
  vi.spyOn(A, "job").mockResolvedValue({ id: 12, status: "running", query: "финтех", stage_typical: {}, typical_run_s: 1140 });
  const stage = { t: "stage", id: "cards", n: 9, of: 10, name: "углубление и карточки", at: 1400 };
  vi.spyOn(A, "events")
    .mockResolvedValueOnce({ status: "running", elapsed: 1500, next: 1, events: [stage] })
    .mockRejectedValue(new Error("Нет связи с сервером."));
  const app = { navTo: vi.fn(), toast: vi.fn(), loadHistory: vi.fn(), copyLink: vi.fn(), setCommands: () => {}, startJob: vi.fn() };
  render(<AppCtx.Provider value={app}><Wait id={12} /></AppCtx.Provider>);
  expect(await screen.findByText("Нет связи с сервером, повторяю", {}, { timeout: 7000 })).toBeTruthy();
  // часы идут каждый кадр — статус обрыва должен пережить их
  await new Promise((r) => setTimeout(r, 500));
  expect(screen.getByText("Нет связи с сервером, повторяю")).toBeTruthy();
  expect(screen.queryByText("Идёт дольше обычного")).toBeNull();
}, 15000);
