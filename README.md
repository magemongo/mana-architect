# Mana Architect

Aplicação local para organizar uma coleção de Magic: The Gathering e ajudar
a montar decks Commander usando somente cartas que o usuário possui.

## Quickstart (Windows / PowerShell)

Requer Python 3.11 ou superior. No terminal, na pasta do projeto:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Para instalar também a interface local:

```powershell
python -m pip install -e ".[app,dev]"
streamlit run .\src\mana_architect\streamlit_app.py
```

O Streamlit abre o Mana Architect no navegador. Escolha um comandante, gere o
deck na página **Construtor** e abra **Deck** para navegar pela lista agrupada
em três colunas compactas, na ordem Comandante, Criaturas, Artefatos,
Encantamentos, Mágicas instantâneas, Feitiços e Terrenos; passe o cursor sobre o nome de uma carta
para ver sua imagem e texto Oracle na prévia. A validação e os downloads JSON/TXT
ficam na página do deck. Na tela larga, todo o painel da prévia acompanha a
rolagem sem cobrir os detalhes nem criar uma área de rolagem própria. A análise
mostra a curva de mana em histograma, e os custos com símbolos conhecidos
aparecem em ícones compactos junto aos nomes, sem caixas de botão. Não há edição
manual nesta etapa; o fluxo futuro de alterações será integrado ao assistente
LLM. A interface usa o banco local configurado em `.env`, assim como a CLI.

O perfil operacional atual de Anowon prioriza sinais de Rogues tribais, mill e
dano de combate; esse perfil legado permanece em
`config/strategy_profiles.toml`. O novo catálogo versionado de estratégias,
tags, funções, regras e parâmetros fica nos arquivos JSON em `config/`;
ele ainda não substitui o perfil legado nem está conectado ao construtor. As
diretrizes estão em `docs/deckbuilding-guidelines.md`. Comandantes sem perfil
usam os critérios gerais de função e curva. O seletor de terrenos favorece
fontes que cobrem cores da identidade do comandante e pode incluir terrenos
utilitários; esses limites são heurísticos e configuráveis no perfil.

O classificador por regras versionadas produz evidências candidatas e mantém
revisão humana para sinais ambíguos; não persiste classificações nem calcula
afinidade ou score. As regras textuais são heurísticas literais limitadas:
não interpretam contexto ou sinergia entre cartas, e combos infinitos exigem
curadoria explícita.

A camada de domínio permite aplicar overrides manuais em memória, preservando
as evidências automáticas e sinalizando quando uma exclusão contradiz uma
sugestão. Também valida perfis de afinidade de comandante com múltiplas
estratégias e tags, níveis `primary`, `secondary` ou `complementary` e
explicações. Scores de afinidade são valores heurísticos, não probabilidades.
Overrides, afinidades e evidências agora podem ser persistidos em tabelas
aditivas do SQLite por transações explícitas; ainda não são conectados ao
construtor. A reclassificação incremental é uma operação explícita e não roda
automaticamente ao abrir o app. Consulte
`docs/classification-storage.md` antes de executar a persistência na coleção.

Se o comando `py` não estiver disponível, instale o Python e habilite o
launcher durante a instalação, ou crie o ambiente com o caminho completo
do executável Python:

```powershell
& "C:\caminho\para\python.exe" -m venv .venv
```

O arquivo `.env` é opcional; sem ele, o banco padrão será
`data/mana_architect.db`. Para importar o CSV exportado pelo ManaBox:

```powershell
python -m mana_architect import-csv .\collection.csv
```

Para pesquisar cartas e consultar a quantidade total registrada:

```powershell
python -m mana_architect search "anowon"
```

Para baixar o Bulk Data `default_cards`, enriquecer cartas e associar
impressões aos respectivos `oracle_id`:

```powershell
python -m mana_architect sync-scryfall
```

O primeiro download tem aproximadamente 77 MB compactado. O arquivo fica
em `data/cache/scryfall/` e é reutilizado enquanto a versão publicada
pelo Scryfall não mudar. O comando consulta os metadados do Bulk Data,
mas processa toda a associação entre coleção e cartas localmente: os IDs
da coleção não são enviados à API. É necessária conexão com a internet
para sincronizar; pesquisa e importação continuam locais.

Após atualizar para a versão que armazena as cores de mana produzidas pelos
terrenos, execute `sync-scryfall` novamente. Os dados já baixados podem ser
reprocessados do cache local, sem baixar o Bulk Data outra vez.

O arquivo `default_cards` contém cartas em inglês e cartas disponíveis
somente em outros idiomas. Por isso, algumas impressões traduzidas podem
não estar no arquivo e permanecerão sem associação a `oracle_id`. Elas
não são removidas nem descartadas; o comando informa quantas ficaram sem
associação.

Depois da sincronização, a pesquisa aceita filtros combináveis:

```powershell
python -m mana_architect search --color U --identity WU
python -m mana_architect search "anowon" --type Creature
python -m mana_architect search --mana-cost "{2}{U}"
```

Os filtros de cor e identidade exigem todos os símbolos informados. Os
filtros de tipo e custo de mana procuram um trecho do campo.

## Escolha de comandante e coleção compatível

Para ver os comandantes elegíveis, legais segundo a última sincronização
e presentes na coleção:

```powershell
python -m mana_architect commanders
python -m mana_architect commanders --query "Anowon"
```

O programa apenas apresenta as opções; a escolha é do usuário. Depois,
filtre sua coleção informando o nome exato ou `oracle_id` escolhido:

