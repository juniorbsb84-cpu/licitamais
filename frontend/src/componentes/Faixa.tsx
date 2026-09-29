/** Faixa escura do topo das telas: continua o cabeçalho e abre a página com um título em serifa. */
export function Faixa({ titulo, apoio, children }: { titulo: React.ReactNode; apoio?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <section className="faixa" aria-labelledby="titulo-faixa">
      <div className="container faixa-dentro">
        <h1 id="titulo-faixa">{titulo}</h1>
        {apoio && <p className="faixa-apoio">{apoio}</p>}
        {children}
      </div>
    </section>
  );
}
