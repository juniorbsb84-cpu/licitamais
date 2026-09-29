import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Licitacao } from "./Licitacao";
import { servidor } from "../testes/servidor";
import detalhe from "../testes/dados/amostra_detalhe.json";

vi.mock("../api/sessao", () => ({ useMe: () => ({ data: undefined, isError: true }) }));

beforeAll(() => servidor.listen());
afterEach(() => servidor.resetHandlers());
afterAll(() => servidor.close());

test("visitante na Licitação vê convite para entrar em vez de Acompanhar", async () => {
  servidor.use(
    http.get("/api/v2/licitacoes/2139", () => HttpResponse.json(detalhe)),
    http.get("/api/v2/precos", () => HttpResponse.json({ n: 0, pontos: [], fornecedores: [] })),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/licitacao/2139"]}>
        <Routes><Route path="/licitacao/:id" element={<Licitacao />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const link = await screen.findByRole("link", { name: "Entre para acompanhar" });
  expect(link.getAttribute("href")).toBe("/entrar");
  expect(screen.queryByRole("button", { name: "Acompanhar" })).not.toBeInTheDocument();
});
