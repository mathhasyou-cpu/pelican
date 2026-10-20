/* Командная палитра (Ctrl+K / ⌘K). Один список на всё: действия экрана, отчёты из истории,
   «найти сигналы по введённому запросу». Стрелки выбирают, Enter выполняет, Esc закрывает.
   Окно — <dialog>: фокус внутри, фон под вуалью. items(q) → [{group, label, hint, icon, run, href?}];
   с `href` пункт — ссылка. */
import { useEffect, useImperativeHandle, useRef, useState } from "react";
import { MAC } from "../lib/format.js";
import { Icon, Kbd } from "./Icon.jsx";

export default function CommandPalette({ items, ref }) {
  const dlg = useRef(null), input = useRef(null), list = useRef(null);
  const [q, setQ] = useState("");
  const [cur, setCur] = useState(0);
  const [open, setOpen] = useState(false);
  const shown = open ? items(q.trim()) : [];
  const at = Math.min(cur, Math.max(0, shown.length - 1));

  function show() { if (dlg.current.open) return; setQ(""); setCur(0); setOpen(true); dlg.current.showModal(); input.current.focus(); }
  function close() { if (dlg.current.open) dlg.current.close(); setOpen(false); }
  function run(i) { const it = shown[i]; if (!it) return; close(); setTimeout(() => it.run(), 0); }
  useImperativeHandle(ref, () => ({ open: show, close }));

  useEffect(() => {
    // по коду клавиши, а не по букве: в русской раскладке K — это «Л»
    function onKey(e) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.code === "KeyK") { e.preventDefault(); if (dlg.current.open) close(); else show(); }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });
  useEffect(() => {
    const el = list.current && list.current.querySelector('[data-i="' + at + '"]');
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
  }, [at]);

  function onKeyDown(e) {
    if (!shown.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setCur((at + 1) % shown.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCur((at - 1 + shown.length) % shown.length); }
    else if (e.key === "Enter") { e.preventDefault(); run(at); }
  }

  let group = null;
  return (
    <dialog className="cmdk" aria-label="Поиск и команды" ref={dlg} onClose={() => setOpen(false)}
      onClick={(e) => { if (e.target === dlg.current) close(); }}>
      <div className="cmdk-box">
        <label className="cmdk-in">
          <Icon name="search" />
          <input ref={input} type="text" placeholder="Найти отчёт, команду или задать новый запрос" autoComplete="off" spellCheck="false"
            role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-autocomplete="list" aria-label="Поиск и команды"
            aria-activedescendant={shown.length ? "cmdk-o" + at : undefined}
            value={q} onChange={(e) => { setQ(e.target.value); setCur(0); }} onKeyDown={onKeyDown} />
          <Kbd>Esc</Kbd>
        </label>
        <div className="cmdk-list" id="cmdk-list" role="listbox" ref={list}>
          {!shown.length ? <p className="cmdk-empty">Ничего не нашлось. Наберите хотя бы три буквы — и запрос можно будет запустить.</p> : null}
          {shown.map((it, i) => {
            const head = it.group !== group ? (group = it.group) : null;
            return [
              head ? <div className="cmdk-g" role="presentation" key={"g" + i}>{head}</div> : null,
              /* Пункт с адресом (`href`) — настоящая ссылка: Ctrl-клик и средняя кнопка открывают
                 новую вкладку (NFR-12); обычный щелчок выполняет пункт на месте. */
              <a className="cmdk-o" role="option" id={"cmdk-o" + i} key={i} data-i={i} aria-selected={i === at} href={it.href}
                onClick={(e) => { if (it.href && (e.ctrlKey || e.metaKey || e.shiftKey)) { close(); return; } e.preventDefault(); run(i); }}
                onMouseMove={() => { if (i !== at) setCur(i); }}>
                <span className="cmdk-ic"><Icon name={it.icon || "arrow-right"} /></span>
                <span className="cmdk-l">{it.label}</span>
                {it.hint ? <span className="cmdk-h">{it.hint}</span> : null}
              </a>,
            ];
          })}
        </div>
        <div className="cmdk-foot">
          <span><Kbd>↑</Kbd><Kbd>↓</Kbd> выбрать</span><span><Kbd>Enter</Kbd> выполнить</span><span className="grow" />
          <span><Kbd>{MAC ? "⌘" : "Ctrl"}</Kbd><Kbd>K</Kbd> открыть отовсюду</span>
        </div>
      </div>
    </dialog>
  );
}
