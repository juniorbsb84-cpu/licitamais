import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useNavigate } from "react-router";
import { useMe } from "./api/sessao";
import { destinoVolta } from "./api/cliente";
import { Entrar } from "./paginas/Entrar";
import { Licitacao } from "./paginas/Licitacao";
import { Precos } from "./paginas/Precos";
import { Conta } from "./paginas/Conta";
import { Operador } from "./paginas/Operador";
import { Oportunidades } from "./paginas/Oportunidades";
import "./estilo/tokens.css";
import "./estilo/base.css";

function Layout({ children }: { children: React.ReactNode }) {
  const [aberto, setAberto] = useState(false);
  const me = useMe();
  const navegar = useNavigate();
  useEffect(() => {
    if (!me.data) return;
    let volta: string | null = null;
    try { volta = destinoVolta(sessionStorage.getItem("volta")); sessionStorage.removeItem("volta"); } catch { /* sem storage */ }
    if (volta) navegar(volta, { replace: true });
  }, [me.data, navegar]);
  return (
    <>
      <header className="topo">
        <div className="container topo-dentro">
          <Link className="marca" to="/" aria-label="LicitamAIs, página inicial"><strong>Licitam</strong><span>AIs</span></Link>
          <button
            className="btn btn--fantasma menu-btn"
            id="menu-btn"
            type="button"
            aria-expanded={aberto}
            aria-controls="menu-principal"
            onClick={() => setAberto((v) => !v)}
          >
            Menu
          </button>
          <nav id="menu-principal" aria-label="Principal" className={aberto ? "aberto" : undefined}>
            <ul className="menu">
              <li><NavLink to="/" end>Oportunidades</NavLink></li>
              <li><NavLink to="/precos">Preços</NavLink></li>
              {me.data && <li><NavLink to="/conta">Alertas</NavLink></li>}
              {me.data?.operador && <li><NavLink to="/operador">Operador</NavLink></li>}
              {!me.data && <li><Link className="entrar" to="/entrar">Entrar</Link></li>}
            </ul>
          </nav>
        </div>
      </header>
      {children}
    </>
  );
}

function ExigeConta({ children }: { children: React.ReactNode }) {
  const me = useMe();
  if (me.isPending) return <main className="container"><p>Carregando…</p></main>;
  if (me.isError) {
    return <Navigate to="/entrar" replace />;
  }
  return <>{children}</>;
}

export function Rotas() {
  return (
    <Layout>
      <Routes>
        {/* leitura: com o site fechado, a API da propria pagina responde 401 e o cliente leva ao login */}
        <Route path="/" element={<Oportunidades />} />
        <Route path="/licitacao/:id" element={<Licitacao />} />
        <Route path="/precos" element={<Precos />} />
        <Route path="/conta" element={<ExigeConta><Conta /></ExigeConta>} />
        <Route path="/entrar" element={<Entrar />} />
        <Route path="/operador" element={<ExigeConta><Operador /></ExigeConta>} />
        <Route path="/api/*" element={<main className="container"><h1>Página não encontrada</h1></main>} />
        <Route path="*" element={<main className="container"><h1>Página não encontrada</h1></main>} />
      </Routes>
    </Layout>
  );
}

export default function App() {
  return <Rotas />;
}
