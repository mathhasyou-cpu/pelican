/* Запущенную задачу видно вне вкладки ожидания (PQA-001), её можно отменить (UX-02), подсказки
   ⓘ не расширяют страницу (PQA-007). */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AppCtx } from "../appctx.jsx";
import Active, { stateText } from "../components/Active.jsx";
import Hint, { GlossaryCtx } from "../components/Hint.jsx";
import A from "../lib/api.js";
import Wait from "../screens/Wait.jsx";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const app = () => ({ navTo: vi.fn(), toast: vi.fn(), loadHistory: vi.fn(), copyLink: vi.fn(), setCommands: () => {}, startJob: vi.fn() });

it("«Сейчас в работе» — очередь целиком: свои со ссылкой и ожидаемым началом, чужие без текста", async () => {
  vi.spyOn(A, "queue").mockResolvedValue([
    { status: "running", position: 0, mine: false, started: "2026-10-20T10:00:00+00:00", eta_start_s: 0 },
    { status: "queued", position: 1, mine: true, id: 46, query: "агентские платежи", stage: null, eta_start_s: 600 },
  ]);
  render(<AppCtx.Provider value={app()}><Active /></AppCtx.Provider>);
  const link = await screen.findByText("агентские платежи");
  expect(link.getAttribute("href")).toBe("#job=46");
  expect(screen.getByText("в очереди, впереди 1 запрос, начнётся примерно через 10 мин")).toBeTruthy();
  expect(document.querySelectorAll(".arows li").length).toBe(2);
  expect(document.querySelector(".arows li.other a")).toBeNull();   // чужое — не ссылка
  expect(screen.getByText("чужой запрос")).toBeTruthy();
});

it("без своих заданий блока нет", async () => {
  const q = vi.spyOn(A, "queue").mockResolvedValue([{ status: "running", position: 0, mine: false }]);
  const { container } = render(<AppCtx.Provider value={app()}><Active /></AppCtx.Provider>);
  await waitFor(() => expect(q).toHaveBeenCalled());
  expect(container.querySelector("#active")).toBeNull();
});

it("идущее — этап и минуты", () => {
  const x = { status: "running", stage: { n: 4, of: 10 }, started: "2026-10-20T10:00:00Z" };
  expect(stateText(x, Date.parse("2026-10-20T10:07:10Z"))).toBe("в работе, этап 4 из 10, 7 мин");
});

it("отмена на ожидании: подтверждение, «останавливается…», затем «отменён»", async () => {
  // у jsdom нет <dialog>.showModal
  if (!HTMLDialogElement.prototype.showModal) HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  if (!HTMLDialogElement.prototype.close) HTMLDialogElement.prototype.close = function () { this.open = false; };
  vi.spyOn(A, "job").mockResolvedValue({ id: 11, status: "running", query: "a b", stage_typical: {}, typical_run_s: 1140 });
  const ev = vi.spyOn(A, "events").mockResolvedValue({ status: "running", elapsed: 30, next: 1,
    events: [{ t: "stage", id: "expand", n: 1, of: 10, name: "раскрываю запрос в направления", at: 1 }] });
  const cancel = vi.spyOn(A, "cancel").mockResolvedValue({ id: 11, status: "running" });
  render(<AppCtx.Provider value={app()}><Wait id={11} /></AppCtx.Provider>);
  fireEvent.click(await screen.findByText("Отменить"));
  // своё окно (UX-16): «Продолжить ждать» ничего не отменяет
  expect(document.querySelector(".ask-dlg").open).toBe(true);
  fireEvent.click(screen.getByText("Продолжить ждать"));
  expect(document.querySelector(".ask-dlg").open).toBe(false);
  expect(cancel).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Отменить"));
  fireEvent.click(screen.getByText("Отменить запрос"));
  expect(cancel).toHaveBeenCalledWith(11);
  expect(await screen.findByText("Останавливается…")).toBeTruthy();
  ev.mockResolvedValue({ status: "cancelled", elapsed: 40, next: 1, events: [] });
  expect(await screen.findByText("Запрос отменён", {}, { timeout: 4000 })).toBeTruthy();
  expect(screen.getByText("Новый запрос")).toBeTruthy();
}, 8000);

it("подсказка у правого края окна прижимается вправо (PQA-007)", () => {
  render(<GlossaryCtx.Provider value={{ hints: { confident: "Доля сигналов…" }, live_names: {} }}><Hint k="confident" /></GlossaryCtx.Provider>);
  const info = document.querySelector(".info");
  vi.spyOn(info, "getBoundingClientRect").mockReturnValue({ left: 600 });
  Object.defineProperty(document.documentElement, "clientWidth", { value: 768, configurable: true });
  fireEvent.mouseEnter(info);
  expect(info.classList.contains("r")).toBe(true);
});
