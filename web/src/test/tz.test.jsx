/* Пункты ТЗ (Газпромбанк.Тех, 7.2), которые обязан показать интерфейс, — на настоящем отчёте
   стенда (report.json — отчёт 20261012-092107, обрезанный до четырёх сигналов). */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppCtx } from "../appctx.jsx";
import Card from "../components/Card.jsx";
import { GlossaryProvider } from "../components/Hint.jsx";
import A from "../lib/api.js";
import { plural } from "../lib/format.js";
import { stageIndex, stageShort } from "../lib/stages.js";
import Report from "../screens/Report.jsx";
import fixture from "./report.json";

afterEach(cleanup);
const rep = () => structuredClone(fixture);
/* Тексты абзацев целиком: ссылки [n] разбивают текст на узлы, и getByText его не находит. */
const paragraphs = () => [...document.querySelectorAll("p")].map((p) => p.textContent);

describe("стадии", () => {
  it("строки бэкенда (строчные) и подписи с заглавной дают один индекс", () => {
    expect(stageIndex("раннее внедрение")).toBe(3);
    expect(stageIndex("Раннее внедрение")).toBe(3);
    expect(stageIndex("прототип/poc")).toBe(1);
    expect(stageShort("прототип/poc")).toBe("прототип");
  });
  it("у каждого сигнала отчёта стадия распознана — радар не сваливает всех в «концепцию»", () => {
    const r = rep();
    for (const s of r.signals.concat(r.thin, r.excluded)) expect(stageIndex(s.stage)).toBeGreaterThanOrEqual(0);
    expect(new Set(r.signals.map((s) => stageIndex(s.stage))).size).toBeGreaterThan(1);
  });
});

describe("карточка сигнала", () => {
  it("показывает описание, преимущество, кейс, уверенность с предикторами и объяснение", () => {
    const s = rep().signals.find((x) => x.why && x.advantage && x.case);
    render(<Card sig={s} />);
    expect(screen.getByText(s.title)).toBeTruthy();
    expect(screen.getByText(Math.round(s.confidence * 100) + " %")).toBeTruthy();
    expect(screen.getByText(s.predictors[0])).toBeTruthy();
    for (const h of ["Почему это слабый сигнал", "Что это", "Потенциальное преимущество", "Кейс для банка", "Источники"]) {
      expect(screen.getByText(h)).toBeTruthy();
    }
    expect(paragraphs()).toContain(s.why);   // ссылки [n] разбивают текст на узлы — сверяем целиком
    expect(paragraphs()).toContain(s.advantage);
  });
  it("пустые объяснение и преимущество названы, а не пропадают молча", () => {
    const s = { ...rep().signals[0], why: "", advantage: "" };
    render(<Card sig={s} />);
    expect(screen.getByText(/Объяснение не сверено с источниками/)).toBeTruthy();
    expect(screen.getByText(/преимущество не названо/)).toBeTruthy();
  });
  it("у источника — наименование со ссылкой, дата, тип, язык, доверие и пометка машинного резюме", () => {
    const s = rep().signals[0];
    const src = s.sources.find((x) => x.url && x.summary_ru);
    render(<Card sig={s} />);
    const row = screen.getByText(src.title).closest("tr");
    expect(screen.getByText(src.title).closest("a").getAttribute("href")).toBe(src.url);
    const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toEqual(expect.arrayContaining([src.kind, src.lang, src.trust_label]));
    expect(row.textContent).toContain(src.summary_ru);
    expect(row.textContent).toContain("машинное резюме");
  });
  it("ссылка [n] в тексте показывает источник и ведёт к нему, раскрывая свёрнутый раздел", () => {
    // фикстура обрезана до четырёх источников — ставим ссылку на тот, что в ней есть
    const s = rep().signals[0];
    const src = s.sources[0], n = src.n;
    s.description = "Метод проверяет память [" + n + "].";
    render(<Card sig={s} fold />);
    const section = screen.getByText("Что это").closest("section");
    const ref = within(section).getAllByRole("link").find((a) => a.textContent === String(n));
    expect(ref.getAttribute("title")).toContain(src.title);
    const row = document.querySelector('[data-src="' + n + '"]');
    expect(row.closest("details").open).toBe(false);
    fireEvent.click(ref);
    expect(row.closest("details").open).toBe(true);
    expect(row.classList.contains("flash")).toBe(true);
  });

  it("оценки в аналитических материалах: резюме отраслевых источников и патенты; нет таких — сказано", () => {
    const s = rep().signals[0];
    const media = s.sources.find((x) => x.kind === "отраслевое медиа");
    render(<Card sig={s} />);
    const sec = screen.getByText("Оценки в аналитических материалах").closest("section");
    expect(sec.textContent).toContain(media.summary_ru);
    expect(sec.textContent).toContain(s.patents);
    cleanup();
    render(<Card sig={{ ...s, patents: "", sources: s.sources.map((x) => ({ ...x, kind: "научная публикация" })) }} />);
    expect(screen.getByText(/нет аналитики, регуляторов или отраслевых медиа/)).toBeTruthy();
  });

  it("номер без такого источника остаётся текстом", () => {
    const s = { ...rep().signals[0], description: "Факт [99]." };
    render(<Card sig={s} />);
    const section = screen.getByText("Что это").closest("section");
    expect(section.textContent).toContain("[99]");
    expect(within(section).queryAllByRole("link")).toHaveLength(0);
  });

  it("зарубежный источник без резюме помечен, ссылка не-http не становится ссылкой", () => {
    const s = rep().signals[0];
    s.sources = [{ ...s.sources[0], summary_ru: "", lang: "en", url: "javascript:alert(1)" }];
    render(<Card sig={s} />);
    expect(screen.getByText("русского резюме нет")).toBeTruthy();
    expect(screen.getByText(s.sources[0].title).closest("a")).toBeNull();
  });
});

