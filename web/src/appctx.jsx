/* Общее для экранов: контекст приложения (переходы, история, тосты) и экраны загрузки/отказа. */
import { createContext, useContext } from "react";
import { Icon } from "./components/Icon.jsx";

export const AppCtx = createContext(null);
export function useApp() { return useContext(AppCtx); }

/* Ссылка внутри приложения: обычный клик — переход без перезагрузки; Ctrl/⌘/Shift-клик,
   средняя кнопка, «копировать адрес», «открыть в новой вкладке» — как у любой ссылки. */
export function Go({ to, children, ...rest }) {
  const app = useApp();
  function onClick(e) {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    app.navTo(to);
  }
  return <a href={"#" + to} onClick={onClick} {...rest}>{children}</a>;
}

/* Экран, пока данные идут с сервера, и экран отказа — с понятным текстом и выходом. */
export function Loading({ text }) {
  return <section className="page-head"><p className="status"><span className="pulse" />{text}</p></section>;
}
export function Fail({ title, text }) {
  return (
    <section className="page-head">
      <h1 className="h1">{title}</h1>
      <p className="lead">{text}</p>
      <p className="row" style={{ marginTop: "var(--sp-6)" }}>
        <Go to="" className="btn primary"><Icon name="plus" />Новый запрос</Go>
        <Go to="history" className="btn"><Icon name="history" />История</Go>
      </p>
    </section>
  );
}
