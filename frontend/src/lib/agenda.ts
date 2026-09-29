import type { components } from "../api/esquema";

type Resumo = components["schemas"]["LicitacaoResumo"];

export interface GrupoAgenda {
  chave: string;
  titulo: string;
  intervalo: string;
  itens: Resumo[];
}

function diaSaoPaulo(d: Date): Date {
  const fmt = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit" });
  const partes = fmt.format(d).split("-").map(Number);
  return new Date(Date.UTC((partes[0] as number), (partes[1] as number) - 1, (partes[2] as number)));
}

function segundaDaSemana(dia: Date): Date {
  const d = new Date(dia);
  const dow = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - dow);
  return d;
}

function parseData(abertura: string | null | undefined): Date | null {
  if (!abertura) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(abertura);
  if (!m) return null;
  const ano = Number(m[1]);
  if (ano < 1900 || ano > 2100) return null;
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
}

function fmtDiaMes(d: Date): string {
  return `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function agruparPorSemana(itens: Resumo[], hoje: Date): GrupoAgenda[] {
  const hojeSp = diaSaoPaulo(hoje);
  const seg = segundaDaSemana(hojeSp);
  const dom = new Date(seg); dom.setUTCDate(dom.getUTCDate() + 6);
  const proxSeg = new Date(seg); proxSeg.setUTCDate(proxSeg.getUTCDate() + 7);
  const proxDom = new Date(seg); proxDom.setUTCDate(proxDom.getUTCDate() + 13);

  const esta: Resumo[] = [];
  const proxima: Resumo[] = [];
  const adiante: Resumo[] = [];
  const semData: Resumo[] = [];
  const andamento: Resumo[] = [];
  const encerradas: Resumo[] = [];

  for (const it of itens) {
    const sit = it.situacao;
    if (sit === "andamento" || sit === "suspensa") { andamento.push(it); continue; }
    if (sit === "encerrada" || sit === "cancelada") { encerradas.push(it); continue; }
    if (sit === "desconhecida") { semData.push(it); continue; }
    const dt = parseData(it.abertura);
    if (!dt) { semData.push(it); continue; }
    if (dt >= seg && dt <= dom) esta.push(it);
    else if (dt >= proxSeg && dt <= proxDom) proxima.push(it);
    else adiante.push(it);
  }

  const grupos: GrupoAgenda[] = [];
  const intervaloEsta = `${String(seg.getUTCDate()).padStart(2, "0")} a ${fmtDiaMes(dom)}`;
  if (esta.length) grupos.push({ chave: "esta", titulo: "Esta semana", intervalo: intervaloEsta, itens: esta });
  if (proxima.length) {
    grupos.push({
      chave: "proxima", titulo: "Próxima semana",
      intervalo: `${fmtDiaMes(proxSeg)} a ${fmtDiaMes(proxDom)}`, itens: proxima,
    });
  }
  if (adiante.length) grupos.push({ chave: "adiante", titulo: "Mais adiante", intervalo: "a partir de " + fmtDiaMes(proxDom).slice(0, 5), itens: adiante });
  if (semData.length) grupos.push({ chave: "sem-data", titulo: "Sem data na fonte", intervalo: "", itens: semData });
  if (andamento.length) grupos.push({ chave: "andamento", titulo: "Em andamento", intervalo: "", itens: andamento });
  if (encerradas.length) grupos.push({ chave: "encerradas", titulo: "Encerradas e canceladas", intervalo: "", itens: encerradas });
  return grupos;
}
