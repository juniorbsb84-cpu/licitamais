import { expect, test } from "vitest";
import { data, descricaoItem, nomeFornecedor, prazo, reais, tituloObjeto } from "./formato";

test.each([
  [0, { texto: "fecha hoje", urgente: true }], [1, { texto: "fecha amanhã", urgente: true }],
  [7, { texto: "fecha em 7 dias", urgente: true }], [8, { texto: "fecha em 8 dias", urgente: false }],
  [-1, null], [null, null],
])("prazo(%s)", (d, esperado) => expect(prazo(d as number | null)).toEqual(esperado));

test.each([
  ["O objeto da presente licitação é a contratação de empresa especializada em assessoria esportiva", "Assessoria esportiva"],
  ["O objeto desta seleção com disputa é contratação de empresa especializada em desenvolvimento de sistemas do SESI", "Desenvolvimento de sistemas do SESI"],
  ["CONTRATAÇÃO DE EMPRESA ESPECIALIZADA PARA MANUTENÇÃO DE FROTA DO SENAC/DF", "Manutenção de frota do SENAC/DF"],
  ["Registro de preços locação de ambulância.", "Registro de preços locação de ambulância."],
  [null, "Objeto não informado"],
])("tituloObjeto", (entrada, esperado) => expect(tituloObjeto(entrada as string | null)).toBe(esperado));

test("tituloObjeto corta em 90 na palavra", () => {
  const t = tituloObjeto("Aquisição de " + "materiais diversos ".repeat(10));
  expect(t.length).toBeLessThanOrEqual(91); expect(t.endsWith("…")).toBe(true); expect(t).not.toMatch(/ …$/);
});

test.each([
  ["POSITIVO TECNOLOGIA S.A. (MATRIZ 48)", "Positivo Tecnologia S.A."],
  ["RIVERA MOVEIS LTDA", "Rivera Moveis LTDA"],
  ["ACESSOLINE TELECOMUNICAÇÕES LTDA - FILIAL PR", "Acessoline Telecomunicações LTDA - Filial PR"],
  [null, "Fornecedor não informado"],
])("nomeFornecedor", (e, s) => expect(nomeFornecedor(e as string | null)).toBe(s));

test("reais e data", () => {
  expect(reais(1234.5)).toBe("R$ 1.234,50"); expect(data("2026-10-06T10:00:00Z")).toBe("06/10/2026"); expect(data(null)).toBe("");
});

test.each([
  ["CONJUNTO LEGO TAPETES", "Conjunto LEGO tapetes"],
  ["TROFEU LEGO FLL PEQUENO", "Trofeu LEGO FLL pequeno"],
  [null, "Item sem descrição"],
])("descricaoItem", (e, s) => expect(descricaoItem(e as string | null)).toBe(s));
