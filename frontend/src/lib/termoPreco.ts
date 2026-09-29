const PALAVRAS_VAZIAS = new Set([
  "contratação", "contratacao", "empresa", "especializada", "prestação", "prestacao",
  "serviços", "servicos", "serviço", "servico", "aquisição", "aquisicao", "fornecimento",
  "registro", "preços", "precos", "necessária", "necessaria", "viabilizar", "execução",
  "execucao", "objeto", "presente", "licitação", "licitacao", "conjunto", "unidade",
  "material", "materiais", "eventual", "para",
]);

function palavras(texto: string): string[] {
  return (texto.toLocaleLowerCase("pt-BR").match(/[a-zà-öø-ÿ0-9/]+/g) ?? []);
}

export function termoPreco(
  buscaDaLista: string | null,
  primeiroItem: string | null,
  objeto: string | null,
): string | null {
  const busca = (buscaDaLista ?? "").trim().toLocaleLowerCase("pt-BR");
  if (busca) return busca;
  if (primeiroItem) {
    const p = palavras(primeiroItem).find((w) => w.length >= 4 && !PALAVRAS_VAZIAS.has(w));
    if (p) return p;
  }
  if (objeto) {
    const p = palavras(objeto).find((w) => w.length >= 5 && !PALAVRAS_VAZIAS.has(w));
    if (p) return p;
  }
  return null;
}
