import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, ErroApi } from "../api/cliente";
import { useMe } from "../api/sessao";
import type { components } from "../api/esquema";
import { agruparPorSemana } from "../lib/agenda";
import { data, nomeFornecedor, prazo, reais, tituloObjeto } from "../lib/formato";
import { escreverFiltros, lerFiltros, type Filtros } from "../lib/filtrosUrl";
import "./Oportunidades.css";

type Resumo = components["schemas"]["LicitacaoResumo"];
type Pagina = components["schemas"]["PaginaLicitacoes"];
interface ResumoTopo {
  abertas: number; fechando_7_dias: number; total: number; fontes: number; ultima_coleta: string | null;
  prazos?: { data: string; qtd: number }[];
}
interface Evidencia {
  id: number; valor_estimado: number | null; parecidas: number; desde: number | null;
  valor_mediano: number | null; desconto_medio: number | null; mais_venceu: string | null;
}

const DIAS_SEMANA = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];
const MESES_LONGOS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const SEMANA_LONGA = ["Domingo", "Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado"];

function dataLonga(iso: string | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ?? "");
  if (!m) return "";
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return `${SEMANA_LONGA[d.getUTCDay()]}, ${Number(m[3])} de ${MESES_LONGOS[Number(m[2]) - 1]} de ${m[1]}`;
}

function Regua({ prazos }: { prazos: { data: string; qtd: number }[] }) {
  const total = prazos.reduce((s, p) => s + p.qtd, 0);
  return (
    <figure className="regua-prazos">
      <figcaption>
        <span>Prazos de abertura, próximos 14 dias</span>
        <span><b className="num">{total}</b> {total === 1 ? "abre" : "abrem"} nas próximas duas semanas</span>
      </figcaption>
      <ol className="dias">
        {prazos.map((p, i) => {
          const d = new Date(`${p.data}T12:00:00Z`);
          const dow = d.getUTCDay();
          const classe = ["dia", dow === 0 || dow === 6 ? "dia--fds" : "", i >= 7 ? "dia--depois" : ""].filter(Boolean).join(" ");
          return (
            <li className={classe} key={p.data} aria-label={`${d.getUTCDate()} de ${MESES_LONGOS[d.getUTCMonth()]}: ${p.qtd} ${p.qtd === 1 ? "abertura" : "aberturas"}`}>
              <span className="dia-n" aria-hidden="true">{d.getUTCDate()}</span>
              <span className="dia-s" aria-hidden="true">{DIAS_SEMANA[dow]}</span>
              {p.qtd > 0 && <span className="dia-qtd num" aria-hidden="true">{p.qtd}</span>}
              <span className="dia-marcas" aria-hidden="true">
                {Array.from({ length: Math.min(p.qtd, 9) }, (_, k) => <i key={k} />)}
              </span>
            </li>
          );
        })}
      </ol>
    </figure>
  );
}

