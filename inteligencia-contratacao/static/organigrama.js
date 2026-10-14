// Organigrama interativo (Cytoscape): pessoas, órgãos e entidades com cargos públicos registados.
// Clicar num nó expande os seus vizinhos; clicar numa ligação mostra fonte, período e natureza.
// Ligações DOCUMENTADAS (cargo exercido) e INFERIDAS (correspondência com o BASE) têm estilos distintos.
/* global cytoscape */
(function () {
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const dataPt = d => d ? new Date(d + 'T00:00:00').toLocaleDateString('pt-PT') : '…';
  const cor = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  window.Organigrama = function (el, { info, nota, filtros }) {
    const expandidos = new Set();
    let cy = null;

    function estilo() {
      return [
        { selector: 'node', style: { 'label': 'data(rotulo)', 'font-size': 10, 'color': cor('--fg'), 'text-wrap': 'wrap',
            'text-max-width': 130, 'text-valign': 'bottom', 'text-margin-y': 4, 'width': 22, 'height': 22,
            'text-background-color': cor('--surface'), 'text-background-opacity': .8, 'text-background-padding': 1 } },
        { selector: 'node[tipo="pessoa"]', style: { 'background-color': cor('--no-pessoa'), 'shape': 'ellipse' } },
        { selector: 'node[tipo="entidade_et"]', style: { 'background-color': cor('--no-et'), 'shape': 'round-rectangle', 'width': 30, 'height': 22 } },
        { selector: 'node[tipo="entidade"]', style: { 'background-color': cor('--no-base'), 'shape': 'diamond', 'width': 28, 'height': 28 } },
        { selector: 'node[tipo="orgao"]', style: { 'background-color': cor('--surface'), 'border-width': 1,
            'border-color': cor('--mut'), 'shape': 'round-rectangle', 'width': 18, 'height': 12, 'color': cor('--mut') } },
        { selector: 'node.centro', style: { 'border-width': 3, 'border-color': cor('--fg') } },
        { selector: 'node.expandido', style: { 'border-width': 2, 'border-color': cor('--mut'), 'border-style': 'dotted' } },
        { selector: 'edge', style: { 'width': 2, 'line-color': cor('--aresta'), 'curve-style': 'bezier',
            'target-arrow-shape': 'triangle', 'target-arrow-color': cor('--aresta'), 'arrow-scale': .7,
            'label': 'data(rotulo)', 'font-size': 8, 'color': cor('--mut'), 'text-rotation': 'autorotate',
            'text-background-color': cor('--surface'), 'text-background-opacity': .8 } },
        { selector: 'edge[?passada]', style: { 'opacity': .35 } },
        { selector: 'edge[tipo="estrutura"]', style: { 'width': 1, 'line-style': 'dotted', 'target-arrow-shape': 'none' } },
        { selector: 'edge[natureza="inferida"]', style: { 'line-style': 'dashed', 'line-color': cor('--aresta-inf'),
            'target-arrow-color': cor('--aresta-inf'), 'color': cor('--aresta-inf') } },
        { selector: ':selected', style: { 'overlay-opacity': .15 } },
      ];
    }

    function params(no) {
      const p = new URLSearchParams({ no });
      const f = filtros();
      if (f.de) p.set('de', f.de);
      if (f.ate) p.set('ate', f.ate);
      p.set('estado', f.estado || 'todas');
      return p;
    }

    function rotuloNo(n) {
      return n.tipo === 'entidade' ? n.nome + '\n(BASE)' : n.nome;
    }

    async function expandir(no, centro = false) {
      const r = await fetch('/api/organigrama?' + params(no));
      if (!r.ok) { info.textContent = (await r.json()).detail || 'Não foi possível carregar.'; return; }
      const g = await r.json();
      expandidos.add(no);
      for (const n of g.nos) {
        if (cy.getElementById(n.id).nonempty()) continue;
        cy.add({ group: 'nodes', data: { id: n.id, tipo: n.tipo, nome: n.nome, rotulo: rotuloNo(n) } });
      }
      for (const a of g.arestas) {
        if (cy.getElementById(a.id).nonempty()) continue;
        const rot = a.natureza === 'inferida' ? `${a.rotulo} (${Math.round(a.confianca * 100)}%)` : a.rotulo;
        cy.add({ group: 'edges', data: { ...a, rotulo: rot, passada: a.atual === false } });
      }
      cy.getElementById(no).addClass(centro ? 'centro' : 'expandido');
      nota.textContent = g.pessoas_omitidas
        ? `${g.pessoas_omitidas} cargos de outras pessoas omitidos (limite por entidade); use os filtros de período.` : '';
      // árvore a partir do nó central: entidade -> órgãos -> pessoas -> outras entidades
      cy.layout({ name: 'breadthfirst', roots: cy.$('.centro'), directed: false, spacingFactor: 1.1,
        padding: 24, animate: false, avoidOverlap: true }).run();
      if (cy.zoom() > 1.1) { cy.zoom(1.1); cy.center(); }
    }

    function descreverAresta(d) {
      if (d.tipo === 'corresponde_a') {
        return `<b>Relação INFERIDA</b> (confiança ${Math.round(d.confianca * 100)}%): a entidade como a Entidade para a
          Transparência a nomeia <em>possivelmente corresponde</em> à entidade do Portal BASE. Método: ${esc(d.metodo)}.
          Não é um facto da fonte; confirmar antes de usar.`;
      }
      return `<b>Relação DOCUMENTADA</b> — ${esc(d.cargo)}${d.orgao ? ' · ' + esc(d.orgao) : ''}<br>
        Período: ${dataPt(d.valido_de)} – ${d.valido_ate ? dataPt(d.valido_ate) : 'em funções (sem declaração de cessação)'}
        ${d.atual ? '· <b>atual</b>' : '· passado'}<br>Fonte: ${esc(d.fonte)}
        (<a href="https://entidadetransparencia.pt/consultardeclaracaopublica" target="_blank" rel="noopener">consultar</a>)`;
    }

    function descreverNo(d) {
      const ligacao = d.tipo === 'pessoa' ? `<a href="/pessoa/${d.id.split(':')[1]}">abrir perfil da pessoa</a>`
        : d.tipo === 'entidade' ? `<a href="/entidade/${d.id.split(':')[1]}">abrir perfil de risco da entidade (BASE)</a>`
        : `<a href="/organigrama?no=${encodeURIComponent(d.id)}">centrar organigrama nesta entidade</a>`;
      return `<b>${esc(d.nome)}</b> — ${ligacao}.`;
    }

    return {
      async iniciar(no) {
        if (cy) cy.destroy();
        expandidos.clear();
        cy = cytoscape({ container: el, style: estilo(), wheelSensitivity: .3, minZoom: .2, maxZoom: 3 });
        cy.on('tap', 'node', async ev => {
          const d = ev.target.data();
          if (d.tipo === 'orgao') return;
          info.innerHTML = descreverNo(d);
          if (!expandidos.has(d.id)) await expandir(d.id);
        });
        cy.on('tap', 'edge', ev => {
          const d = ev.target.data();
          info.innerHTML = d.tipo === 'estrutura' ? 'Órgão da entidade (estrutura, não é uma relação).' : descreverAresta(d);
        });
        await expandir(no, true);
      },
      async recarregar(no) { await this.iniciar(no); },
    };
  };
})();
