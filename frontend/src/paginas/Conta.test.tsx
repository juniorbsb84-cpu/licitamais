import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Conta } from "./Conta";
import { servidor } from "../testes/servidor";

vi.mock("../api/sessao", () => ({
  useMe: () => ({ data: { id: 1, email: "a@b.com", operador: false, csrf: "x" } }),
}));

beforeAll(() => servidor.listen());
afterEach(() => { servidor.resetHandlers(); cleanup(); vi.unstubAllGlobals(); });
afterAll(() => servidor.close());

function montar() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/conta"]}>
        <Routes><Route path="/conta" element={<Conta />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("lista alertas e cria com palavras separadas por virgula", async () => {
  let corpo: unknown = null;
  servidor.use(
    http.get("/api/v2/alertas", () => HttpResponse.json([{ id: 1, palavras: ["pneus"], ativo: true, telegram_vinculado: false }])),
    http.post("/api/v2/alertas", async ({ request }) => {
      corpo = await request.json();
      return HttpResponse.json({ id: 2, palavras: ["pneus", "frota"], ativo: true, telegram_vinculado: false }, { status: 201 });
    }),
    http.get("/api/v2/me", () => HttpResponse.json({ id: 1, email: "a@b.com", operador: false, csrf: "x" })),
  );
  montar();
  await screen.findByText("pneus");
  const usuario = userEvent.setup();
  await usuario.type(screen.getByLabelText("Palavras do alerta"), "pneus, frota");
  await usuario.click(screen.getByRole("button", { name: "Criar alerta" }));
  await screen.findByText("pneus, frota");
  expect(corpo).toEqual({ palavras: ["pneus", "frota"] });
});

test("apagar pede confirmacao e faz DELETE", async () => {
  let apagado = "";
  servidor.use(
    http.get("/api/v2/alertas", () => HttpResponse.json([{ id: 7, palavras: ["pneus", "frota"], ativo: true, telegram_vinculado: false }])),
    http.delete("/api/v2/alertas/7", ({ request }) => {
      apagado = new URL(request.url).pathname;
      return HttpResponse.json(null, { status: 204 });
    }),
  );
  montar();
  const usuario = userEvent.setup();
  await screen.findByText("pneus, frota");
  const confirmar = vi.spyOn(window, "confirm").mockReturnValue(true);
  await usuario.click(screen.getByRole("button", { name: /Apagar/ }));
  expect(confirmar).toHaveBeenCalledWith("Apagar o alerta pneus, frota?");
  expect(apagado).toBe("/api/v2/alertas/7");
});

test("gerar codigo mostra vincular e validade", async () => {
  servidor.use(
    http.get("/api/v2/alertas", () => HttpResponse.json([])),
    http.post("/api/v2/conta/telegram", () => HttpResponse.json({ codigo: "123456", validade_minutos: 10 })),
  );
  montar();
  const usuario = userEvent.setup();
  await usuario.click(await screen.findByRole("button", { name: "Gerar código" }));
  await screen.findByText("/vincular 123456");
  expect(screen.getByText("Vale 10 minutos.")).toBeInTheDocument();
});

test("sair faz POST e vai para entrar", async () => {
  let saiu = false;
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, pathname: "/conta", search: "", assign });
  servidor.use(
    http.get("/api/v2/alertas", () => HttpResponse.json([])),
    http.post("/api/v2/auth/sair", () => { saiu = true; return HttpResponse.json(null, { status: 204 }); }),
  );
  montar();
  const usuario = userEvent.setup();
  await usuario.click(await screen.findByRole("button", { name: "Sair" }));
  await vi.waitFor(() => expect(saiu).toBe(true));
  expect(assign).toHaveBeenCalledWith("/entrar");
});

test("erro ao criar alerta mostra a mensagem da API", async () => {
  servidor.use(
    http.get("/api/v2/alertas", () => HttpResponse.json([])),
    http.post("/api/v2/alertas", () => HttpResponse.json({ erro: "Limite de 5 alertas ativos. Apague um para criar outro." }, { status: 429 })),
    http.get("/api/v2/me", () => HttpResponse.json({ id: 1, email: "a@b.com", operador: false, csrf: "x" })),
  );
  montar();
  const usuario = userEvent.setup();
  await usuario.type(await screen.findByLabelText("Palavras do alerta"), "pneus");
  await usuario.click(screen.getByRole("button", { name: "Criar alerta" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Limite de 5 alertas ativos. Apague um para criar outro.");
});

test("falha ao carregar alertas nao diz que nao ha alertas", async () => {
  servidor.use(
    http.get("/api/v2/alertas", () => HttpResponse.json({ erro: "falha" }, { status: 500 })),
    http.get("/api/v2/me", () => HttpResponse.json({ id: 1, email: "a@b.com", operador: false, csrf: "x" })),
  );
  montar();
  expect(await screen.findByRole("alert")).toHaveTextContent("Não foi possível carregar seus alertas.");
  expect(screen.queryByText(/Nenhum alerta ainda/)).not.toBeInTheDocument();
});
