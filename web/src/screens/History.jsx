/* История запросов: поиск, месяцы; «/» ставит курсор в поиск. */
import { useEffect, useState } from "react";
import { Fail, Loading, useApp } from "../appctx.jsx";
import Active from "../components/Active.jsx";
import HistoryList from "../components/HistoryList.jsx";
import { reportsWord } from "../lib/format.js";

export default function History() {
  const app = useApp();
  const [state, setState] = useState({ loading: true });
  useEffect(() => {
    let live = true;
    app.loadHistory().then(() => { if (live) setState({}); }, (err) => { if (live) setState({ error: err.message }); });
    return () => { live = false; };
  }, [app.loadHistory]);   // история перечитывается при каждом входе на экран
  if (state.loading) return <Loading text="Загружаю историю" />;
  if (state.error) return <Fail title="История не загрузилась" text={state.error} retry />;
  const { total, items, typical } = app.hist;
  return (
    <>
      <section className="page-head">
        <h1 className="h1">История запросов</h1>
        <p className="lead">
          {total ? reportsWord(total) + ", от новых к старым. Любой открывается сразу, отчёт для печати и PDF — значком в строке."
            : "Отчётов пока нет. Первый появится здесь, когда закончится запрос с главной."}
        </p>
      </section>
      <Active />
      {total ? (
        <div className="surface hist">
          <HistoryList items={items} typical={typical} />
        </div>
      ) : null}
    </>
  );
}
