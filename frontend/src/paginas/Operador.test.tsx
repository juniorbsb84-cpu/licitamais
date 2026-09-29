import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Operador } from "./Operador";
import { servidor } from "../testes/servidor";

vi.mock("../api/sessao", () => ({
  useMe: () => ({ data: { id: 1, email: "a@b.com", operador: true, csrf: "x" } }),
}));

beforeAll(() => servidor.listen());
afterEach(() => { servidor.resetHandlers(); cleanup(); });
afterAll(() => servidor.close());

test("operador lista fontes em portugues e incidentes", async () => {
  servidor.use(http.get("/api/v2/operador/saude", () => HttpResponse.json({
    fontes: [
      { fonte: "brb", status: "ok", inicio: "2026-09-24T10:00:00Z", respostas: 10, novos: 2, erro: null },
      { fonte: "senac", status: "partial", inicio: null, respostas: 0, novos: 0, erro: null },
      { fonte: "iges", status: "failed", inicio: null, respostas: 0, novos: 0, erro: "timeout" },
      { fonte: "sestsenat", status: "ok_zero", inicio: null, respostas: 0, novos: 0, erro: null },
      { fonte: "sescoop", status: "suspect", inicio: null, respostas: 0, novos: 0, erro: null },
    ],
    incidentes: [{ id: 3, fonte: "brb", tipo: "coleta", severidade: "alta", aberto_em: "2026-09-24T09:00:00Z", mensagem: "queda" }],
  })));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/operador"]}>
        <Operador />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  for (const texto of ["OK", "Parcial", "Sem novidades", "Suspeita"]) {
    expect(await screen.findByText(texto, { exact: true })).toBeInTheDocument();
  }
  expect(await screen.findByText("Falhou", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("queda")).toBeInTheDocument();
});

test("sem permissao (403) explica em vez de mostrar tela vazia", async () => {
  servidor.use(http.get("/api/v2/operador/saude", () => HttpResponse.json({ erro: "acesso restrito ao operador" }, { status: 403 })));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/operador"]}><Operador /></MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Esta página é só para o operador.");
});
