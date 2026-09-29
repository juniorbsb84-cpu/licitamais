import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Entrar } from "./Entrar";
import { servidor } from "../testes/servidor";

beforeAll(() => servidor.listen()); afterEach(() => servidor.resetHandlers()); afterAll(() => servidor.close());

function montar(rota: string) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[rota]}>
        <Routes><Route path="/entrar" element={<Entrar />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("pedir link mostra confirmação", async () => {
  servidor.use(http.post("/api/v2/auth/link", () => HttpResponse.json(
    { mensagem: "Se o e-mail for válido, o link chega em instantes. Ele vale 15 minutos." }, { status: 202 })));
  montar("/entrar");
  const usuario = userEvent.setup();
  await usuario.type(screen.getByLabelText("E-mail"), "a@b.com");
  await usuario.click(screen.getByRole("button", { name: "Enviar link de acesso" }));
  await screen.findByText("Se o e-mail for válido, o link chega em instantes. Ele vale 15 minutos.");
});

test("link vencido avisa", () => {
  montar("/entrar?erro=link");
  expect(screen.getByText("Esse link já foi usado ou venceu. Peça outro.")).toBeInTheDocument();
});

test("link do e-mail (/entrar?t=) segue para a API que cria a sessão", () => {
  const replace = vi.fn();
  vi.stubGlobal("location", { ...window.location, replace });
  montar("/entrar?t=abc-123_X");
  expect(replace).toHaveBeenCalledWith("/api/v2/auth/entrar?t=abc-123_X");
  vi.unstubAllGlobals();
});
