/* Отчёты на главной и в истории открываются ссылками, а не кнопками: адрес можно открыть в
   новой вкладке, скопировать, Ctrl-кликнуть. */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AppCtx } from "../appctx.jsx";
import HistoryList, { RecentCards } from "../components/HistoryList.jsx";

afterEach(cleanup);

const items = [
  { name: "20261012-092107", query: "защита информации от ии", started: "2026-10-12T09:44:17+00:00", signals: 15, excluded: 14, seconds: 1389 },
  { name: "20260928-200250", query: "перспективные решения в финтехе", started: "2026-09-28T20:28:41+00:00", signals: 15, excluded: 9, seconds: 1200 },
];
const withApp = (ui, app) => render(<AppCtx.Provider value={app}>{ui}</AppCtx.Provider>);

it("строка истории: запрос и «открыть» — ссылки на отчёт, печать — ссылка на print.html", () => {
  withApp(<HistoryList items={items} />, { navTo: vi.fn() });
  const row = screen.getByText("защита информации от ии").closest("li");
  const links = within(row).getAllByRole("link");
  expect(links.map((a) => a.getAttribute("href"))).toEqual([
    "#report=20261012-092107", "#report=20261012-092107", "print.html#report=20261012-092107&print",
  ]);
  expect(within(row).queryAllByRole("button")).toHaveLength(0);
});

it("карточка последнего отчёта — ссылка; обычный клик внутри приложения, Ctrl-клик — браузеру", () => {
  const app = { navTo: vi.fn() };
  withApp(<RecentCards items={items} />, app);
  const link = screen.getByRole("link", { name: "перспективные решения в финтехе" });
  expect(link.getAttribute("href")).toBe("#report=20260928-200250");
  fireEvent.click(link, { ctrlKey: true });
  expect(app.navTo).not.toHaveBeenCalled();
  fireEvent.click(link);
  expect(app.navTo).toHaveBeenCalledWith("report=20260928-200250");
});

it("пункт палитры с адресом — ссылка: Ctrl-клик уходит браузеру, обычный клик выполняет пункт", async () => {
  const { default: CommandPalette } = await import("../components/CommandPalette.jsx");
  const { createRef } = await import("react");
  const { act } = await import("@testing-library/react");
  if (!HTMLDialogElement.prototype.showModal) HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  if (!HTMLDialogElement.prototype.close) HTMLDialogElement.prototype.close = function () { this.open = false; };
  const run = vi.fn(), ref = createRef();
  render(<CommandPalette ref={ref} items={() => [{ group: "Отчёты", label: "финтех", href: "#report=20260928-200250", run }]} />);
  act(() => ref.current.open());
  const option = screen.getByRole("option", { name: /финтех/ });
  expect(option.tagName).toBe("A");
  expect(option.getAttribute("href")).toBe("#report=20260928-200250");
  expect(fireEvent.click(option, { ctrlKey: true })).toBe(true);   // не preventDefault — новая вкладка
  expect(run).not.toHaveBeenCalled();
});
