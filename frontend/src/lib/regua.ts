export function posicaoLog(valor: number, min: number, max: number): number {
  if (!(min < max)) return 50;
  if (!(valor > 0)) return 0;
  const piso = min > 0 ? min : valor;
  if (!(piso < max)) return 50;
  const p = (Math.log10(valor) - Math.log10(piso)) / (Math.log10(max) - Math.log10(piso));
  return Math.min(100, Math.max(0, p * 100));
}
