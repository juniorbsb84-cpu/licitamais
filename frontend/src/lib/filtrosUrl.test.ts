import { expect, test } from "vitest";
import { escreverFiltros, lerFiltros } from "./filtrosUrl";

test("filtros invalidos sao normalizados", () => {
  const f = lerFiltros(new URLSearchParams("?situacao=aberta&situacao=xyz&pagina=-3&ordem=aleatoria"));
  expect(f).toMatchObject({ situacao: ["aberta"], pagina: 1, ordem: "prazo" });
});

test("ida e volta preserva valores validos", () => {
  const origem = "q=pneu&situacao=aberta&fonte=senac&ordem=abertura&pagina=2";
  const f = lerFiltros(new URLSearchParams(origem));
  expect(escreverFiltros(f).toString()).toBe(origem);
});

test("q com 150 caracteres e cortado em 100", () => {
  const f = lerFiltros(new URLSearchParams(`?q=${"x".repeat(150)}`));
  expect(f.q).toHaveLength(100);
});
