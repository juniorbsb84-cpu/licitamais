import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/cliente";
import { useMe } from "../api/sessao";
import { data, nomeFornecedor, reais } from "../lib/formato";
import { Faixa } from "../componentes/Faixa";
import { Regua } from "../componentes/Regua";
import "./Precos.css";

interface Ponto {
  data: string | null;
  valor: number;
  fornecedor: string | null;
  cnpj: string | null;
  fonte: string;
  processo_id: number;
}

interface Resposta {
  n: number;
  min: number | null;
  q1: number | null;
  mediana: number | null;
  q3: number | null;
  max: number | null;
  tipo: string;
  pontos: Ponto[];
  fornecedores: { fornecedor: string; cnpj: string | null; vitorias: number }[];
}

const SUGESTOES = ["limpeza", "vigilância", "mobiliário", "impressão", "software", "refeição", "uniformes", "manutenção predial"];

export function Precos() {
  useMe();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const tipo = params.get("tipo") === "contrato" ? "contrato" : "item";
  const [busca, setBusca] = useState(q);
  const [todos, setTodos] = useState(false);

  const consulta = useQuery({
    queryKey: ["precos", q, tipo],
    queryFn: () => api<Resposta>("/precos", { params: { q, tipo } }),
    enabled: q.trim().length > 0,
    retry: false,
  });

  function buscar(termo: string) {
    const proximo = new URLSearchParams(params);
    if (termo.trim()) proximo.set("q", termo.trim());
    else proximo.delete("q");
    setParams(proximo, { replace: false });
  }

  function trocarTipo(novo: "item" | "contrato") {
    const proximo = new URLSearchParams(params);
    proximo.set("tipo", novo);
    setParams(proximo, { replace: false });
  }

  return (
    <>
    <Faixa
      titulo="Quanto já pagaram pelo que você vende."
      apoio="Valores adjudicados e contratos assinados nas entidades que acompanhamos. Busque pelo objeto, como aparece nos editais."
    >
      <form className="busca-preco" role="search" onSubmit={(e) => { e.preventDefault(); buscar(busca); }}>
        <input
          className="campo"
          type="search"
          name="q"
          placeholder="Buscar por objeto"
          aria-label="Buscar por objeto"
          value={busca}
          onChange={(e) => setBusca(e.target.value)}
        />
        <button className="btn btn--primario" type="submit">Buscar</button>
        <fieldset className="tipo-preco">
          <legend className="pular">Tipo de valor</legend>
          <label><input type="radio" name="tipo" value="item" checked={tipo === "item"} onChange={() => trocarTipo("item")} /> Itens</label>
          <label><input type="radio" name="tipo" value="contrato" checked={tipo === "contrato"} onChange={() => trocarTipo("contrato")} /> Contratos</label>
        </fieldset>
      </form>
    </Faixa>
    <main className="container pagina-corpo" id="conteudo">
      {!q && (
        <section className="bloco" aria-labelledby="t-sugestoes">
          <h2 className="bloco-titulo" id="t-sugestoes">Por onde começar</h2>
          <p className="bloco-sub">Termos que costumam ter bastante histórico. Escolha um para ver a faixa de preços e quem mais venceu.</p>
          <ul className="sugestoes">
            {SUGESTOES.map((t) => (
              <li key={t}><button type="button" className="sugestao" onClick={() => { setBusca(t); buscar(t); }}>{t}</button></li>
            ))}
          </ul>
        </section>
      )}
      {consulta.isPending && q && <p className="pagina-aviso">Carregando preços…</p>}
      {consulta.isError && <p className="pagina-aviso" role="alert">Não foi possível buscar os preços. Tente de novo.</p>}
      {consulta.data && consulta.data.n === 0 && <p className="pagina-aviso">Nenhum valor encontrado para essa busca. Tente um termo mais curto, como aparece no edital.</p>}
      {consulta.data && consulta.data.n > 0 && (
        <>
          <p className="pagina-aviso" data-testid="precos-resumo">{consulta.data.n} valores, mediana <span className="num">{reais(consulta.data.mediana ?? 0)}</span>.</p>
          <Regua termo={q} dados={consulta.data} />
          <section className="bloco" aria-labelledby="t-pontos">
            <h2 className="bloco-titulo" id="t-pontos">Valores encontrados</h2>
            <p className="bloco-sub">Do mais recente ao mais antigo. Cada linha abre o processo de origem.</p>
            <ul className="pontos">
              {(todos ? consulta.data.pontos : consulta.data.pontos.slice(0, 30)).map((p, i) => (
                <li key={i}>
                  <Link to={`/licitacao/${p.processo_id}`}>
                    <span className="ponto-nome">{nomeFornecedor(p.fornecedor)}</span>
                    <span className="ponto-data num">{p.data ? data(p.data) : ""}</span>
                    <span className="ponto-valor num">{reais(p.valor)}</span>
                  </Link>
                </li>
              ))}
            </ul>
            {!todos && consulta.data.pontos.length > 30 && (
              <button className="btn btn--fantasma mostrar-todos" type="button" onClick={() => setTodos(true)}>
                Mostrar todos os {consulta.data.pontos.length} valores
              </button>
            )}
          </section>
        </>
      )}
    </main>
    </>
  );
}
