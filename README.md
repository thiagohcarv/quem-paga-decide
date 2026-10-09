# Quem Paga Decide

Painel público com o que cada deputado federal e senador fez desde 2023:
projetos apresentados, quantos viraram norma, patrimônio declarado ao TSE,
tempo de mandato e os processos a que respondem.

## Como funciona

- `robo/atualizar.py` baixa os dados oficiais da Câmara, do Senado e do TSE.
- `.github/workflows/atualizar.yml` roda o robô toda madrugada e guarda o
  resultado em `docs/dados/`. Cada execução vira um registro no histórico do
  repositório, então há cópia de todas as versões anteriores.
- `docs/index.html` é o site. Ele lê só os arquivos de `docs/dados/`, por isso
  continua no ar mesmo que os portais oficiais caiam.

Se uma fonte falhar, o robô mantém a cópia anterior daquela fonte e o site
avisa de quando são os dados.

## Colocar no ar (uma vez)

1. Em **Settings → Pages**, escolha **Deploy from a branch**, branch `main`,
   pasta `/docs`, e salve.
2. Em **Actions**, abra **Atualizar dados** e clique em **Run workflow** para
   a primeira coleta. Leva alguns minutos.
3. O endereço do site aparece em **Settings → Pages**.

## Registrar processos e inquéritos

Os registros ficam em `docs/dados/processos.json`, um por item:

```json
{
  "chave": "NOME CIVIL COMPLETO SEM ACENTO|UF",
  "tribunal": "STF",
  "tipo": "Inquérito",
  "numero": "0000",
  "situacao": "Em andamento",
  "fonte_tipo": "oficial",
  "link": "https://portal.stf.jus.br/...",
  "consultado_em": "2026-10-09"
}
```

- `chave` é o campo `chave` do parlamentar em `parlamentares.json`.
- `fonte_tipo` é `oficial` (consulta no tribunal) ou `imprensa`.
- Todo registro precisa de número e link. Sem os dois, não entra.
- Investigado ou réu não é condenado; o site diz isso em todas as fichas.

## Limites conhecidos

- "Viraram norma" usa a situação oficial "transformado em norma jurídica".
  Projeto aprovado dentro de outro texto não é contado.
- Eleitos em 2026 sem mandato no Congresso aparecem sem produção: não existe
  base nacional única de vereadores, deputados estaduais e prefeitos.
- O cruzamento com o TSE é por nome completo e estado.
- Patrimônio é a soma dos bens declarados pelo próprio candidato em 2018,
  2022 e 2026, pelo valor declarado. Não é avaliação de mercado.
- Tempo de mandato: deputados, desde a primeira legislatura na Câmara;
  senadores, desde o início do mandato atual.
