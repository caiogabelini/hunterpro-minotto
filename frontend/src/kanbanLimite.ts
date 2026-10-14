/**
 * Regra do aviso "há mais leads no banco do que os exibidos" no Kanban.
 *
 * `GET /api/leads` corta em `limit` (o Kanban pede o teto do backend,
 * `LIMITE_LEADS_KANBAN` em api.ts). Receber EXATAMENTE `limit` leads é o
 * único sinal que a rota dá de que pode haver mais -- ela não devolve
 * total. Pode ser falso positivo (o banco ter exatamente `limit` leads),
 * e isso é aceitável: o erro na direção contrária -- esconder leads sem
 * avisar -- é o bug que este aviso existe pra impedir (14/10/2026: 120
 * leads no banco, Kanban mostrando 50, contadores das colunas errados).
 *
 * `limite` inválido (<= 0, não finito) nunca avisa: sem limite válido
 * não há corte pra detectar.
 */
export function kanbanPodeEstarIncompleto(qtdRecebida: number, limite: number): boolean {
  if (!Number.isFinite(limite) || limite <= 0) return false;
  return qtdRecebida >= limite;
}