```powershell
python -m mana_architect search --commander "Anowon, the Ruin Thief"
python -m mana_architect search --commander "COLE_AQUI_O_ORACLE_ID" --type Creature
python -m mana_architect search --commander "COLE_AQUI_O_ORACLE_ID" --roles
```

Esse filtro inclui apenas cartas que você possui, que estão marcadas como
legais em Commander nos dados Scryfall sincronizados e cuja identidade de
cor é subconjunto da identidade do comandante. Os dados de legalidade
podem ficar desatualizados: execute `sync-scryfall` e confira a data
exibida. A classificação de funções é heurística, baseada em trechos do
texto Oracle, e serve apenas como ponto de partida. A seleção de pares de
comandantes (Partner/Background) ainda não está implementada.

## Validar deck Commander

Crie um arquivo JSON com o comandante escolhido e as quantidades por
`oracle_id`. A carta do comandante precisa estar na lista e conta dentro
das 100 cartas:

```json
{
  "commander": "ORACLE_ID_DO_COMANDANTE",
  "cards": [
    {"oracle_id": "ORACLE_ID_DO_COMANDANTE", "quantity": 1},
    {"oracle_id": "ORACLE_ID_DE_OUTRA_CARTA", "quantity": 99}
  ]
}
```

Valide com:

```powershell
python -m mana_architect validate-deck .\deck.json
```

O validador verifica tamanho, comandante, legalidade conforme o snapshot
Scryfall registrado, identidade de cor, singleton (incluindo terrenos
básicos e exceções explícitas de quantidade no texto Oracle) e
disponibilidade física. O resultado diferencia decks inválidos de
validações incompletas quando faltam dados. O resultado `válido` é sempre
condicionado aos dados sincronizados: a data e as fontes são exibidas, e
isso não garante que a banlist esteja atualizada hoje. Execute
`sync-scryfall` antes de validar. Ainda não há suporte a dois comandantes
em parceria.

## Gerar rascunho de deck

Escolha um comandante listado pelo comando `commanders` e gere um
rascunho com as cartas disponíveis e legais conforme o snapshot atual:

```powershell
python -m mana_architect build-deck "Anowon, the Ruin Thief" --output .\data\decks\anowon.json
```

O padrão é 38 terrenos; altere para refletir sua preferência e estratégia:

```powershell
python -m mana_architect build-deck "Anowon, the Ruin Thief" --lands 36 --output .\data\decks\anowon.json
```

O gerador inclui o comandante, distribui terrenos básicos entre as cores
da identidade quando disponíveis e usa os rótulos heurísticos (ramp,
compra, remoção e recursão), com custo de mana como critério secundário,
para ordenar as outras opções. A composição não é uma recomendação
estratégica garantida: revise sinergia, curva, terrenos e plano de jogo.
O rascunho só é salvo se tiver 100 cartas e não houver violações
determinísticas conhecidas; se os dados não permitirem completar a lista,
o programa informa a limitação e não grava um deck incompleto. Se a
validação ficar incompleta por dados ausentes, o JSON informa esse estado
e o comando retorna código 2. O arquivo pode ser editado e verificado de
novo com `validate-deck`.

O arquivo de destino não é sobrescrito por padrão. Para substituí-lo
explicitamente, acrescente `--overwrite`. Arquivos em `data/decks/` não
são versionados no Git.

## Analisar e exportar decklist

Para ver os nomes, a curva de mana, a contagem de terrenos, os rótulos
funcionais heurísticos e o resultado do validador:

```powershell
python -m mana_architect analyze-deck .\data\decks\anowon.json
```

Para também criar uma lista de texto fácil de ler ou copiar:

```powershell
python -m mana_architect analyze-deck .\data\decks\anowon.json --export-text .\data\decks\anowon.txt
```

O arquivo `.txt` separa comandante e deck principal, usa o formato
`quantidade nome da carta` e não é sobrescrito sem `--overwrite`.
A curva inclui não-terrenos com valor de mana conhecido, inclusive o
comandante. Os rótulos funcionais podem se sobrepor e são heurísticos,
não uma avaliação estratégica.

Também é possível executar os testes:

```powershell
python -m pytest
```

O caminho do banco pode ser alterado em `.env` pela variável
`MANA_ARCHITECT_DB_PATH`.

## Importação do ManaBox

O importador usa as colunas `Name`, `Set code`, `Collector number`,
`Foil`, `Quantity`, `Scryfall ID`, `Condition` e `Language`. Linhas com
campos inválidos interrompem a importação com o número da linha indicado;
o arquivo original não é modificado.

Reimportar o mesmo CSV atualiza a quantidade de cada impressão para a
quantidade informada no arquivo, sem somá-la novamente. Linhas repetidas
com a mesma impressão, idioma, acabamento e condição são agrupadas e suas
quantidades somadas. Registros que não aparecem em uma nova importação
não são apagados.

As impressões e seus identificadores Scryfall são armazenados localmente.
O comando `sync-scryfall` preenche os dados de cartas e associa as
impressões encontradas no Bulk Data à identidade lógica (`oracle_id`).
Não apaga impressões quando os dados externos estão indisponíveis ou não
contêm determinado idioma.

## Escopo atual

O projeto inclui importação e pesquisa de coleção pela CLI, sincronização de
cartas, seleção explícita de comandante, validação e geração determinística
de rascunhos, análise e exportação de decklists. A interface Streamlit local
separa o construtor da visualização do deck, com lista agrupada e prévia de
cartas. A seleção considera o perfil estratégico inicial de Anowon e a
cobertura de cores da base de mana; as classificações, os sinais de sinergia
e a composição continuam heurísticos, não recomendações estratégicas
garantidas.
