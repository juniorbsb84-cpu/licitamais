import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Rotas } from "./App";
import { servidor } from "./testes/servidor";

beforeAll(() => servidor.listen()); afterEach(() => { servidor.resetHandlers(); cleanup(); }); afterAll(() => servidor.close());

function montar(rota: string) {
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={[rota]}><Rotas /></MemoryRouter></QueryClientProvider>);
}

test("logado: cabeçalho não oferece Entrar", async () => {
  servidor.use(http.get("/api/v2/me", () => HttpResponse.json({ id: 1, email: "a@b.com", operador: false, csrf: "x" })));
  montar("/entrar");
  await screen.findByRole("link", { name: "Alertas" });
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.queryByRole("link", { name: "Entrar" })).not.toBeInTheDocument();
});

test("sem sessão: cabeçalho oferece Entrar", async () => {
  servidor.use(http.get("/api/v2/me", () => HttpResponse.json({ erro: "autenticação necessária" }, { status: 401 })));
  montar("/entrar");
  expect(await screen.findByRole("link", { name: "Entrar" })).toBeInTheDocument();
});

test("visitante (site aberto): vê Oportunidades sem ser mandado ao login e sem link Alertas", async () => {
  servidor.use(
    http.get("/api/v2/me", () => HttpResponse.json({ erro: "autenticação necessária" }, { status: 401 })),
    http.get("/api/v2/resumo", () => HttpResponse.json({ abertas: 3, fechando_7_dias: 1, total: 9, fontes: 2, ultima_coleta: null, prazos: [] })),
    http.get("/api/v2/licitacoes", () => HttpResponse.json({ itens: [], total: 0, pagina: 1, por_pagina: 20, facetas: {} })),
  );
  montar("/");
  expect(await screen.findByRole("heading", { level: 1, name: /3 licitações abertas/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Entrar" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Alertas" })).not.toBeInTheDocument();
});