describe("экран отчёта", () => {
  function app() {
    return { hist: { items: [], total: 0 }, sorted: [], navTo: vi.fn(), openPrint: vi.fn(), copyLink: vi.fn(), toast: vi.fn(), setCommands: () => {}, mac: false };
  }
  it("запрос, прочитанные источники, сигналы, уверенные выше 75 %, кандидаты со ссылкой на список", async () => {
    const r = rep();
    vi.spyOn(A, "report").mockResolvedValue(r);
    render(<AppCtx.Provider value={app()}><Report name={r.name} view="table" /></AppCtx.Provider>);
    expect(await screen.findByRole("heading", { level: 1, name: r.query })).toBeTruthy();
    // число согласовано: 451 источник, 452 источника, 455 источников
    expect(screen.getByText(new RegExp("Прочитано " + r.sources_processed + " " + plural(r.sources_processed, "источник", "источника", "источников") + " "))).toBeTruthy();
    const kpi = (label) => screen.getByText(label).closest("div").querySelector("dd");
    expect(kpi("сигналов в топе").textContent).toBe(String(r.signals.length));
    expect(kpi("с уверенностью выше 75 %").textContent).toBe(String(r.signals.filter((s) => s.confidence > 0.75).length));
    // число оценённых — из прогона, а не сумма списков (в фикстуре списки обрезаны)
    const link = within(kpi("кандидатов оценено")).getByRole("link");
    expect(link.textContent).toBe(String(r.candidates));
    // инсайт открывается документом; модели раскрыты с ролями (ТЗ 3.1)
    const doc = screen.getByRole("link", { name: /Открыть как документ/ });
    expect(doc.getAttribute("href")).toBe("print.html#report=" + encodeURIComponent(r.name) + "&sig=" + encodeURIComponent(r.signals[0].label));
    expect(document.querySelector(".foot .models").textContent).toContain(r.models[0].role);
    // переход к списку: исключённое раскрывается с причиной по каждому
    expect(document.getElementById("cutreg").hidden).toBe(true);
    fireEvent.click(link);
    expect(document.getElementById("cutreg").hidden).toBe(false);
    expect([...document.querySelectorAll("td.why")].map((t) => t.textContent)).toContain(r.excluded[0].why);
  });

  it("вторая строка чисел, отказы источников, пояснение короткого ТОП и «наблюдать»", async () => {
    const r = rep();
    r.live_by_source = { google_news_en: 45 };
    r.live_errors = { patents: "исчерпан бюджет времени" };
    r.watch = [{ ...r.thin[0], label: "watch me", title: "Наблюдаемое", layer_reason: "одно подтверждение из трёх" }];
    r.history_runs = 2; r.stable = 3;
    vi.spyOn(A, "report").mockResolvedValue(r);
    vi.spyOn(A, "glossary").mockResolvedValue({ hints: { confident: "Сигналов выше 75%.", thin: "Не вошли в ТОП." }, live_names: { patents: "патенты EPO", google_news_en: "Google Новости (англ.)" } });
    render(<AppCtx.Provider value={app()}><GlossaryProvider><Report name={r.name} view="table" /></GlossaryProvider></AppCtx.Provider>);
    await screen.findByRole("heading", { level: 1, name: r.query });
    const kpi = (label) => screen.getByText(label).closest("div").querySelector("dd").textContent;
    expect(kpi("научных работ найдено")).toBe(String(r.works_found));
    expect(kpi("новостей о раундах и пилотах")).toBe(String(r.headlines_seen));
    expect(kpi("источников обработано")).toBe(String(r.sources_processed));
    expect(kpi("за пределами ТОП")).toBe(String(r.thin.length));
    expect(kpi("держатся в ≥ половине из 2 прошлых прогонов")).toBe("3");
    // подсказка ⓘ — текст из глоссария бэкенда
    await waitFor(() => expect(screen.getAllByRole("tooltip").map((t) => t.textContent)).toContain("Сигналов выше 75%."));
    expect(screen.getByText(/Не ответили:/).closest("p").textContent).toContain("патенты EPO (исчерпан бюджет времени)");
    expect(screen.getByText(/Google Новости \(англ\.\): 45/)).toBeTruthy();
    expect(screen.getByRole("note").textContent).toContain("В ТОП набралось " + r.signals.length + " из 15");
    fireEvent.click(screen.getByRole("button", { name: /Исключено/ }));
    expect(screen.getByText("одно подтверждение из трёх")).toBeTruthy();
  });
});

describe("инсайт документом", () => {
  it("print.html с sig= показывает один лист: запрос, место в топе, карточку, модели", async () => {
    const r = rep(), s = r.signals[1];
    location.hash = "report=" + encodeURIComponent(r.name) + "&sig=" + encodeURIComponent(s.label);
    vi.resetModules();   // Print читает адрес при загрузке модуля
    const { default: Print } = await import("../print/Print.jsx");
    const { default: A2 } = await import("../lib/api.js");
    vi.spyOn(A2, "report").mockResolvedValue(r);
    render(<Print />);
    expect(await screen.findByText(s.title)).toBeTruthy();
    expect(document.querySelectorAll("article.card")).toHaveLength(1);
    expect(screen.getByText("№ 2 из " + r.signals.length + " в топе")).toBeTruthy();
    expect(document.body.textContent).toContain(r.query);
    expect(document.body.textContent).toContain(r.models[0].role);
    location.hash = "";
  });
});
