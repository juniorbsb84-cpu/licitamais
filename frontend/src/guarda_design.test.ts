// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";

function todosArquivos(dir: string, exts: string[]): string[] {
  const saida: string[] = [];
  for (const nome of readdirSync(dir)) {
    const caminho = join(dir, nome);
    if (statSync(caminho).isDirectory()) saida.push(...todosArquivos(caminho, exts));
    else if (exts.some((e) => caminho.endsWith(e))) saida.push(caminho);
  }
  return saida;
}

const RAIZ = join(process.cwd(), "src");
const ARQUIVOS = todosArquivos(RAIZ, [".tsx", ".css"]);
const TOKENS = ["#FFFFFF", "#F6F6F4", "#E7E4DD", "#1A1A1A", "#66625A", "#1F6B47", "#8A4B00", "#B3372B", "#D4AF37", "#7A5E12", "#A7A399", "#3A3935"];

test("guarda de design: sem vicios de IA nem cores fora dos tokens", () => {
  const violacoes: string[] = [];
  for (const arq of ARQUIVOS) {
    if (arq.includes(".test.")) continue;
    const texto = readFileSync(arq, "utf-8");
    const base = arq.split("\\").pop()?.split("/").pop() ?? arq;
    const checar = (re: RegExp, oque: string) => {
      if (re.test(texto)) violacoes.push(`${base}: ${oque}`);
    };
    checar(/[—–→]/, "travessao/en dash/seta");
    checar(/ · /, "meta com ponto medio");
    checar(/text-transform:\s*uppercase/, "caixa alta em CSS");
    checar(/linear-gradient|radial-gradient/, "gradiente");
    checar(/\p{Extended_Pictographic}/u, "emoji");
    checar(/dangerouslySetInnerHTML/, "dangerouslySetInnerHTML");
    if (base !== "tokens.css") {
      for (const m of texto.matchAll(/#[0-9A-Fa-f]{3,8}\b/g)) {
        const hex = m[0].toUpperCase();
        const normalizada = hex.length === 4
          ? `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}`
          : hex;
        if (!TOKENS.includes(normalizada) && !["#EFE6C8"].includes(normalizada)) {
          violacoes.push(`${base}: cor fora dos tokens (${m[0]})`);
        }
      }
    }
  }
  expect(violacoes).toEqual([]);
});

test("guarda de design: box-shadow so no cabecalho e na gaveta", () => {
  const violacoes: string[] = [];
  for (const arq of ARQUIVOS.filter((a) => a.endsWith(".css"))) {
    const base = arq.split("\\").pop()?.split("/").pop() ?? arq;
    if (base === "tokens.css" || base === "base.css") continue;
    const texto = readFileSync(arq, "utf-8");
    const linhas = texto.split("\n");
    for (let i = 0; i < linhas.length; i++) {
      const linha = linhas[i] ?? "";
      if (!linha.includes("box-shadow")) continue;
      const contexto = linhas.slice(Math.max(0, i - 6), i + 1).join("\n");
      const permitido = /\.filtros\.aberta/.test(contexto) || /inset 2px 0 0/.test(linha) || /inset 0 0 0 1px/.test(linha)
        || /box-shadow:\s*none/.test(linha) || /transition:[^;]*box-shadow/.test(linha);
      if (!permitido) violacoes.push(`${base}:${i + 1}: ${linha.trim()}`);
    }
  }
  expect(violacoes).toEqual([]);
});
