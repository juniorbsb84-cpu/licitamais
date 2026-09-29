import { expect, test } from "vitest";
import { posicaoLog } from "./regua";

test("posicaoLog nos extremos e no meio geometrico", () => {
  expect(posicaoLog(10, 10, 1000)).toBe(0); expect(posicaoLog(1000, 10, 1000)).toBe(100);
  expect(posicaoLog(100, 10, 1000)).toBeCloseTo(50);
});
test("valores iguais nao geram NaN", () => expect(posicaoLog(5, 5, 5)).toBe(50));
test("valor <= 0 vai para a borda", () => expect(posicaoLog(0, 10, 1000)).toBe(0));
