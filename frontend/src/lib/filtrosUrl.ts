import type { components } from "../api/esquema";

export type Situacao = components["schemas"]["LicitacaoResumo"]["situacao"];
export type Ordem = "prazo" | "relevancia" | "abertura";

export interface Filtros {
  q: string;
  situacao: Situacao[];
  fonte: string[];
  modalidade?: string;
  abertura_de?: string;
  abertura_ate?: string;
  ordem: Ordem;
  pagina: number;
}

const SITUACOES: Situacao[] = ["aberta", "andamento", "suspensa", "encerrada", "cancelada", "desconhecida"];
const ORDENS: Ordem[] = ["prazo", "relevancia", "abertura"];
const DATA = /^\d{4}-\d{2}-\d{2}$/;

function lista(params: URLSearchParams, chave: string): string[] {
  return params.getAll(chave).filter((v) => v !== "");
}

export function lerFiltros(busca: URLSearchParams): Filtros {
  const q = (busca.get("q") ?? "").slice(0, 100);
  const situacao = lista(busca, "situacao").filter((s): s is Situacao => (SITUACOES as string[]).includes(s));
  const fonte = lista(busca, "fonte").slice(0, 10);
  const modalidadeCrua = busca.get("modalidade");
  const modalidade = modalidadeCrua ? modalidadeCrua : undefined;
  const deCrua = busca.get("abertura_de");
  const abertura_de = deCrua && DATA.test(deCrua) ? deCrua : undefined;
  const ateCrua = busca.get("abertura_ate");
  const abertura_ate = ateCrua && DATA.test(ateCrua) ? ateCrua : undefined;
  const ordemCrua = busca.get("ordem");
  const ordem: Ordem = ordemCrua && (ORDENS as string[]).includes(ordemCrua) ? (ordemCrua as Ordem) : "prazo";
  const paginaCrua = Number(busca.get("pagina"));
  const pagina = Number.isInteger(paginaCrua) && paginaCrua >= 1 ? paginaCrua : 1;
  return { q, situacao, fonte, modalidade, abertura_de, abertura_ate, ordem, pagina };
}

export function escreverFiltros(f: Filtros): URLSearchParams {
  const params = new URLSearchParams();
  if (f.q) params.set("q", f.q);
  for (const s of f.situacao) params.append("situacao", s);
  for (const v of f.fonte) params.append("fonte", v);
  if (f.modalidade) params.set("modalidade", f.modalidade);
  if (f.abertura_de) params.set("abertura_de", f.abertura_de);
  if (f.abertura_ate) params.set("abertura_ate", f.abertura_ate);
  if (f.ordem !== "prazo") params.set("ordem", f.ordem);
  if (f.pagina !== 1) params.set("pagina", String(f.pagina));
  return params;
}
