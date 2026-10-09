# Quem Paga Decide — contexto para o Claude Code

Painel público, em português, com o que cada deputado federal e senador fez
desde 2023 (projetos, quantos viraram norma, processos). Prazo curto: precisa
estar no ar em poucos dias. O dono do projeto não é programador; explique os
passos em linguagem simples.

## Estado atual

- Código escrito, **nunca executado contra os portais reais**. A lógica foi
  testada só com respostas simuladas.
- Falta: criar o repositório público no GitHub, enviar o código, ativar o
  GitHub Pages (branch `main`, pasta `/docs`) e rodar o workflow
  "Atualizar dados" pela primeira vez.

## Primeira tarefa

Rodar `python robo/atualizar.py` localmente e corrigir o que falhar. Pontos de
maior risco, nesta ordem:

1. **Senado** (`etapa_senado`): as duas rotas de autorias (`/processo` e
   `/senador/{cod}/autorias.json`) foram escritas de memória. Conferir o
   formato real em https://legis.senado.leg.br/dadosabertos/ e ajustar os
   nomes de campos.
2. **TSE** (`etapa_tse`): baixa `consulta_cand_{ano}.zip` e
   `bem_candidato_{ano}.zip` de 2018, 2022 e 2026. Conferir os nomes das
   colunas (`SQ_CANDIDATO`, `DS_SIT_TOT_TURNO`, `VR_BEM_CANDIDATO`). O arquivo
   de 2026 pode ainda não marcar os eleitos; nesse caso o painel sobe sem a
   marca, e a lista não deve ser inventada. O patrimônio é cruzado por nome
   completo e estado: conferir se homônimos geram valores trocados.
3. **Câmara** (`etapa_camara`): conferir os campos dos arquivos anuais
   `proposicoes-{ano}.json` e `proposicoesAutores-{ano}.json`, e o campo
   `idLegislaturaInicial` de `deputados.json` (usado no tempo de mandato).

Depois de corrigir, conferir por amostragem três deputados e três senadores
contra as páginas oficiais antes de divulgar qualquer número.

## Regras do projeto

- Só a biblioteca padrão do Python no robô. Site em HTML, CSS e JS puros.
- Visual minimalista: fundo liso, uma coluna, linhas finas, verde e amarelo
  apenas nos detalhes. Não adicionar cartões, sombras ou bibliotecas.
- Se uma fonte falhar, manter a cópia anterior (`docs/dados/copia_*.json`) e
  registrar em `status.json`. O site nunca pode ficar vazio por causa de um
  portal fora do ar.
- Processos e inquéritos (`docs/dados/processos.json`): cada registro exige
  número e link da fonte, com `fonte_tipo` igual a `oficial` ou `imprensa`.
  Nunca incluir acusação sem fonte. Investigado ou réu não é condenado.
- Patrimônio é sempre "declarado ao TSE"; nunca apresentar como fortuna
  real nem insinuar enriquecimento ilícito a partir da variação.
- Nenhum número ou nome inventado, nem como exemplo dentro dos dados.
