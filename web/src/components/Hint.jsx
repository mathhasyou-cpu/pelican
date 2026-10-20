/* Подсказка ⓘ к числу или колонке. Тексты — те же, что у отчёта-документа (`weak.page.HINTS`,
   `GET /api/glossary`): своих формулировок интерфейс не держит. Нет текста — нет значка. */
import { createContext, useContext, useEffect, useState } from "react";
import A from "../lib/api.js";

export const GlossaryCtx = createContext({ hints: {}, live_names: {} });

/* Глоссарий для поддерева: грузится один раз, до ответа — пустой. */
export function GlossaryProvider({ children }) {
  const [g, setG] = useState({ hints: {}, live_names: {} });
  useEffect(() => {
    let live = true;
    A.glossary().then((d) => { if (live && d) setG(d); });
    return () => { live = false; };
  }, []);
  return <GlossaryCtx.Provider value={g}>{children}</GlossaryCtx.Provider>;
}

export function useGlossary() { return useContext(GlossaryCtx); }

/* Ширина подсказки (`.info .tip`, max-width 320 px) и запас до края окна. */
const TIP_W = 336;

/* right — подсказка прижата к правому краю, чтобы у крайних чисел не вылезла за экран. Колонка
   числа зависит от ширины окна (4 колонки KPI, 2 на планшете, auto-fit у второго ряда), поэтому
   при наведении и фокусе значок у правого края прижимает подсказку вправо и сам. */
export default function Hint({ k, right }) {
  const text = useGlossary().hints[k];
  const [flip, setFlip] = useState(false);
  if (!text) return null;
  const place = (e) => setFlip(e.currentTarget.getBoundingClientRect().left + TIP_W > document.documentElement.clientWidth);
  return (
    <span className={"info" + (right || flip ? " r" : "")} tabIndex={0} aria-label={text} onMouseEnter={place} onFocus={place}>
      ⓘ<span className="tip" role="tooltip">{text}</span>
    </span>
  );
}
