import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

export const servidor = setupServer(
  http.all("/api/v2/*", () => HttpResponse.json({ erro: "sem mock para esta rota" }, { status: 500 })),
);
