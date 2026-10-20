import icons from "../lib/icons.js";
import { stageIndex } from "../lib/stages.js";

/* Иконка из спрайта (lib/icons.js); спрайт вставляется в документ при первом вызове. */
export function Icon({ name, className }) {
  icons.sprite();
  return (
    <svg className={"ic" + (className ? " " + className : "")} aria-hidden="true" focusable="false">
      <use href={"#i-" + name} />
    </svg>
  );
}

/* Мини-мишень стадии: закрашен пояс радара, где стоит сигнал (центр — раннее внедрение). */
export function StageIcon({ stage }) {
  const b = 3 - Math.max(0, stageIndex(stage));
  return (
    <svg className="sti" viewBox="0 0 16 16" aria-hidden="true">
      {[2.2, 3.9, 5.6, 7.3].map((r, k) => (
        <circle key={k} cx="8" cy="8" r={r} className={(k === 0 ? "c" : "") + (b === k ? " on" : "")} />
      ))}
    </svg>
  );
}

export function Kbd({ children }) { return <kbd className="kbd">{children}</kbd>; }
