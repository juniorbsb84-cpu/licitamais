import { posicaoLog } from "../lib/regua";
import { nomeFornecedor, reais } from "../lib/formato";

interface Ponto {
  data: string | null;
  valor: number;
  fornecedor: string | null;
  cnpj: string | null;
  fonte: string;
  processo_id: number;
}

interface FornecedorRegua {
  fornecedor: string;
  cnpj: string | null;
  vitorias: number;
}

interface ReguaDados {
  n: number;
  min: number | null;
  q1: number | null;
  mediana: number | null;
  q3: number | null;
  max: number | null;
  pontos: Ponto[];
  fornecedores: FornecedorRegua[];
}

function cnpj(valor: string | null): string {
  const d = (valor ?? "").replace(/\D/g, "");
  if (d.length !== 14) return valor ?? "";
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
}

export function Regua({ termo, dados }: { termo: string; dados: ReguaDados }) {
  if (dados.n === 0) return null;
  if (dados.n === 1) {
    return (
      <section className="bloco" aria-labelledby="t-regua" data-testid="regua">
        <h2 className="bloco-titulo" id="t-regua">Quanto já pagaram por itens parecidos ({termo})</h2>
        <p>Um valor encontrado: {reais(dados.min ?? 0)}</p>
      </section>
    );
  }
  const min = dados.min ?? 0;
  const max = dados.max ?? 0;
  const eixo = (v: number | null) => (v === null ? 0 : posicaoLog(v, min, max));
  const pontos = dados.pontos;
  const rotulos = pontos.filter((p) => p.processo_id !== undefined).length > 1 ? pontos : pontos;
  void rotulos;
  return (
    <section className="bloco" aria-labelledby="t-regua" data-testid="regua">
      <h2 className="bloco-titulo" id="t-regua">Quanto já pagaram por itens parecidos ({termo})</h2>
      <p className="bloco-sub">
        {dados.n} valores adjudicados por itens de &quot;{termo}&quot; no histórico, de{" "}
        <span className="num">{reais(min)}</span> a <span className="num">{reais(max)}</span>. Cada ponto é um valor; a faixa
        vai do 1º ao 3º quartil e o traço escuro marca a mediana. Eixo em escala logarítmica.
      </p>
      <div
        className="regua"
        role="img"
        aria-label={`Distribuição de ${dados.n} valores pagos por itens de ${termo} em escala logarítmica: mínimo ${reais(min)}; primeiro quartil ${reais(dados.q1 ?? 0)}; mediana ${reais(dados.mediana ?? 0)}; terceiro quartil ${reais(dados.q3 ?? 0)}; máximo ${reais(max)}.`}
      >
        <div className="regua-grafico" data-testid="regua-grafico">
          <div
            className="regua-caixa"
            style={{ left: `${eixo(dados.q1)}%`, width: `${Math.max(0, eixo(dados.q3) - eixo(dados.q1))}%` }}
          />
          <div className="regua-mediana" style={{ left: `${eixo(dados.mediana)}%` }} />
          {pontos.map((p, i) => (
            <span
              key={i}
              className="regua-ponto"
              style={{ left: `${posicaoLog(p.valor, min, max)}%` }}
              title={`${reais(p.valor)}: ${nomeFornecedor(p.fornecedor)}, processo ${p.processo_id}`}
            />
          ))}
        </div>
        <div className="regua-eixo" aria-hidden="true" data-testid="regua-eixo">
          <span className="rot-a rot-min">mín<i>{reais(min)}</i></span>
          <span className="rot-a rot-med" style={{ "--pos": `${eixo(dados.mediana)}%` } as React.CSSProperties}>
            mediana<i>{reais(dados.mediana ?? 0)}</i>
          </span>
          <span className="rot-a rot-max">máx<i>{reais(max)}</i></span>
        </div>
        <p className="regua-leitura">
          Metade dos valores ficou entre <span className="num">{reais(dados.q1 ?? 0)}</span> e{" "}
          <span className="num">{reais(dados.q3 ?? 0)}</span>. Use a mediana de{" "}
          <strong>{reais(dados.mediana ?? 0)}</strong> como referência ao montar sua proposta.
        </p>
      </div>
      {dados.fornecedores.length > 0 && (
        <div className="regua-fornecedores">
          <h3 className="regua-forn-titulo">Quem mais venceu itens &quot;{termo}&quot;</h3>
          <ul className="regua-forn-lista">
            {dados.fornecedores.map((f) => (
              <li key={`${f.fornecedor}-${f.cnpj}`}>
                <span className="forn-nome">{nomeFornecedor(f.fornecedor)}</span>
                <span className="forn-cnpj">{cnpj(f.cnpj)}</span>
                <span className="forn-vit">{f.vitorias} {f.vitorias === 1 ? "vitória" : "vitórias"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
