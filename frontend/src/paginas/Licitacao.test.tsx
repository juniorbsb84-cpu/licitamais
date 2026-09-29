import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Licitacao } from "./Licitacao";
import { servidor } from "../testes/servidor";
import detalhe from "../testes/dados/amostra_detalhe.json";
import precos from "../testes/dados/amostra_precos_lego.json";

vi.mock("../api/sessao", () => ({
  useMe: () => ({ data: { id: 1, email: "a@b.com", operador: false, csrf: "x" } }),
}));

beforeAll(() => servidor.listen());
afterEach(() => servidor.resetHandlers());
afterAll(() => servidor.close());

function padrao() {
  servidor.use(
    http.get("/api/v2/licitacoes/2139", () => HttpResponse.json(detalhe)),
    http.get("/api/v2/precos", () => HttpResponse.json(precos)),
    http.get("/api/v2/me", () => HttpResponse.json({ id: 1, email: "a@b.com", operador: false, csrf: "x" })),
  );
}

function montar(rota = "/licitacao/2139") {
  const id = `det-${Math.random().toString(36).slice(2)}`;
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[{ pathname: rota, state: { q: "lego" } }]}>
        <Routes><Route path="/licitacao/:id" element={<div data-testid={id}><Licitacao /></div>} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return screen.getByTestId(id);
}

afterEach(() => {
  servidor.resetHandlers();
  cleanup();
});

test("situacao e fonte na mesma linha", async () => {
  padrao();
  montar();
  const status = await screen.findByTestId("detalhe-status");
  const texto = (status.textContent ?? "").replace(/\s+/g, " ");
  expect(texto).toContain("Encerrada");
  expect(texto).toContain("(na fonte: Finalizado)");
});

test("ficha com 5 campos e linha de ausencias", async () => {
  padrao();
  const tela = montar();
  const ficha = await within(tela).findByTestId("ficha");
  for (const campo of ["Órgão", "Fonte", "Número", "Modalidade", "Abertura"]) {
    expect(within(ficha).getByText(campo, { exact: true })).toBeInTheDocument();
  }
  expect(within(tela).getByText("A fonte não publica: publicação, critério, homologação.")).toBeInTheDocument();
});

test("vencedores com nome limpo e total", async () => {
  padrao();
  const tela = montar();
  const tabela = await within(tela).findByTestId("vencedores");
  expect(within(tabela).getAllByText("Positivo Tecnologia S.A.").length).toBeGreaterThan(0);
  expect(tabela.textContent).not.toContain("(MATRIZ");
  expect(tabela.textContent).toContain("R$ 719.780,00");
});

test("regua com mediana verde e eixo minimo mediana maximo", async () => {
  padrao();
  const tela = montar();
  const regua = await within(tela).findByTestId("regua");
  expect(within(regua).getByRole("heading", { name: /Quanto já pagaram por itens parecidos \(lego\)/ })).toBeInTheDocument();
  const eixo = within(regua).getByTestId("regua-eixo");
  const rotulos = within(eixo).getAllByText(/R\$/).map((el) => el.textContent ?? "");
  expect(rotulos.join(" ")).toContain("R$ 6.020,00");
  expect(rotulos.join(" ")).toContain("R$ 83.445,00");
  expect(rotulos.join(" ")).toContain("R$ 1.433.650,60");
  expect(within(regua).getByText(/Metade dos valores ficou entre/).textContent).toContain("R$ 8.550,00");
  expect(within(regua).getByText(/Metade dos valores ficou entre/).textContent).toContain("R$ 565.000,00");
});

test("regua sem valores nao aparece", async () => {
  padrao();
  servidor.use(
    http.get("/api/v2/precos", () => HttpResponse.json({ n: 0, min: null, q1: null, mediana: null, q3: null, max: null, tipo: "item", pontos: [], fornecedores: [] })),
  );
  montar();
  await screen.findByTestId("ficha");
  await vi.waitFor(() => expect(screen.queryByTestId("regua")).not.toBeInTheDocument(), { timeout: 3000 });
});

test("regua com n igual a 1 mostra frase e esconde grafico", async () => {
  padrao();
  servidor.use(
    http.get("/api/v2/precos", () => HttpResponse.json({
      n: 1, min: 6020.0, q1: 6020.0, mediana: 6020.0, q3: 6020.0, max: 6020.0,
      tipo: "item", pontos: [], fornecedores: [],
    })),
  );
  const tela = montar();
  const regua = await within(tela).findByTestId("regua");
  expect(within(regua).getByText("Um valor encontrado: R$ 6.020,00")).toBeInTheDocument();
  expect(within(regua).queryByTestId("regua-grafico")).not.toBeInTheDocument();
});

