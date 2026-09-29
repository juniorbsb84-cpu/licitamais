import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { api, ErroApi } from "../api/cliente";
import "./Entrar.css";

export function Entrar() {
  const [params] = useSearchParams();
  const token = params.get("t");
  const [email, setEmail] = useState("");
  const [mensagem, setMensagem] = useState<string | null>(null);
  const [erroEnvio, setErroEnvio] = useState<string | null>(null);

  const pedido = useMutation({
    mutationFn: (destino: string) => api<{ mensagem: string }>("/auth/link", { metodo: "POST", corpo: { email: destino } }),
    onSuccess: (resposta) => { setMensagem(resposta.mensagem); setErroEnvio(null); },
    onError: (e) => {
      setErroEnvio(e instanceof ErroApi ? e.message : "Não foi possível pedir o link. Tente de novo.");
    },
  });

  useEffect(() => {
    // o e-mail de acesso aponta para /entrar?t= (endereco do site antigo); quem cria a sessao e a API
    if (token) location.replace(`/api/v2/auth/entrar?t=${encodeURIComponent(token)}`);
  }, [token]);

  return (
    <main className="entrar-pagina" id="conteudo">
      <section className="entrar-capa" aria-label="LicitamAIs">
        <p className="entrar-marca">Licitações do Sistema S, do BRB e das cooperativas</p>
        <p className="entrar-frase">O que o PNCP não mostra, com quem venceu e por quanto.</p>
      </section>
      <section className="entrar-lado">
      <h1>Entrar</h1>
      <p className="entrar-intro">Receba um link de acesso no seu e-mail. Sem senha: o link vale 15 minutos e entra direto.</p>
      {params.get("erro") === "link" && (
        <p className="entrar-erro" role="alert">Esse link já foi usado ou venceu. Peça outro.</p>
      )}
      <form
        className="entrar-form"
        onSubmit={(e) => { e.preventDefault(); pedido.mutate(email); }}
      >
        <label htmlFor="email">E-mail</label>
        <input
          id="email"
          className="campo"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <button className="btn btn--primario" type="submit" disabled={pedido.isPending}>
          Enviar link de acesso
        </button>
      </form>
      {mensagem && <p className="entrar-ok" role="status">{mensagem}</p>}
      {erroEnvio && <p className="entrar-erro" role="alert">{erroEnvio}</p>}
      </section>
    </main>
  );
}
