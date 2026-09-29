export function reais(v: number): string {
  return v
    .toLocaleString("pt-BR", { style: "currency", currency: "BRL", minimumFractionDigits: 2, maximumFractionDigits: 2 })
    .replace(/ /g, " ");
}

export function data(iso: string | null): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return "";
  return `${m[3]}/${m[2]}/${m[1]}`;
}

export function prazo(dias: number | null): { texto: string; urgente: boolean } | null {
  if (dias === null || dias === undefined || dias < 0) return null;
  if (dias === 0) return { texto: "fecha hoje", urgente: true };
  if (dias === 1) return { texto: "fecha amanhã", urgente: true };
  return { texto: `fecha em ${dias} dias`, urgente: dias <= 7 };
}

const SIGLAS = new Set([
  "SESI", "SENAI", "SENAC", "SESC", "SEST", "SENAT", "SESCOOP", "BRB", "IGES", "CNI", "DF", "UF",
  "CNPJ", "ARP", "DEX", "IEL", "NC", "CBF7", "LED", "PCI", "ABRAMAN", "CBMGA", "FLL", "LEGO",
]);

function ehSigla(palavra: string): boolean {
  const limpa = palavra.replace(/[.,;:!?()\[\]"']/g, "");
  if (SIGLAS.has(limpa.toUpperCase())) return true;
  if (/^[A-Z]{2,}$/.test(limpa)) return true;
  if (/^[A-Z]+\/[A-Z]+$/.test(limpa)) return true;
  const partes = limpa.split("/");
  if (partes.length === 2 && partes.every((p) => p !== undefined && /^[A-Z]{2,}$/.test(p as string))) return true;
  if (/^[A-Z]{2,}-\d+$/.test(limpa)) return true;
  return false;
}

function sentenceCase(texto: string): string {
  const letras = (texto.match(/[A-Za-zÀ-ÖØ-öø-ÿ]/g) ?? []).length;
  const maiusculas = (texto.match(/[A-ZÀ-Þ]/g) ?? []).length;
  const eCaixaAlta = letras > 0 && maiusculas / letras >= 0.7;
  const base = eCaixaAlta ? texto.toLocaleLowerCase("pt-BR") : texto;
  const palavras = base.split(/(\s+)/);
  const fora = new Set(["de", "da", "do", "das", "dos", "e", "em", "na", "no", "nas", "nos", "para", "por", "com", "sem", "sob", "a", "o", "as", "os", "ao", "à", "às", "dos"]);
  let primeiro = true;
  return palavras
    .map((p) => {
      if (/^\s+$/.test(p) || p === "") return p;
      if (/^[A-Za-zÀ-ÖØ-öø-ÿ]+\/[A-Za-zÀ-ÖØ-öø-ÿ]+$/.test(p)) {
        const [a, b] = p.split("/") as [string, string];
        const fa = (s: string) => (SIGLAS.has(s.toUpperCase()) ? s.toUpperCase() : s.charAt(0).toLocaleUpperCase("pt-BR") + s.slice(1).toLocaleLowerCase("pt-BR"));
        const saida = `${fa(a as string)}/${(b as string).toUpperCase()}`;
        primeiro = false;
        return saida;
      }
      if (ehSigla(p)) {
        primeiro = false;
        const limpaAlto = p.replace(/[.,]/g, "").toUpperCase();
        if (SIGLAS.has(limpaAlto)) return p.toUpperCase() === p ? p : limpaAlto;
        if (/^[A-Z]{2,}$/.test(p.replace(/[.,]/g, ""))) {
          const baixo = p.toLocaleLowerCase("pt-BR");
          return baixo.charAt(0).toLocaleUpperCase("pt-BR") + baixo.slice(1);
        }
        return p.toUpperCase() === p ? p : p;
      }
      const núcleo = p.match(/^([^A-Za-zÀ-ÖØ-öø-ÿ]*)([A-Za-zÀ-ÖØ-öø-ÿ]+)(.*)$/);
      if (!núcleo) return p;
      const [, pre, meio, pos] = núcleo as [string, string, string, string];
      if (!primeiro && fora.has(meio.toLocaleLowerCase("pt-BR"))) return pre + meio.toLocaleLowerCase("pt-BR") + pos;
      primeiro = false;
      const baixo = meio.toLocaleLowerCase("pt-BR");
      return pre + baixo + pos;
    })
    .join("")
    .replace(/^([^A-Za-zÀ-ÖØ-öø-ÿ]*)([a-zà-öø-ÿ])/, (_m, pre: string, c: string) => pre + c.toLocaleUpperCase("pt-BR"));
}

export function descricaoItem(texto: string | null): string {
  if (!texto || !texto.trim()) return "Item sem descrição";
  return sentenceCase(texto.trim().replace(/\s+/g, " "));
}

export function tituloObjeto(objeto: string | null): string {
  if (!objeto || !objeto.trim()) return "Objeto não informado";
  let t = objeto.trim().replace(/\s+/g, " ");
  t = t.replace(/^o objeto (da presente|do presente|desta|deste) [^,]*?\s(é|e)\s+/i, "");
  t = t.replace(/^(a )?contrata[çc][ãa]o de empresa( especializada)?( (para|em|na|no|de))?\s+/i, "");
  t = t.replace(/^(a )?contratacao de empresa( especializada)?( (para|em|na|no|de))?\s+/i, "");
  t = sentenceCase(t.trim());
  if (t.length <= 90) return t;
  const corte = t.slice(0, 90);
  const espaço = corte.lastIndexOf(" ");
  const curto = (espaço > 60 ? corte.slice(0, espaço) : corte).trimEnd();
  return curto + "…";
}

const MAIUSCULAS_FIXAS = new Set(["S.A.", "LTDA", "ME", "EPP", "EIRELI", "S/A"]);
const PARTICULAS = new Set(["de", "da", "do", "das", "dos", "e"]);

function siglaUf(palavra: string): boolean {
  return /^[A-Z]{2}$/.test(palavra.replace(/[.,]/g, ""));
}

export function nomeFornecedor(nome: string | null): string {
  if (!nome || !nome.trim()) return "Fornecedor não informado";
  const semMatriz = nome.trim().replace(/\s*\(MATRIZ\s*\d*\)\s*$/i, "").trim().replace(/\s+/g, " ");
  const palavras = semMatriz.split(" ");
  let apósFilial = false;
  return palavras
    .map((p, i) => {
      const alto = p.toLocaleUpperCase("pt-BR");
      if (MAIUSCULAS_FIXAS.has(alto)) return alto === "S.A." ? "S.A." : alto;
      if (/^filial$/i.test(p)) { apósFilial = true; return "Filial"; }
      if (apósFilial && siglaUf(p)) return p.toLocaleUpperCase("pt-BR");
      if (ehSigla(p) && (/[./]/.test(p) || p.length <= 4)) {
        if (p.includes(".")) return p;
        return p.toLocaleUpperCase("pt-BR");
      }
      if (i > 0 && PARTICULAS.has(alto.toLocaleLowerCase("pt-BR"))) return alto.toLocaleLowerCase("pt-BR");
      if (/^[A-ZÀ-Þ]{2,}$/.test(p)) {
        const baixo = p.toLocaleLowerCase("pt-BR");
        return baixo.charAt(0).toLocaleUpperCase("pt-BR") + baixo.slice(1);
      }
      return p.charAt(0).toLocaleUpperCase("pt-BR") + p.slice(1).toLocaleLowerCase("pt-BR");
    })
    .join(" ");
}
