import type { components } from "../api/esquema";
import { data, descricaoItem } from "../lib/formato";

type Detalhe = components["schemas"]["LicitacaoDetalhe"];

function rotuloFonte(codigo: string): string {
  const mapa: Record<string, string> = {
    senac: "SENAC",
    brb: "BRB",
    sestsenat: "SEST/SENAT",
    sistema_industria: "Sistema Indústria",
    iges: "IGES",
    sescoop: "SESCOOP",
    caixa: "Caixa",
    bb: "Banco do Brasil",
    bbts: "BBTS",
  };
  return mapa[codigo] ?? codigo;
}

const MODALIDADES: [RegExp, string][] = [
  [/sele[cç][aã]o\s+disputa\s+aberta/i, "Seleção disputa aberta"],
  [/preg[aã]o\s+eletr[oô]nico/i, "Pregão eletrônico"],
  [/concorr[eê]ncia/i, "Concorrência"],
  [/dispensa/i, "Dispensa de licitação"],
  [/inexigibilidade/i, "Inexigibilidade"],
  [/leil[aã]o/i, "Leilão"],
  [/credenciamento/i, "Credenciamento"],
  [/convite/i, "Convite"],
];

export function rotuloModalidade(valor: string): string {
  for (const [re, rotulo] of MODALIDADES) {
    if (re.test(valor)) return rotulo;
  }
  return descricaoItem(valor);
}

export function Ficha({ licitacao }: { licitacao: Detalhe }) {
  const ausentes: string[] = [];
  if (!licitacao.publicacao) ausentes.push("publicação");
  if (!licitacao.criterio) ausentes.push("critério");
  if (!licitacao.homologacao) ausentes.push("homologação");
  return (
    <section className="bloco" aria-labelledby="t-ficha">
      <h2 className="bloco-titulo" id="t-ficha">Ficha do processo</h2>
      <dl className="ficha" data-testid="ficha">
        {licitacao.orgao && (
          <div className="ficha-cel"><dt>Órgão</dt><dd>{licitacao.orgao}</dd></div>
        )}
        <div className="ficha-cel"><dt>Fonte</dt><dd>{rotuloFonte(licitacao.fonte)}</dd></div>
        {licitacao.numero && (
          <div className="ficha-cel"><dt>Número</dt><dd className="num">{licitacao.numero}</dd></div>
        )}
        {licitacao.modalidade && (
          <div className="ficha-cel"><dt>Modalidade</dt><dd>{rotuloModalidade(licitacao.modalidade)}</dd></div>
        )}
        {licitacao.abertura && (
          <div className="ficha-cel"><dt>Abertura</dt><dd className="num">{data(licitacao.abertura)}</dd></div>
        )}
      </dl>
      {ausentes.length > 0 && (
        <p className="ficha-ausentes">A fonte não publica: {ausentes.join(", ")}.</p>
      )}
    </section>
  );
}

export function LinhaDoTempo({ fases }: { fases: Detalhe["fases"] }) {
  return (
    <section className="bloco" aria-labelledby="t-fases">
      <h2 className="bloco-titulo" id="t-fases">Linha do tempo</h2>
      <div className="timeline">
        {fases.length === 0 ? (
          <p>Nenhuma fase publicada pela fonte ainda.</p>
        ) : (
          <ol className="timeline-lista">
            {fases.map((f, i) => (
              <li className="fase" key={i}>
                {f.inicio && <span className="num fase-data">{data(f.inicio)}</span>}{" "}
                <span className="fase-rotulo">{f.rotulo ?? "Fase"}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
      <p className="timeline-dica">Com o alerta ativado em Acompanhar, cada fase nova chega direto no seu Telegram.</p>
    </section>
  );
}
