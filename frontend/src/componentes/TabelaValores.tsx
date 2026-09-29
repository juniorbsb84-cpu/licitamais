import type { components } from "../api/esquema";
import { data, descricaoItem, nomeFornecedor, reais } from "../lib/formato";

type Detalhe = components["schemas"]["LicitacaoDetalhe"];

function fraseQuantidade(q: number | null): string {
  if (q === null || q === undefined) return "";
  return q.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
}

export function TabelaValores({ licitacao }: { licitacao: Detalhe }) {
  const itens = licitacao.itens;
  const vencedores = licitacao.vencedores;
  const total = vencedores.reduce((s, v) => s + (v.valor ?? 0), 0);
  const contratos = licitacao.contratos;
  const totalContratos = contratos.reduce((s, c) => s + (c.valor ?? 0), 0);
  const vigencia = (c: Detalhe["contratos"][number]) =>
    c.vigencia_inicio && c.vigencia_fim ? `${data(c.vigencia_inicio)} a ${data(c.vigencia_fim)}`
      : c.vigencia_inicio ? `desde ${data(c.vigencia_inicio)}` : c.assinatura ? `assinado em ${data(c.assinatura)}` : "";
  const comEstimativa = itens.some((i) => i.valor_estimado !== null && i.valor_estimado !== undefined);
  return (
    <>
      {itens.length > 0 && (
        <section className="bloco" aria-labelledby="t-itens">
          <h2 className="bloco-titulo" id="t-itens">Itens</h2>
          <p className="bloco-sub">
            {itens.length} {itens.length === 1 ? "item" : "itens"} com as quantidades publicadas.
            {!comEstimativa && " A fonte não publica valor estimado por item."}
          </p>
          <div className="tabela-wrap">
            <table className="tabela">
              <caption className="pular" style={{ position: "absolute", left: -9999 }}>Itens do processo</caption>
              <thead>
                <tr>
                  <th scope="col">Descrição</th>
                  <th scope="col" className="dir">Quantidade</th>
                  <th scope="col">Unidade</th>
                </tr>
              </thead>
              <tbody>
                {itens.map((it, i) => (
                  <tr key={i}>
                    <td data-rot="Descrição" className="nome">{descricaoItem(it.descricao)}</td>
                    <td data-rot="Quantidade" className="num dir">{fraseQuantidade(it.quantidade)}</td>
                    <td data-rot="Unidade" className="secundario">{(it.unidade ?? "").toLocaleLowerCase("pt-BR")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {contratos.length > 0 && (
        <section className="bloco" aria-labelledby="t-contratos">
          <h2 className="bloco-titulo" id="t-contratos">Contratos</h2>
          <p className="bloco-sub">
            {contratos.length} {contratos.length === 1 ? "contrato assinado" : "contratos assinados"},{" "}
            <span className="num">{reais(totalContratos)}</span> somados.
          </p>
          <div className="tabela-wrap" data-testid="contratos">
            <table className="tabela">
              <caption className="pular" style={{ position: "absolute", left: -9999 }}>Contratos assinados</caption>
              <thead>
                <tr>
                  <th scope="col">Fornecedor</th>
                  <th scope="col" className="col-cnpj">CNPJ</th>
                  <th scope="col">Vigência</th>
                  <th scope="col" className="dir">Valor</th>
                </tr>
              </thead>
              <tbody>
                {contratos.map((c, i) => (
                  <tr key={i}>
                    <td data-rot="Fornecedor" className="nome">{nomeFornecedor(c.fornecedor)}</td>
                    <td data-rot="CNPJ" className="num secundario col-cnpj">{cnpj(c.cnpj)}</td>
                    <td data-rot="Vigência" className="num secundario">{vigencia(c)}</td>
                    <td data-rot="Valor" className="num dir valor">{c.valor !== null && c.valor !== undefined ? reais(c.valor) : ""}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}>{contratos.length} {contratos.length === 1 ? "contrato" : "contratos"}</td>
                  <td className="dir num total">{reais(totalContratos)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </section>
      )}
      {vencedores.length > 0 && (
        <section className="bloco" aria-labelledby="t-vencedores">
          <h2 className="bloco-titulo" id="t-vencedores">Vencedores</h2>
          <p className="bloco-sub">
            {vencedores.length} valores adjudicados, <span className="num">{reais(total)}</span> somados.
            {licitacao.contratos.length === 0 && " A fonte não publica contratos assinados deste processo."}
          </p>
          <div className="tabela-wrap" data-testid="vencedores">
            <table className="tabela">
              <caption className="pular" style={{ position: "absolute", left: -9999 }}>Valores adjudicados</caption>
              <thead>
                <tr>
                  <th scope="col">Fornecedor</th>
                  <th scope="col" className="col-cnpj">CNPJ</th>
                  <th scope="col" className="dir">Valor</th>
                </tr>
              </thead>
              <tbody>
                {vencedores.map((v, i) => (
                  <tr key={i}>
                    <td data-rot="Fornecedor" className="nome">{nomeFornecedor(v.fornecedor)}</td>
                    <td data-rot="CNPJ" className="num secundario col-cnpj">{cnpj(v.cnpj)}</td>
                    <td data-rot="Valor" className="num dir valor">{v.valor !== null && v.valor !== undefined ? reais(v.valor) : ""}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={2}>{vencedores.length} valores adjudicados</td>
                  <td className="dir num total">{reais(total)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

function cnpj(valor: string | null): string {
  const d = (valor ?? "").replace(/\D/g, "");
  if (d.length !== 14) return valor ?? "";
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
}
