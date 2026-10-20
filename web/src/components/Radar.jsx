/* React-обёртка императивного радара (lib/radar.js): создаёт его в своём узле, отдаёт
   экземпляр через onReady и отпускает при размонтировании. Точки двигаются сотнями CSS-
   переходов на каждом событии прогона — это остаётся внутри модуля, а не в состоянии React. */
import { useEffect, useRef } from "react";
import R from "../lib/radar.js";

export default function Radar({ opts, onReady, className = "radarbox" }) {
  const ref = useRef(null);
  const ready = useRef(onReady);
  ready.current = onReady;
  const options = useRef(opts);
  useEffect(() => {
    const el = ref.current;
    const radar = R.create(el, options.current || {});
    if (ready.current) ready.current(radar);
    return () => { radar.destroy(); el.innerHTML = ""; };
  }, []);
  return <div className={className} ref={ref} />;
}
