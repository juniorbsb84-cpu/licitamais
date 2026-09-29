import { useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ErroApi } from "../api/cliente";
import { useMe } from "../api/sessao";
import type { components } from "../api/esquema";
import { data, tituloObjeto } from "../lib/formato";
import { termoPreco } from "../lib/termoPreco";
import { Ficha, LinhaDoTempo } from "../componentes/Ficha";
import { TabelaValores } from "../componentes/TabelaValores";
import { Regua } from "../componentes/Regua";
import "./Licitacao.css";

type Detalhe = components["schemas"]["LicitacaoDetalhe"];

interface ReguaResposta {
  n: number;
  min: number | null;
  q1: number | null;
  mediana: number | null;
  q3: number | null;
  max: number | null;
  pontos: {
    data: string | null;
    valor: number;
    fornecedor: string | null;
    cnpj: string | null;
    fonte: string;
    processo_id: number;
  }[];
  fornecedores: { fornecedor: string; cnpj: string | null; vitorias: number }[];
}

const SIT_ROTULO: Record<string, string> = {
  aberta: "Aberta",
  andamento: "Em andamento",
  suspensa: "Suspensa",
  encerrada: "Encerrada",
  cancelada: "Cancelada",
  desconhecida: "Situação desconhecida",
};

export function Licitacao() {
  const me = useMe();
  const { id } = useParams();
  const location = useLocation();
  const buscaLista = (location.state as { q?: string } | null)?.q ?? null;
  const [acompanhando, setAcompanhando] = useState(false);

  const detalheQ = useQuery({
    queryKey: ["licitacao", id],
    queryFn: () => api<Detalhe>(`/licitacoes/${id}`),
    retry: false,
  });

  const det = detalheQ.data;
  const termo = det ? termoPreco(buscaLista, det.itens[0]?.descricao ?? null, det.objeto) : null;
  const tipoRegua = det && det.itens.length > 0 ? "item" : "contrato";

  const reguaQ = useQuery({
    queryKey: ["precos", termo, tipoRegua],
    queryFn: () => api<ReguaResposta>("/precos", { params: { q: termo as string, tipo: tipoRegua } }),
    enabled: termo !== null,
    retry: false,
  });

  const alerta = useMutation({
    mutationFn: (palavras: string[]) => api("/alertas", { metodo: "POST", corpo: { palavras } }),
    onSuccess: () => setAcompanhando(true),
    // 409 = ja existe alerta com esse termo: para quem clicou, o resultado e o mesmo
    onError: (e) => { if (e instanceof ErroApi && e.status === 409) setAcompanhando(true); },
  });

  if (detalheQ.isPending) {
    return <main className="container"><p>Carregando licitação…</p></main>;
  }
  if (detalheQ.error instanceof ErroApi && detalheQ.error.status === 404) {
    return (
      <main className="container">
        <p>Licitação não encontrada.</p>
        <p><Link to="/">Voltar às oportunidades</Link></p>
      </main>
    );
  }
  if (detalheQ.error || !det) {
    return <main className="container"><p role="alert">Não foi possível carregar a licitação. Tente de novo.</p></main>;
  }

  const dias = det.dias_para_abertura;
  const haDias = dias !== null && dias !== undefined && dias < 0 ? -dias : null;
  const prazoTexto = haDias === null
    ? (det.abertura ? `Abertura em ${data(det.abertura)}` : "Sem data de abertura na fonte")
    : `Abertura em ${data(det.abertura)}, há ${haDias} ${haDias === 1 ? "dia" : "dias"}`;

  return (
    <>
    <div className="detalhe-noite">
    <div className="container">
      <nav className="migalha" aria-label="Você está em">
        <Link to="/">Oportunidades</Link>
        <span aria-hidden="true">/</span>
        <span>Processo <span className="num">{det.numero ?? det.id}</span>{det.orgao ? `, ${det.orgao}` : ""}</span>
      </nav>
      <div className="detalhe-cabeca">
        <div>
          <p className="detalhe-status" data-testid="detalhe-status">
            <span className={`sit sit--${det.situacao}`}>{SIT_ROTULO[det.situacao] ?? det.situacao}</span>
            {det.rotulo && <span className="sit-fonte">(na fonte: {det.rotulo})</span>}
            <span className="detalhe-prazo">{prazoTexto}</span>
          </p>
          <h1>{tituloObjeto(det.objeto)}</h1>
          {det.objeto && det.objeto.trim().toLocaleLowerCase("pt-BR") !== tituloObjeto(det.objeto).toLocaleLowerCase("pt-BR") && (
            <p className="detalhe-objeto">{det.objeto}</p>
          )}
        </div>
        <div className="acao">
          {!me.data ? (
            <>
              <Link
                className="btn btn--primario"
                to="/entrar"
                onClick={() => { try { sessionStorage.setItem("volta", location.pathname); } catch { /* sem storage */ } }}
              >
                Entre para acompanhar
              </Link>
              <p className="acompanhar-nota">Com uma conta gratuita, você recebe no Telegram cada novidade deste processo.</p>
            </>
          ) : acompanhando ? (
            <>
              <button className="btn btn--primario" type="button" aria-pressed="true" disabled>Acompanhando</button>
              <p className="acompanhar-nota">Alerta criado. Você recebe no Telegram cada nova fase deste processo.</p>
              <p><Link to="/conta">Gerenciar alertas</Link></p>
            </>
          ) : (
            <>
              <button
                className="btn btn--primario"
                type="button"
                aria-pressed="false"
                disabled={termo === null || alerta.isPending}
                onClick={() => { if (termo) alerta.mutate([termo]); }}
              >
                Acompanhar
              </button>
              <p className="acompanhar-nota">Crie um alerta e receba no Telegram cada novidade deste processo.</p>
              {alerta.isError && !(alerta.error instanceof ErroApi && alerta.error.status === 409) && (
                <p role="alert">{alerta.error instanceof ErroApi ? alerta.error.message : "Não foi possível criar o alerta. Tente de novo."}</p>
              )}
            </>
          )}
          {det.link_origem && (
            <>
              <a className="link-origem" href={det.link_origem.url} target="_blank" rel="noopener noreferrer">{det.link_origem.rotulo}</a>
              {det.link_origem.tipo === "lista" && det.numero && (
                <p className="link-origem-dica">Busque pelo número {det.numero}.</p>
              )}
            </>
          )}
        </div>
      </div>
      <Ficha licitacao={det} />
    </div>
    </div>
    <main className="container detalhe-corpo" id="conteudo">
      {termo && reguaQ.data && reguaQ.data.n > 0 && <Regua termo={termo} dados={reguaQ.data} />}
      <TabelaValores licitacao={det} />
      <LinhaDoTempo fases={det.fases} />
    </main>
    </>
  );
}
