import { expect, test } from "vitest";
import { termoPreco } from "./termoPreco";

test("usa a busca que trouxe o usuario", () => {
  expect(termoPreco("lego", null, null)).toBe("lego");
});

test("usa a primeira palavra do item fora da lista vazia", () => {
  expect(termoPreco(null, "CONJUNTO LEGO TAPETES", "Necessária para viabilizar a compra")).toBe("lego");
});

test("usa a primeira palavra do objeto fora da lista vazia", () => {
  expect(termoPreco(null, null, "Necessária para viabilizar a execução da temporada")).toBe("temporada");
});

test("tudo vazio devolve null", () => {
  expect(termoPreco(null, null, null)).toBe(null);
  expect(termoPreco("", "   ", "para")).toBe(null);
});