function QuadroEvidencia({ ev }: { ev: Evidencia | undefined }) {
  if (!ev) return <aside className="evidencia" aria-hidden="true" />;
  if (!ev.parecidas && ev.valor_estimado === null) {
    return <aside className="evidencia"><p className="evidencia-vazio">Sem compra parecida no histórico.</p></aside>;
  }
  const principal = ev.valor_estimado ?? ev.valor_mediano;
  return (
    <aside className="evidencia" aria-label="Histórico de compras parecidas">
      {principal !== null && (
        <>
          <span className="evidencia-valor num">{reais(principal)}</span>
          <span className="evidencia-rotulo">{ev.valor_estimado !== null ? "valor estimado no edital" : ev.parecidas === 1 ? "único preço pago em compra parecida" : "mediana paga em compras parecidas"}</span>
        </>
      )}
      {ev.parecidas > 0 && (
        <dl>
          <dt>Preços pagos</dt><dd className="num">{ev.parecidas}{ev.desde ? ` desde ${ev.desde}` : ""}</dd>
          {ev.desconto_medio !== null && <><dt>Desconto médio</dt><dd className="num">{(ev.desconto_medio * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%</dd></>}
          {ev.mais_venceu && <><dt>Mais venceu</dt><dd>{nomeFornecedor(ev.mais_venceu)}</dd></>}
        </dl>
      )}
    </aside>
  );
}

const SITUACOES: { valor: Filtros["situacao"][number]; rotulo: string }[] = [
  { valor: "aberta", rotulo: "Aberta" },
  { valor: "andamento", rotulo: "Em andamento" },
  { valor: "encerrada", rotulo: "Encerrada" },
  { valor: "cancelada", rotulo: "Cancelada" },
];

const SIT_ROTULO: Record<string, string> = {
  aberta: "Aberta",
  andamento: "Em andamento",
  suspensa: "Suspensa",
  encerrada: "Encerrada",
  cancelada: "Cancelada",
  desconhecida: "Situação desconhecida",
};

const FONTES: { valor: string; rotulo: string }[] = [
  { valor: "senac", rotulo: "SENAC" },
  { valor: "brb", rotulo: "BRB" },
  { valor: "sestsenat", rotulo: "SEST/SENAT" },
  { valor: "sistema_industria", rotulo: "Sistema Indústria" },
  { valor: "iges", rotulo: "IGES" },
  { valor: "sescoop", rotulo: "SESCOOP" },
  { valor: "caixa", rotulo: "Caixa" },
  { valor: "bb", rotulo: "Banco do Brasil" },
  { valor: "bbts", rotulo: "BBTS" },
];

function nomeFonte(codigo: string): string {
  return FONTES.find((f) => f.valor === codigo)?.rotulo ?? codigo;
}

function haQuantoTempo(iso: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const min = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (min < 60) return `Atualizado há ${min} min, `;
  const h = Math.round(min / 60);
  return `Atualizado há ${h} h, `;
}

function prazoMargem(item: Resumo): { dia: string; mes: string; conta: string; curto: boolean } | null {
  const p = prazo(item.dias_para_abertura ?? null);
  const ab = item.abertura ? /^(\d{4})-(\d{2})-(\d{2})/.exec(item.abertura) : null;
  const meses = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  if (p && ab) {
    const ano = Number(ab[1]);
    const agora = new Date().getFullYear();
    const mes = meses[Number(ab[2]) - 1] ?? "";
    return { dia: String(Number(ab[3])), mes: ano !== agora ? `${mes} ${String(ano).slice(2)}` : (mes as string), conta: p.texto, curto: p.urgente };
  }
  if (item.dias_para_abertura !== null && item.dias_para_abertura !== undefined && (item.dias_para_abertura as number) < 0 && ab) {
    const dias = -(item.dias_para_abertura as number);
    const mes = meses[Number(ab[2]) - 1] ?? "";
    const texto = dias === 1 ? "abriu ontem" : `abriu há ${dias} dias`;
    return { dia: String(Number(ab[3])), mes: (mes as string), conta: texto, curto: false };
  }
  if (ab) {
    const mes = meses[Number(ab[2]) - 1] ?? "";
    return { dia: String(Number(ab[3])), mes: (mes as string), conta: `abertura em ${data(item.abertura)}`, curto: false };
  }
  return null;
}

function Linha({ item, busca, ev }: { item: Resumo; busca: string; ev: Evidencia | undefined }) {
  const margem = prazoMargem(item);
  const p = prazo(item.dias_para_abertura ?? null);
  const encerrada = item.situacao === "encerrada" || item.situacao === "cancelada";
  return (
    <Link
      className={encerrada ? "linha linha--encerrada" : "linha"}
      to={`/licitacao/${item.id}`}
      state={busca ? { q: busca } : undefined}
    >
      <div className="prazo">
        {margem ? (
          <>
            <span className="prazo-data"><b>{margem.dia}</b> <i>{margem.mes}</i></span>
            <span className={margem.curto ? "prazo-conta prazo-conta--curto" : "prazo-conta"}>{margem.conta}</span>
          </>
        ) : (
          <span className="prazo-conta prazo-conta--sem">Sem data na fonte</span>
        )}
      </div>
      <div className="linha-corpo">
        <h3 className="linha-objeto">{tituloObjeto(item.objeto)}</h3>
        <p className="linha-meta">
          <span>{nomeFonte(item.fonte)}</span>
          {item.orgao && <span className="meta-orgao">{item.orgao}</span>}
          {item.modalidade && <span>{item.modalidade}</span>}
          {item.numero && <span className="meta-numero">{item.numero}</span>}
        </p>
      </div>
      <div className="linha-sit">
        <span className={`sit sit--${item.situacao}`}>{SIT_ROTULO[item.situacao] ?? item.situacao}</span>
        {item.rotulo && <span className="sit-fonte">{item.rotulo}</span>}
        {p === null && item.situacao === "aberta" && !margem && <span className="sit-fonte">Sem prazo na fonte</span>}
      </div>
      <QuadroEvidencia ev={ev} />
    </Link>
  );
}

export function Oportunidades() {
  useMe();
  const [params, setParams] = useSearchParams();
  const filtros = lerFiltros(params);
  const [buscaLocal, setBuscaLocal] = useState(filtros.q);
  const [gaveta, setGaveta] = useState(false);
  const abrirRef = useRef<HTMLButtonElement>(null);

  useEffect(() => { setBuscaLocal(filtros.q); }, [filtros.q]);

  useEffect(() => {
    if (buscaLocal === filtros.q) return;
    const t = setTimeout(() => {
      const proximo = { ...filtros, q: buscaLocal, pagina: 1 };
      setParams(escreverFiltros(proximo), { replace: false });
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buscaLocal]);

  useEffect(() => {
    if (!gaveta) return;
    const aoTecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setGaveta(false); abrirRef.current?.focus(); }
    };
    document.addEventListener("keydown", aoTecla);
    return () => document.removeEventListener("keydown", aoTecla);
  }, [gaveta]);

  const consulta = useQuery({
    queryKey: ["licitacoes", filtros.q, filtros.situacao, filtros.fonte, filtros.modalidade, filtros.abertura_de, filtros.abertura_ate, filtros.ordem, filtros.pagina],
    queryFn: () => api<Pagina>("/licitacoes", {
      params: {
        q: filtros.q || undefined,
        situacao: filtros.situacao.length ? filtros.situacao : undefined,
        fonte: filtros.fonte.length ? filtros.fonte : undefined,
        modalidade: filtros.modalidade,
        abertura_de: filtros.abertura_de,
        abertura_ate: filtros.abertura_ate,
        ordem: filtros.ordem,
        pagina: filtros.pagina,
      },
    }),
    placeholderData: keepPreviousData,
    retry: false,
  });

  const resumoQ = useQuery({
    queryKey: ["resumo"],
    queryFn: () => api<ResumoTopo>("/resumo"),
    staleTime: 60_000,
    retry: false,
  });

  function trocar(up: Partial<Filtros>) {
    setParams(escreverFiltros({ ...filtros, ...up, pagina: up.pagina ?? 1 }), { replace: false });
  }

  function alternarLista(chave: "situacao" | "fonte", valor: string) {
    const atual = filtros[chave] as string[];
    const tem = atual.includes(valor);
    trocar({ [chave]: tem ? atual.filter((v) => v !== valor) : [...atual, valor] } as Partial<Filtros>);
  }

  const idsPagina = consulta.data?.itens.map((i) => i.id) ?? [];
  const evidenciasQ = useQuery({
    queryKey: ["evidencias", idsPagina],
    queryFn: () => api<Evidencia[]>("/evidencias", { params: { ids: idsPagina.map(String) } }),
    enabled: idsPagina.length > 0,
    staleTime: 300_000,
    retry: false,
  });
  const evidencias = new Map((evidenciasQ.data ?? []).map((e) => [e.id, e]));

  const erro422 = consulta.error instanceof ErroApi && consulta.error.status === 422;
  const grupos = consulta.data ? agruparPorSemana(consulta.data.itens, new Date()) : [];
  const contSit = consulta.data?.facetas?.["situacao"] ?? {};
  const contMod = consulta.data?.facetas?.["modalidade"] ?? {};
  // modalidade vinda da URL fica visivel (e removivel) mesmo sem contagem na resposta
  const modalidades = [...Object.keys(contMod), ...(filtros.modalidade && !(filtros.modalidade in contMod) ? [filtros.modalidade] : [])];
  const contFonte = consulta.data?.facetas?.["fonte"] ?? {};
  const porPagina = consulta.data?.por_pagina ?? 20;
  const total = consulta.data?.total ?? 0;
  const inicio = total === 0 ? 0 : (filtros.pagina - 1) * porPagina + 1;
  const fim = Math.min(total, filtros.pagina * porPagina);
  const ultimaPagina = Math.max(1, Math.ceil(total / porPagina));

  return (
    <>
    <section className="edicao" aria-labelledby="titulo-edicao">
      <div className="container">
        <div className="edicao-topo">
          <div>
            <p className="edicao-data">{dataLonga(resumoQ.data?.prazos?.[0]?.data)}</p>
            <h1 id="titulo-edicao">
              {resumoQ.data
                ? `${resumoQ.data.abertas.toLocaleString("pt-BR")} licitações abertas onde o PNCP não chega.`
                : "Oportunidades"}
            </h1>
          </div>
          {resumoQ.data && (
            <p className="edicao-apoio">
              Sistema S, BRB e cooperativas publicam em portais próprios. Lemos as {resumoQ.data.fontes} fontes
              todo dia e guardamos quem venceu e por quanto em cada disputa encerrada.{" "}
              <strong>{resumoQ.data.fechando_7_dias} {resumoQ.data.fechando_7_dias === 1 ? "fecha" : "fecham"} nos próximos 7 dias.</strong>{" "}
              <span className="resumo-hora">{haQuantoTempo(resumoQ.data.ultima_coleta).replace(/, $/, ".")}</span>
            </p>
          )}
        </div>
        {resumoQ.data?.prazos && resumoQ.data.prazos.length > 0 && <Regua prazos={resumoQ.data.prazos} />}
      </div>
    </section>
    <main className="container" id="conteudo">

      <div className="layout">
        <aside className={gaveta ? "filtros aberta" : "filtros"} id="filtros" aria-label="Filtros">
          <button className="filtros-fechar" type="button" onClick={() => { setGaveta(false); abrirRef.current?.focus(); }}>
            Fechar
          </button>
          <fieldset className="filtro-grupo">
            <legend>Situação</legend>
            {SITUACOES.map((s) => (
              <label className="filtro-opcao" key={s.valor}>
                <input
                  type="checkbox"
                  name="situacao"
                  value={s.valor}
                  checked={filtros.situacao.includes(s.valor)}
                  onChange={() => alternarLista("situacao", s.valor)}
                />
                {s.rotulo} <span className="contagem">{contSit[s.valor] ?? 0}</span>
              </label>
            ))}
          </fieldset>
          <fieldset className="filtro-grupo">
            <legend>Fonte</legend>
            {FONTES.map((f) => (
              <label className="filtro-opcao" key={f.valor}>
                <input
                  type="checkbox"
                  name="fonte"
                  value={f.valor}
                  checked={filtros.fonte.includes(f.valor)}
                  onChange={() => alternarLista("fonte", f.valor)}
                />
                {f.rotulo} <span className="contagem">{contFonte[f.valor] ?? 0}</span>
              </label>
            ))}
          </fieldset>
          {modalidades.length > 0 && (
            <fieldset className="filtro-grupo">
              <legend>Modalidade</legend>
              <label className="filtro-opcao">
                <input type="radio" name="modalidade" checked={!filtros.modalidade} onChange={() => trocar({ modalidade: undefined })} />
                Todas
              </label>
              {modalidades.map((m) => (
                <label className="filtro-opcao" key={m}>
                  <input type="radio" name="modalidade" value={m} checked={filtros.modalidade === m} onChange={() => trocar({ modalidade: m })} />
                  {m} <span className="contagem">{contMod[m] ?? 0}</span>
                </label>
              ))}
            </fieldset>
          )}
          <div className="filtro-grupo">
            <span className="filtro-rotulo" id="rotulo-periodo">Período de abertura</span>
            <div className="filtro-periodo" role="group" aria-labelledby="rotulo-periodo">
              <label>De <input className="campo" type="date" name="abertura_de" value={filtros.abertura_de ?? ""} onChange={(e) => trocar({ abertura_de: e.target.value || undefined })} /></label>
              <label>Até <input className="campo" type="date" name="abertura_ate" value={filtros.abertura_ate || ""} onChange={(e) => trocar({ abertura_ate: e.target.value || undefined })} /></label>
            </div>
          </div>
          <button className="limpar" type="button" onClick={() => setParams(new URLSearchParams(), { replace: false })}>
            Limpar filtros
          </button>
        </aside>
        <div className="sombra" hidden={!gaveta} onClick={() => { setGaveta(false); abrirRef.current?.focus(); }} />

        <section aria-label="Lista de licitações">
          <form className="barra" role="search" onSubmit={(e) => e.preventDefault()}>
            <button
              className="btn btn--fantasma btn-filtrar"
              ref={abrirRef}
              type="button"
              aria-expanded={gaveta}
              aria-controls="filtros"
              onClick={() => setGaveta(true)}
            >
              Filtrar
            </button>
            <input
              className="campo"
              type="search"
              name="q"
              placeholder="Buscar por objeto, órgão ou número"
              aria-label="Buscar por objeto, órgão ou número"
              value={buscaLocal}
              onChange={(e) => setBuscaLocal(e.target.value)}
            />
            <button className="btn btn--primario" type="submit">Buscar</button>
            <div className="ordenar">
              <label htmlFor="ordem">Ordenar por</label>
              <select
                className="select"
                id="ordem"
                name="ordem"
                value={filtros.ordem}
                onChange={(e) => trocar({ ordem: e.target.value as Filtros["ordem"] })}
              >
                <option value="prazo">Prazo</option>
                <option value="abertura">Abertura mais recente</option>
                <option value="relevancia">Relevância</option>
              </select>
            </div>
          </form>

          {consulta.isPending && <p>Carregando licitações…</p>}
          {erro422 && (
            <div data-testid="erro-lista">
              <p role="alert">Algum filtro está inválido. Limpe os filtros e tente de novo.</p>
              <button className="btn btn--fantasma" type="button" onClick={() => setParams(new URLSearchParams(), { replace: false })}>
                Limpar filtros
              </button>
            </div>
          )}
          {consulta.error && !erro422 && <p role="alert">Não foi possível carregar a lista. Tente de novo.</p>}
          {consulta.data && consulta.data.itens.length === 0 && !consulta.error && (
            <div data-testid="vazio-lista">
              <p>Nenhuma licitação com esses filtros.</p>
              <button className="btn btn--fantasma" type="button" onClick={() => setParams(new URLSearchParams(), { replace: false })}>
                Limpar filtros
              </button>
            </div>
          )}
          {consulta.data && consulta.data.itens.length > 0 && (
            <div className="lista" id="lista">
              <div className="lista-cabeca" aria-hidden="true">
                <span>Prazo</span>
                <span>Processo</span>
                <span className="col-sit">Situação</span>
                <span>Histórico de compras parecidas</span>
              </div>
              {grupos.map((g) => (
                <div key={g.chave}>
                  <h2 className="grupo">
                    <span>{g.titulo}</span>
                    {g.intervalo && <span className="grupo-nota">{g.intervalo}</span>}
                    <span className="grupo-conta">{g.itens.length} {g.itens.length === 1 ? "processo" : "processos"}</span>
                  </h2>
                  {g.itens.map((it) => <Linha key={it.id} item={it} busca={filtros.q} ev={evidencias.get(it.id)} />)}
                </div>
              ))}
            </div>
          )}
          {consulta.data && (
            <nav className="paginacao" aria-label="Paginação">
              <span className="num">Mostrando {inicio} a {fim} de {total} processos</span>
              <div className="paginas">
                <button type="button" disabled={filtros.pagina <= 1} onClick={() => trocar({ pagina: filtros.pagina - 1 })} aria-disabled={filtros.pagina <= 1}>
                  Anterior
                </button>
                <span className="atual" aria-current="page">{filtros.pagina}</span>
                <button type="button" disabled={filtros.pagina >= ultimaPagina} onClick={() => trocar({ pagina: filtros.pagina + 1 })} aria-disabled={filtros.pagina >= ultimaPagina}>
                  Próxima
                </button>
              </div>
            </nav>
          )}
        </section>
      </div>
    </main>
    </>
  );
}
