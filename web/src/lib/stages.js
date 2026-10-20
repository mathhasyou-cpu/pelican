/* Стадии зрелости — те же строки, что отдаёт бэкенд (`weak/rubric.py: STAGES`, строчными).
   ⚠️ Сравнивать только через stageIndex: со строкой с заглавной («Раннее внедрение») индекс
   был бы −1, и сигнал молча падал бы в пояс «концепция». */
export const STAGES = ["концепция/исследование", "прототип/poc", "пилот", "раннее внедрение"];
/* Подписи для людей — `rubric.STAGE_LABELS`. */
export const STAGE_LABEL = ["Концепция/Исследование", "Прототип/PoC", "Пилот", "Раннее внедрение"];
export const STAGE_SHORT = ["концепция", "прототип", "пилот", "раннее внедрение"];

/* Индекс стадии 0…3; неизвестная — −1. */
export function stageIndex(stage) {
  return STAGES.indexOf(String(stage || "").trim().toLowerCase());
}
export function stageLabel(stage) { const i = stageIndex(stage); return i >= 0 ? STAGE_LABEL[i] : String(stage || ""); }
export function stageShort(stage) { const i = stageIndex(stage); return i >= 0 ? STAGE_SHORT[i] : String(stage || ""); }
