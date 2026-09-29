import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ErroApi } from "../api/cliente";
import { useMe } from "../api/sessao";
import { Faixa } from "../componentes/Faixa";
import "./Conta.css";

interface Alerta {
  id: number;
  palavras: string[];
  ativo: boolean;
  telegram_vinculado: boolean;
}

export function Conta() {
  useMe();
  const qc = useQueryClient();
  const [palavras, setPalavras] = useState("");
  const [codigo, setCodigo] = useState<string | null>(null);

  const alertasQ = useQuery({
    queryKey: ["alertas"],
    queryFn: () => api<Alerta[]>("/alertas"),
    retry: false,
  });

  const criar = useMutation({
    mutationFn: (lista: string[]) => api<Alerta>("/alertas", { metodo: "POST", corpo: { palavras: lista } }),
    onSuccess: (novo) => {
      setPalavras("");
      qc.setQueryData<Alerta[]>(["alertas"], (atual) => [...(atual ?? []), novo]);
    },
  });

  const apagar = useMutation({
    mutationFn: (aid: number) => api<void>(`/alertas/${aid}`, { metodo: "DELETE" }),
    onSuccess: (_, aid) => {
      qc.setQueryData<Alerta[]>(["alertas"], (atual) => (atual ?? []).filter((a) => a.id !== aid));
    },
  });

  const telegram = useMutation({
    mutationFn: () => api<{ codigo: string; validade_minutos: number }>("/conta/telegram", { metodo: "POST" }),
    onSuccess: (resposta) => setCodigo(resposta.codigo),
  });

  const sair = useMutation({
    mutationFn: () => api<void>("/auth/sair", { metodo: "POST" }),
    onSuccess: () => window.location.assign("/entrar"),
  });

  const alertas = alertasQ.data ?? [];

  return (
    <>
    <Faixa
      titulo="Conta e alertas"
      apoio="Cada alerta vigia palavras no objeto das licitações e avisa no Telegram quando surge processo novo ou muda uma fase."
    />
    <main className="container pagina-corpo" id="conteudo">
      <section className="bloco" aria-labelledby="t-lista">
        <h2 className="bloco-titulo" id="t-lista">Seus alertas</h2>
        {alertasQ.isError && <p role="alert">Não foi possível carregar seus alertas. Recarregue a página.</p>}
        {alertasQ.isSuccess && alertas.length === 0 && <p>Nenhum alerta ainda. Crie o primeiro abaixo.</p>}
        <ul className="alertas">
          {alertas.map((a) => (
            <li key={a.id}>
              <span className="alerta-palavras">{a.palavras.join(", ")}</span>
              <span className={a.telegram_vinculado ? "alerta-canal alerta-canal--ok" : "alerta-canal"}>
                {a.telegram_vinculado ? "Telegram vinculado" : "Telegram não vinculado"}
              </span>
              <button
                className="link-botao"
                type="button"
                onClick={() => {
                  if (window.confirm(`Apagar o alerta ${a.palavras.join(", ")}?`)) apagar.mutate(a.id);
                }}
              >
                Apagar
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section className="bloco" aria-labelledby="t-novo">
        <h2 className="bloco-titulo" id="t-novo">Novo alerta</h2>
        <form
          className="form-alerta"
          onSubmit={(e) => {
            e.preventDefault();
            const lista = palavras.split(",").map((p) => p.trim()).filter(Boolean);
            if (lista.length) criar.mutate(lista);
          }}
        >
          <label htmlFor="palavras">Palavras do alerta</label>
          <input
            id="palavras"
            className="campo"
            value={palavras}
            onChange={(e) => setPalavras(e.target.value)}
            placeholder="pneus, frota"
          />
          <button className="btn btn--primario" type="submit">Criar alerta</button>
          <p className="form-dica">Separe as palavras por vírgula. O alerta dispara quando qualquer uma aparece no objeto.</p>
        </form>
        {criar.isError && (
          <p role="alert">{criar.error instanceof ErroApi ? criar.error.message : "Não foi possível criar o alerta. Tente de novo."}</p>
        )}
      </section>
      <section className="bloco" aria-labelledby="t-telegram">
        <h2 className="bloco-titulo" id="t-telegram">Telegram</h2>
        <p className="bloco-sub">Gere um código e mande para o bot do LicitamAIs no Telegram. Os alertas passam a chegar nessa conversa.</p>
        <button className="btn btn--fantasma" type="button" onClick={() => telegram.mutate()}>
          Gerar código
        </button>
        {codigo && (
          <p className="codigo-telegram"><code>/vincular {codigo}</code> <span>Vale 10 minutos.</span></p>
        )}
      </section>
      <section className="bloco" aria-labelledby="t-sair">
        <h2 className="bloco-titulo" id="t-sair">Sair</h2>
        <p className="bloco-sub">Encerra a sessão neste navegador. Para voltar, peça um novo link de acesso.</p>
        <button className="link-botao" type="button" onClick={() => sair.mutate()}>
          Sair
        </button>
      </section>
    </main>
    </>
  );
}
