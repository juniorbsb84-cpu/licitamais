import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Precos } from "./Precos";
import { servidor } from "../testes/servidor";

vi.mock("../api/sessao", () => ({
  useMe: () => ({ data: { id: 1, email: "a@b.com", operador: false, csrf: "x" } }),
}));

beforeAll(() => servidor.listen());
afterEach(() => { servidor.resetHandlers(); cleanup(); });
afterAll(() => servidor.close());

const RESPOSTA = {
  n: 10, min: 6020.0, q1: 8550.0, mediana: 83445.0, q3: 565000.0, max: 1433650.6,
  tipo: "item",
  pontos: [{
    data: null, valor: 495600.0, fornecedor: "POSITIVO TECNOLOGIA S.A. (MATRIZ 48)",
    cnpj: "81243735000148", fonte: "sistema_industria", processo_id: 2139,
  }],
  fornecedores: [],
  serie_ano: [],
};

function montar(rota = "/precos?q=limpeza&tipo=item") {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[rota]}>
        <Routes><Route path="/precos" element={<div><Precos /></div>} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("busca limpeza mostra mediana e n", async () => {
  servidor.use(
    http.get("/api/v2/precos", () => HttpResponse.json(RESPOSTA)),
    http.get("/api/v2/me", () => HttpResponse.json({ id: 1, email: "a@b.com", operador: false, csrf: "x" })),
  );
  montar();
  expect(await screen.findByTestId("precos-resumo")).toBeInTheDocument();
  expect(screen.getAllByText("R$ 83.445,00").length).toBeGreaterThan(0);
});

test("tipo Itens refaz com tipo=item", async () => {
  const tipos: string[] = [];
  servidor.use(http.get("/api/v2/precos", ({ request }) => {
    tipos.push(new URL(request.url).searchParams.get("tipo") ?? "");
    return HttpResponse.json(RESPOSTA);
  }));
  montar("/precos?q=limpeza&tipo=contrato");
  await screen.findByTestId("precos-resumo");
  const usuario = userEvent.setup();
  await usuario.click(screen.getByRole("radio", { name: "Itens" }));
  await vi.waitFor(() => expect(tipos).toContain("item"));
});

test("cada ponto tem link para a licitacao", async () => {
  servidor.use(http.get("/api/v2/precos", () => HttpResponse.json(RESPOSTA)));
  montar();
  const link = await screen.findByRole("link", { name: /Positivo Tecnologia/ });
  expect(link.getAttribute("href")).toBe("/licitacao/2139");
});

test("falha da API de precos mostra aviso", async () => {
  servidor.use(http.get("/api/v2/precos", () => HttpResponse.json({ erro: "falha" }, { status: 500 })));
  montar("/precos?q=limpeza");
  expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível buscar os preços. Tente de novo.");
});
