import { expect, test } from "vitest";
import { agruparPorSemana } from "./agenda";
import type { components } from "../api/esquema";

type Resumo = components["schemas"]["LicitacaoResumo"];

test("agrupa por semana na ordem da agenda", () => {
  const hoje = new Date("2026-09-24T12:00:00Z");
  const it = (id: number, situacao: string, abertura: string | null) => ({ id, situacao, abertura }) as Resumo;
  const grupos = agruparPorSemana([
    it(1, "aberta", "2026-09-24"), it(2, "aberta", "2026-09-29"), it(3, "aberta", "2026-10-20"),
    it(4, "aberta", null), it(5, "andamento", "2026-09-01"), it(6, "encerrada", "2026-01-01"),
  ], hoje);
  expect(grupos.map(g => [g.titulo, g.itens.map(i => i.id)])).toEqual([
    ["Esta semana", [1]], ["Próxima semana", [2]], ["Mais adiante", [3]], ["Sem data na fonte", [4]],
    ["Em andamento", [5]], ["Encerradas e canceladas", [6]],
  ]);
  expect(grupos[0]!.intervalo).toBe("21 a 27/09");
});