test("acompanhar cria alerta e vira acompanhando", async () => {
  padrao();
  let corpo: unknown = null;
  servidor.use(http.post("/api/v2/alertas", async ({ request }) => {
    corpo = await request.json();
    return HttpResponse.json({ id: 9, palavras: ["lego"], ativo: true }, { status: 201 });
  }));
  const tela = montar();
  const usuario = userEvent.setup();
  const botao = await within(tela).findByRole("button", { name: "Acompanhar" });
  await usuario.click(botao);
  await within(tela).findByRole("button", { name: "Acompanhando" });
  expect(corpo).toEqual({ palavras: ["lego"] });
  expect(within(tela).getByRole("link", { name: "Gerenciar alertas" }).getAttribute("href")).toBe("/conta");
});

test("licitacao inexistente mostra nao encontrada", async () => {
  padrao();
  servidor.use(http.get("/api/v2/licitacoes/404", () => HttpResponse.json({ erro: "licitação não encontrada" }, { status: 404 })));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/licitacao/404"]}>
        <Routes><Route path="/licitacao/:id" element={<Licitacao />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Licitação não encontrada.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Voltar às oportunidades" }).getAttribute("href")).toBe("/");
});

test("acompanhar algo ja acompanhado (409) mostra acompanhando", async () => {
  padrao();
  servidor.use(http.post("/api/v2/alertas", () => HttpResponse.json({ erro: "Você já tem um alerta com essas palavras." }, { status: 409 })));
  const tela = montar();
  await userEvent.setup().click(await within(tela).findByRole("button", { name: "Acompanhar" }));
  await within(tela).findByRole("button", { name: "Acompanhando" });
});

test("limite de alertas (429) mostra a mensagem da API", async () => {
  padrao();
  servidor.use(http.post("/api/v2/alertas", () => HttpResponse.json({ erro: "Limite de 5 alertas ativos. Apague um para criar outro." }, { status: 429 })));
  const tela = montar();
  await userEvent.setup().click(await within(tela).findByRole("button", { name: "Acompanhar" }));
  expect(await within(tela).findByRole("alert")).toHaveTextContent("Limite de 5 alertas ativos. Apague um para criar outro.");
});

test("contratos publicados aparecem com fornecedor, vigência e total", async () => {
  servidor.use(
    http.get("/api/v2/licitacoes/2139", () => HttpResponse.json({
      ...detalhe,
      vencedores: [],
      contratos: [
        { numero: "450", fornecedor: "RIVERA MOVEIS LTDA", cnpj: "12345678000199", valor: 50000, assinatura: "2020-02-02", vigencia_inicio: "2020-02-02", vigencia_fim: "2021-02-01" },
        { numero: "451", fornecedor: "ALFA LTDA", cnpj: null, valor: 25000.5, assinatura: null, vigencia_inicio: null, vigencia_fim: null },
      ],
    })),
    http.get("/api/v2/precos", () => HttpResponse.json(precos)),
  );
  const tela = montar();
  const tabela = await within(tela).findByTestId("contratos");
  expect(within(tela).getByRole("heading", { name: "Contratos" })).toBeInTheDocument();
  expect(within(tabela).getByText("Rivera Moveis LTDA")).toBeInTheDocument();
  expect(within(tabela).getByText("12.345.678/0001-99")).toBeInTheDocument();
  expect(within(tabela).getByText("02/02/2020 a 01/02/2021")).toBeInTheDocument();
  expect((tabela.textContent ?? "").replace(/\s/g, " ")).toContain("R$ 75.000,50");
});

test("link de processo abre em nova aba", async () => {
  servidor.use(
    http.get("/api/v2/licitacoes/2139", () => HttpResponse.json({
      ...detalhe,
      link_origem: { url: "https://compras.sistemaindustria.com.br/x", tipo: "processo", rotulo: "Abrir o processo no portal da fonte" },
    })),
    http.get("/api/v2/precos", () => HttpResponse.json(precos)),
  );
  const tela = montar();
  const elo = await within(tela).findByRole("link", { name: "Abrir o processo no portal da fonte" });
  expect(elo.getAttribute("href")).toBe("https://compras.sistemaindustria.com.br/x");
  expect(elo.getAttribute("target")).toBe("_blank");
  expect(elo.getAttribute("rel")).toContain("noopener");
});

test("link de lista mostra a dica do numero", async () => {
  servidor.use(
    http.get("/api/v2/licitacoes/2139", () => HttpResponse.json({
      ...detalhe,
      numero: "001/2026",
      link_origem: { url: "https://transparencia.sestsenat.org.br/l", tipo: "lista", rotulo: "Abrir a lista de processos no portal da fonte" },
    })),
    http.get("/api/v2/precos", () => HttpResponse.json(precos)),
  );
  const tela = montar();
  await within(tela).findByRole("link", { name: "Abrir a lista de processos no portal da fonte" });
  expect(within(tela).getByText("Busque pelo número 001/2026.")).toBeInTheDocument();
});

test("sem link de origem nao renderiza nada", async () => {
  servidor.use(
    http.get("/api/v2/licitacoes/2139", () => HttpResponse.json({ ...detalhe, link_origem: null })),
    http.get("/api/v2/precos", () => HttpResponse.json(precos)),
  );
  const tela = montar();
  await within(tela).findByTestId("ficha");
  expect(within(tela).queryByRole("link", { name: /portal da fonte/ })).not.toBeInTheDocument();
});
