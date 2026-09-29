import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Rotas } from "./App";

vi.mock("./api/sessao", () => ({
  useMe: () => ({ data: { id: 1, email: "a@b.com", operador: true, csrf: "x" } }),
}));

afterEach(() => cleanup());

function montar(rota: string) {
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={[rota]}><Rotas /></MemoryRouter></QueryClientProvider>);
}

test("cabecalho tem os tres links do prototipo", () => {
  montar("/entrar");
  for (const nome of ["Oportunidades", "Preços", "Alertas"]) expect(screen.getByRole("link", { name: nome })).toBeInTheDocument();
});

test("link Operador aparece com me.operador", () => {
  montar("/entrar");
  expect(screen.getByRole("link", { name: "Operador" })).toBeInTheDocument();
});

test("rota desconhecida mostra pagina nao encontrada", () => {
  montar("/nao-existe");
  expect(screen.getByRole("heading", { name: "Página não encontrada" })).toBeInTheDocument();
});
