/* Заставка. Один оркестрованный момент на 2584 мс (987 + 987 + 610): проступает вода и круги
   по ней, пеликан прорисовывается в нырке и уходит головой под воду, у клюва на глубине
   вспыхивает блип, летят брызги, поднимается словесный знак, слой гаснет. Раз за сессию;
   клик или клавиша — сразу к делу; по прямой ссылке на отчёт или ожидание — короткий
   вариант на 987 мс.
   ⚠️ Показывать ли — решает index.html до первой отрисовки (классы `splashing`,
   `splash-short` на <html>), и пока класс `splashing` стоит, экран под заставкой скрыт.
   Заставка снимает его, когда начинает гаснуть: экран проступает из-под неё. Решение здесь,
   в React, опаздывало на отрисовку — готовый отчёт мелькал до заставки.
   Знак — константная разметка lib/logo.js, данных в ней нет. */
import { useEffect, useRef, useState } from "react";
import L from "../lib/logo.js";
import { reduceMotion } from "../lib/format.js";

const root = document.documentElement;
function reveal() { root.classList.remove("splashing", "splash-short"); }

export default function Splash() {
  const [show] = useState(() => root.classList.contains("splashing"));
  const [short] = useState(() => root.classList.contains("splash-short"));
  const [state, setState] = useState("on");   // on → leaving → off
  // Разметка знака — один раз: `L.svg` даёт новые id масок на каждый вызов, и новая строка
  // заставляла React вставлять знак заново — анимация пеликана начиналась сначала.
  const [mark] = useState(() => L.svg({ cls: "logo-big", stroke: 1.1, bill: true }));
  const skip = useRef(() => {});
  useEffect(() => {
    if (!show) return undefined;
    // ⚠️ Уход — один раз. Обработчик клавиш висел и после ухода: каждое нажатие (зажатый Alt
    // шлёт их очередью, набор в поиске — тоже) возвращало заставку в «гаснет», и она мигала.
    let gone = false;
    const leave = (quick) => {
      if (gone) return;
      gone = true;
      clearTimeout(t);
      document.removeEventListener("keydown", onKey);
      setState(quick ? "fast" : "leaving");
    };
    // Модификатор сам по себе — не «к делу»: это Alt+Tab, Ctrl+… , а не просьба пропустить.
    const onKey = (e) => { if (!["Alt", "Control", "Shift", "Meta", "AltGraph"].includes(e.key)) leave(true); };
    const hold = reduceMotion ? 610 : short ? 987 : 2207;
    const t = setTimeout(() => leave(false), hold);
    document.addEventListener("keydown", onKey);
    skip.current = () => leave(true);   // клик — тот же единственный уход
    return () => { gone = true; clearTimeout(t); document.removeEventListener("keydown", onKey); };
  }, [show, short]);
  useEffect(() => {
    if (state === "on" || state === "off") return undefined;
    reveal();   // гаснет — экран проступает из-под неё
    const t = setTimeout(() => setState("off"), state === "fast" ? 160 : 400);
    return () => clearTimeout(t);
  }, [state]);
  if (!show || state === "off") return null;
  const eye = L.HIT;
  const cls = "splash " + (reduceMotion ? "still" : "play") + (short ? " short" : "") +
    (state !== "on" ? " leaving" : "") + (state === "fast" ? " fast" : "");
  return (
    <div className={cls} aria-hidden="true" onClick={() => skip.current()}>
      <div className="inner">
        <div className="mark-wrap">
          <span dangerouslySetInnerHTML={{ __html: mark }} style={{ display: "contents" }} />
          <span className="ping" style={{ left: (eye.cx / 55 * 100).toFixed(1) + "%", top: (eye.cy / 55 * 100).toFixed(1) + "%" }} />
        </div>
        <div className="word"><span className="wm">АС ПЕЛИКАН</span><small>автоматизированная система</small></div>
      </div>
    </div>
  );
}
