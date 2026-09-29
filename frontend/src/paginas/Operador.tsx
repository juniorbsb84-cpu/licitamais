import { useQuery } from "@tanstack/react-query";
import { api, ErroApi } from "../api/cliente";
import { useMe } from "../api/sessao";
import { data } from "../lib/formato";
import { Faixa } from "../componentes/Faixa";
import "./Operador.css";

const STATUS: Record<string, string> = {
  ok: "OK",
  partial: "Parcial",
  failed: "Falhou",
  ok_zero: "Sem novidades",
  suspect: "Suspeita",
  nunca_executado: "Nunca executado",
};

interface Fonte {
  fonte: string;
  status: string;
  inicio: string | null;
  respostas: number;
  novos: number;
  erro: string | null;
}

interface Incidente {
  id: number;
  fonte: string;
  tipo: string;
  severidade: string;
  aberto_em: string;
  mensagem: string;
}

export function Operador() {
  useMe();
  const saude = useQuery({
    queryKey: ["saude"],
    queryFn: () => api<{ fontes: Fonte[]; incidentes: Incidente[] }>("/operador/saude"),
    retry: false,
  });

  return (
    <>
    <Faixa titulo="Operador" apoio="Saúde das fontes na última coleta e incidentes que ainda pedem atenção." />
    <main className="container pagina-corpo" id="conteudo">
      {saude.isPending && <p>Carregando saúde das fontes…</p>}
      {saude.isError && (
        <p role="alert">{saude.error instanceof ErroApi && saude.error.status === 403
          ? "Esta página é só para o operador."
          : "Não foi possível carregar a saúde das fontes. Tente de novo."}</p>
      )}
      {saude.data && (
        <>
          <section className="bloco" aria-labelledby="t-fontes">
            <h2 className="bloco-titulo" id="t-fontes">Fontes</h2>
            <div className="tabela-wrap">
              <table className="tabela">
                <thead>
                  <tr>
                    <th scope="col">Fonte</th>
                    <th scope="col">Situação</th>
                    <th scope="col">Início</th>
                    <th scope="col" className="dir">Respostas</th>
                    <th scope="col" className="dir">Novos</th>
                  </tr>
                </thead>
                <tbody>
                  {saude.data.fontes.map((f) => (
                    <tr key={f.fonte}>
                      <td data-rot="Fonte" className="nome">{f.fonte}</td>
                      <td data-rot="Situação"><span>{STATUS[f.status] ?? f.status}</span>{f.erro ? ` (${f.erro})` : ""}</td>
                      <td data-rot="Início" className="num">{f.inicio ? data(f.inicio) : ""}</td>
                      <td data-rot="Respostas" className="num dir">{f.respostas}</td>
                      <td data-rot="Novos" className="num dir">{f.novos}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="bloco" aria-labelledby="t-inc">
            <h2 className="bloco-titulo" id="t-inc">Incidentes abertos</h2>
            {saude.data.incidentes.length === 0 ? (
              <p>Nenhum incidente aberto.</p>
            ) : (
              <ul className="incidentes">
                {saude.data.incidentes.map((i) => (
                  <li key={i.id}>
                    <strong>{i.fonte}</strong> {i.mensagem} <span className="num">{data(i.aberto_em)}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </main>
    </>
  );
}
