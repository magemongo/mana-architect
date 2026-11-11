# Mana Architect — Diretrizes de implementação do catálogo de estratégias

> Especificação para implementação do catálogo de estratégias, classificação de cartas e comandantes e pontuação determinística de sinergia. Este arquivo é destinado à raiz do repositório e deve ser tratado como requisito de produto e engenharia.

## 1. Objetivo e contexto

O Mana Architect é uma aplicação local de apoio à construção de decks de Commander a partir da coleção real do usuário. A classificação deve ajudar a encontrar cartas que contribuam para um plano de jogo e respeitar a identidade de cor, legalidade e disponibilidade na coleção. O sistema deve produzir resultados repetíveis, explicáveis e auditáveis.

Este documento especifica a evolução do núcleo de dados do projeto para incluir:

- Um catálogo configurável de estratégias, iniciado com 15 temas.
- Classificação de cartas por estratégia e por tags de sinergia.
- Classificação funcional de cartas (ramp, remoção, compra de cartas etc.).
- Afinidade de comandantes com uma ou mais estratégias.
- Classificação automática baseada em regras explícitas e classificação manual com precedência definida.
- Um motor determinístico de compatibilidade e pontuação, com explicação dos pontos atribuídos.
- Persistência relacional em SQLite e arquivos JSON validados como fonte de configuração.

O catálogo não é uma lista de classes mutuamente exclusivas. Um comandante pode, por exemplo, apoiar Tokens, Aristocrats e Lifegain; uma carta pode ser simultaneamente enabler, payoff, interação e sinérgica com várias estratégias.

## 2. Princípios obrigatórios

1. **Separar conceitos.** Estratégia descreve um plano ou eixo do deck; tag de sinergia descreve uma mecânica ou interação; função descreve o serviço prestado pela carta. Não use uma única lista de rótulos para representar tudo isso.
2. **Permitir múltiplas classificações.** Cartas e comandantes podem ter zero, uma ou várias estratégias e funções. Não force uma categoria primária única para toda carta.
3. **Código decide; dados configuram.** O Python aplica critérios e pesos definidos. O LLM não pode inventar ou alterar classificações, pesos, legalidade ou compatibilidade durante uma recomendação.
4. **Explicar cada resultado.** Toda pontuação precisa poder ser decomposta em regras, evidências e ajustes manuais que a produziram.
5. **Repetibilidade.** Mesma versão de catálogo, mesma carta, mesmo comandante e mesmos parâmetros devem produzir o mesmo resultado, sem aleatoriedade, rede ou chamada de modelo.
6. **Preservar proveniência.** Distinguir uma classificação inferida automaticamente de uma classificação revisada ou fixada manualmente.
7. **Tratar evidência com prudência.** Uma palavra-chave isolada nem sempre prova sinergia. Ex.: flying pode ajudar Voltron, mas não torna qualquer carta uma carta Voltron; possuir o tipo Artifact não significa que a carta tenha papel relevante em um deck de artefatos.
8. **Compatibilidade antes de ranqueamento.** Identidade de cor, legalidade no formato e disponibilidade na coleção são filtros ou restrições; nunca devem ser compensados por uma pontuação alta de sinergia.
9. **Não perder dados existentes.** Inspecionar a estrutura e as migrações atuais antes de alterar o esquema. Não recriar nem apagar o banco da coleção.
10. **Configuração versionada.** IDs estáveis são chaves técnicas; nomes e traduções são rótulos de apresentação. Alterar um nome não pode quebrar referências salvas.

## 3. Vocabulário e fronteiras

### 3.1 Estratégia

Uma estratégia é um eixo de construção ou plano de jogo que pode ser apoiado por um comandante e pelas cartas do deck. Os 15 temas iniciais são conceitos editoriais úteis, não uma taxonomia formal exaustiva do Magic.

### 3.2 Tag de sinergia

Uma tag descreve uma mecânica, recurso, evento ou interação observável, por exemplo `create_tokens`, `sacrifice_creature`, `spell_cast_noncreature`, `landfall`, `life_gain`, `graveyard_recursion` ou `protect_single_creature`. Tags devem ter IDs estáveis e podem se relacionar com mais de uma estratégia.

### 3.3 Função

Uma função descreve a contribuição operacional da carta no deck. Exemplos: `ramp`, `card_draw`, `removal`, `board_wipe`, `protection`, `recursion`, `finisher`, `enabler`, `payoff`, `engine`, `tutor`, `mana_fixing` e `land`.

Uma carta pode ter mais de uma função. `Enabler`, `payoff` e `engine` são funções de construção/sinergia, não substitutos de serviços como compra ou remoção.

### 3.4 Afinidade do comandante

Afinidade é a contribuição do comandante a estratégias, tags e funções. Registrar separadamente:

- **Primária:** plano mais diretamente habilitado pelo texto e pelo funcionamento do comandante.
- **Secundária:** plano coerente, mas dependente de suporte adicional.
- **Complementar:** sinergia plausível que pode ser escolhida pelo usuário, porém não deve dominar automaticamente a recomendação.

Esses níveis são metadados configuráveis e não pressupõem que exista uma estratégia correta única.

### 3.5 Evidência e intenção

Regras automáticas detectam evidências no texto Oracle, tipos, subtipos, keywords, custos e campos estruturados disponíveis. Intenção estratégica é uma inferência com confiança e deve poder ser ajustada por uma pessoa.

## 4. Catálogo inicial: 15 estratégias

Os critérios abaixo orientam a configuração inicial. Use os IDs sugeridos sem tradução, em `snake_case`. Os exemplos de cartas são descrições de padrões, não uma lista fechada de nomes de cartas.

| ID | Nome de exibição | Descrição e natureza | Evidências e tipos de carta | Tags e efeitos relevantes |
|---|---|---|---|---|
| `tokens` | Tokens | Gera, multiplica ou explora fichas para pressão de campo, valor de ETB/morte ou custo de outras ações. Eixo de geração de recurso e presença de mesa. | Criadores recorrentes ou em massa; duplicadores; efeitos de populate; payoffs por quantidade, entrada, ataque ou morte de fichas; anthems para o time. | `create_tokens`, `token_doubler`, `populate`, `go_wide`, `anthem`, `creature_etb`, `creature_death`, `sacrifice_token`, evasão em massa. |
| `plus_one_counters` | +1/+1 Counters | Coloca, distribui ou aumenta contadores +1/+1 e transforma esse recurso em crescimento, valor ou vitória. Eixo de progressão e recurso acumulável. | Colocadores e proliferadores; payoffs por criaturas com contadores; remoção ou transferência de contadores; criaturas que escalam com contadores. | `add_plus_one_counters`, `counter_doubler`, `proliferate`, `counter_matters`, `modify_power_toughness`, `counter_transfer`. |
| `combo` | Combo | Monta uma sequência de peças que, em conjunto, gera valor repetível, uma condição de vitória ou um resultado desproporcional. Categoria de plano de vitória/estrutura; exige evidência de peças interdependentes, não apenas texto poderoso. | Peças com loops/repetição, mana ou recursos ilimitados, tutores relevantes, proteção e payoff/saída de combo. Não declarar combo infinito por heurística textual isolada. | `combo_piece`, `repeatable_loop`, `untap_permanent`, `free_cast`, `sacrifice_outlet`, `tutor`, `combo_protection`, `infinite_resource` (somente revisão manual/curadoria validada). |
| `artifacts` | Artifacts | Usa artefatos como recurso, tema de permanentes, combustível ou condição de valor. A presença do tipo Artifact sozinha não implica afinidade forte. | Criação, desconto, sacrifício, recursão, contagem, compra ou payoff por artefatos; artefatos que se animam ou interagem entre si. | `artifact_count`, `create_artifact`, `artifact_etb`, `sacrifice_artifact`, `artifact_recursion`, `artifact_cost_reduction`, `artifact_matter`. |
| `aggro` | Aggro | Pressiona cedo e continuamente por combate, priorizando velocidade e dano sobre vantagem de longo prazo. Plano de ritmo e vitória por combate. | Ameaças de baixo custo, geração de corpos cedo, haste/evasão, bônus de ataque, efeitos de combate que reduzem bloqueios. | `low_curve`, `haste`, `combat_damage`, `attack_trigger`, `evasion`, `go_wide`, `anthem`, `early_pressure`. |
| `reanimator` | Reanimator | Coloca criaturas ou outros permanentes no cemitério e os retorna ao campo por custo reduzido ou efeito repetido. Plano de reutilização do cemitério. | Auto-mill, descarte, sacrifício voluntário, criaturas de alto impacto, reanimação e proteção de cemitério. | `self_mill`, `discard_outlet`, `graveyard_recursion`, `reanimate_creature`, `death_trigger`, `enters_from_graveyard`, `graveyard_setup`. |
| `spellslinger` | Spellslinger | Conjura mágicas, especialmente instantâneas e feitiços, para gerar valor, ameaças ou vitória. Eixo de densidade e recompensa por mágicas. | Redução de custo, cópia/recursão de mágicas, compra por conjuração, tokens ou dano por instant/sorcery. | `spell_cast_noncreature`, `instant_sorcery_matters`, `spell_copy`, `spell_recursion`, `magecraft`, `prowess`, `storm_count`, `cast_from_graveyard`. |
| `lifegain` | Lifegain | Ganha vida de forma relevante e converte esse ganho em recursos, escala ou vitória. Ganho incidental sem payoff não deve receber afinidade forte automaticamente. | Ganho repetido ou em quantidade; payoffs por ganhar vida ou total de vida; estabilização ligada a ganho. | `life_gain`, `life_gain_trigger`, `life_total_matters`, `life_payment`, `life_loss_opponent`, `lifelink`. |
| `control` | Control | Limita recursos e ameaças adversárias com interação, respostas eficientes e vantagem no jogo longo. Plano de resposta/estabilização. | Counterspells, remoção pontual, anulações, wipes, tax/lock (quando aplicável), compra e recursão de interação. | `counterspell`, `spot_removal`, `board_wipe`, `permanent_lock`, `tempo`, `card_advantage`, `instant_speed_interaction`. |
| `aristocrats` | Aristocrats | Gera valor quando criaturas morrem, em especial criaturas próprias, frequentemente usando fichas e outlets de sacrifício. Plano de conversão de corpos em valor/dano. | Outlets gratuitos/repetíveis; payoffs por morte/sacrifício; recorrência; criadores de corpos descartáveis. | `sacrifice_creature`, `death_trigger`, `dies_trigger`, `aristocrats_payoff`, `death_drain`, `sacrifice_outlet`, `recursion`, `token_generation`. |
| `burn` | Burn | Converte mágicas e efeitos diretos em dano à vida de oponentes ou a qualquer alvo como via de pressão ou finalização. Priorizar dano não proveniente de combate quando essa for a intenção. | Instantâneas/feitiços que causam dano, triggers de conjuração que causam dano, duplicadores e efeitos que convertem eventos em dano. Dano de combate genérico não é evidência suficiente. | `noncombat_damage`, `spell_damage`, `damage_any_target`, `damage_opponent`, `damage_amplifier`, `cast_trigger_damage`, `burn_finisher`. |
| `ramp` | Ramp | Acelera ou amplia a disponibilidade de mana além da progressão normal de terrenos, permitindo jogar ameaças e mágicas mais cedo. Função transversal e também eixo de construção. | Busca/colocação de terrenos, mana rocks, dorks, tesouros, redução de custo, geração repetível de mana. Não classificar land drop normal como ramp. | `mana_acceleration`, `land_ramp`, `mana_rock`, `mana_dork`, `treasure`, `cost_reduction`, `extra_land_play`, `mana_from_permanent`. |
| `lands_matter` | Lands Matter | Usa quantidade, entrada, saída ou tipos de terrenos como fonte de valor, sinergia ou condição de vitória. | Landfall; terrenos que entram múltiplos/recursão; sacrifício/busca de terrenos; payoffs por terrenos no campo ou no cemitério; criaturas-terreno. | `landfall`, `land_etb`, `extra_land_play`, `land_recursion`, `land_sacrifice`, `lands_in_graveyard`, `land_count`, `land_token`. |
| `voltron` | Voltron | Concentra melhorias e proteção em uma ameaça, normalmente o comandante, para vencer com dano de combate eficiente. Plano de ameaça única. | Auras/equipamentos, bônus concentrado de poder, proteção direcionada, evasão, indestrutível/hexproof/proteção, recursão de equipamento. Keywords relevantes incluem flying, trample, menace, double strike e vigilance conforme o contexto. | `single_creature_buff`, `aura`, `equipment`, `protect_single_creature`, `hexproof`, `indestructible`, `evasion`, `commander_damage`, `double_strike`, `trample`. |
| `midrange` | Midrange | Mistura desenvolvimento, interação e ameaças eficientes, buscando trocar recursos e vencer com cartas individualmente fortes ao longo do jogo. Plano flexível de curva média. | Ameaças resilientes, valor de ETB, remoção eficiente, compra/recursão e permanentes que geram vantagem sem exigir uma combinação estreita. É uma afinidade ampla; não classificar automaticamente toda carta eficiente como midrange. | `value_etb`, `card_advantage`, `resilient_threat`, `efficient_interaction`, `recursion`, `mana_curve_mid`, `flexible_threat`. |

Cada item de configuração deve incluir uma breve descrição, natureza (por exemplo `resource_engine`, `victory_plan`, `interaction_plan`, `combat_plan` ou `cross_cutting`), tags associadas, exemplos de evidências e notas sobre falsos positivos. Natureza é descritiva, não um campo usado para bloquear sobreposição.

## 5. Arquitetura recomendada

Manter camadas com dependências em uma direção:

```text
config JSON versionada
        ↓ validação e carregamento
catálogo de domínio (estratégias, tags, funções, regras e pesos)
        ↓
classificador determinístico ← dados Oracle normalizados da carta
        ↓                         ajustes manuais / overrides
serviço de afinidade e score
        ↓
repositórios SQLite → aplicação / CLI / futura interface
```

### 5.1 Responsabilidades

- **Configuração:** define vocabulário, descrições, regras, limiares, pesos e versão do catálogo. Nenhum peso oculto hardcoded fora da configuração, exceto validações invariantes.
- **Normalização:** transforma dados Oracle/Scryfall e importações em representação consistente; mantém o texto original e a fonte/versão dos dados.
- **Classificador:** aplica regras puras ao objeto de carta e produz evidências candidatas. Não grava diretamente no banco.
- **Resolução de override:** aplica decisão manual por campo; preserva histórico e justificação. Override explícito pode substituir ou excluir classificação automática.
- **Scorer:** combina afinidade do comandante, evidência da carta, sinergia de tags, funções necessárias e parâmetros de estratégia; produz score limitado e explicação.
- **Persistência:** armazena dados importados, versões de catálogo, resultados calculados e correções manuais em transação.
- **Apresentação:** mostra classificação e motivo em linguagem legível; não recalcula regras de domínio.

### 5.2 Organização de arquivos sugerida

Adaptar à estrutura existente do repositório, sem mover módulos existentes sem motivo:

```text
config/
  strategy_catalog.json
  synergy_tags.json
  card_roles.json
  classification_rules.json
  scoring.json
src/mana_architect/
  domain/
    models.py
    catalog.py
  classification/
    normalizer.py
    rule_engine.py
    overrides.py
  synergy/
    commander_affinity.py
    scorer.py
    explanations.py
  database/
    models.py
    repositories.py
    migrations/
tests/
  fixtures/
  unit/
  integration/
```

Se o repositório ainda usa estrutura diferente, preservar convenções já adotadas e registrar o mapeamento no resumo da implementação.

## 6. Modelo de dados

Use IDs estáveis em `snake_case`, chaves estrangeiras explícitas, unicidade para IDs e constraints para intervalos. O banco de coleção já pode conter cartas e impressões físicas: preferir relacionar classificação ao `oracle_id` (identidade da carta funcional), não ao acabamento/edição. Preservar a relação com a carta física para disponibilidade e versão.

### 6.1 Entidades lógicas

- `strategy`: id, nome, descrição, natureza, estado ativo, versão/metadata.
- `synergy_tag`: id, nome, descrição e família opcional.
- `card_role`: id, nome, descrição e grupo (`deck_service`, `synergy_role` ou `resource`).
- `strategy_tag`: relação muitos-para-muitos entre estratégia e tags, com peso/relacionamento configurável.
- `oracle_card`: registro já existente ou entidade de referência pelo Oracle ID; conservar fonte e campos normalizados.
- `card_classification`: entidade de classificação de uma carta para uma estratégia/função/tag, com score bruto, confiança, origem, versão de regra e estado de revisão.
- `classification_evidence`: regra que disparou, campo examinado, trecho/valor ou referência estruturada e contribuição sugerida.
- `manual_override`: alvo, dimensão, rótulo, ação (`force_include`, `force_exclude`, `set_level`), valor, nota, autor opcional, timestamps e versão.
- `commander_affinity`: comandante/Oracle ID, estratégia, nível, score configurado, origem e nota.
- `catalog_version`: versão, hash ou timestamp da configuração carregada.
- `classification_run`: carta, versão de catálogo/regra, instante, versão do normalizador e resultado; útil para reprodutibilidade.

### 6.2 Regras de armazenamento

- Não guardar classificação manual como se fosse saída automática.
- Não persistir somente o score final: armazenar sinais/evidências que o compõem ou poder reconstruí-los pela versão do catálogo.
- Ao atualizar catálogo, não apagar overrides. Marcar resultados calculados com versão anterior como desatualizados e recalcular por tarefa explícita/migração reversível.
- Definir transações para gravação de uma rodada completa e índices em Oracle ID, strategy ID, tag ID, origem e versão.
- Guardar `confidence`/`score` como número real limitado conforme contrato, validar faixa no domínio e não confiar somente em constraint do SQLite.
- Implementar migrações aditivas e reversíveis quando possível. Snapshot/backup antes de transformação destrutiva.

## 7. Arquivos JSON de configuração

Os arquivos JSON são a fonte editável do catálogo, carregados e validados na inicialização. Usar UTF-8, schema versionado, IDs únicos, referências existentes e erros que indiquem arquivo/campo. Não duplicar a mesma regra com pesos inconsistentes em vários arquivos.

### 7.1 `strategy_catalog.json`

```json
{
  "schema_version": 1,
  "catalog_version": "1.0.0",
  "strategies": [
    {
      "id": "tokens",
      "name": "Tokens",
      "description": "Gera ou explora fichas para presença de campo, valor e pressão.",
      "nature": ["resource_engine", "combat_plan"],
      "active": true,
      "tag_affinities": [
        {"tag_id": "create_tokens", "weight": 1.0},
        {"tag_id": "token_doubler", "weight": 1.0},
        {"tag_id": "sacrifice_token", "weight": 0.7}
      ],
      "notes": ["Tipo de ficha e escala importam; token incidental não basta para alta afinidade."]
    }
  ]
}
```

Preencher o arquivo com todos os 15 IDs da seção 4. `tag_affinities` é uma associação inicial; pesos específicos de score podem residir em `scoring.json` para manter uma única fonte por tipo de peso.

### 7.2 `synergy_tags.json`

```json
{
  "schema_version": 1,
  "tags": [
    {
      "id": "create_tokens",
      "name": "Cria fichas",
      "description": "Cria uma ou mais fichas como parte de um efeito.",
      "family": "tokens",
      "related_strategy_ids": ["tokens", "aristocrats", "aggro"],
      "aliases": ["token_generation", "create token"],
      "manual_review_recommended": false
    },
    {
      "id": "protect_single_creature",
      "name": "Protege uma criatura",
      "description": "Protege uma criatura individual contra remoção ou interação.",
      "family": "protection",
      "related_strategy_ids": ["voltron"],
      "aliases": [],
      "manual_review_recommended": true
    }
  ]
}
```

### 7.3 `card_roles.json`

```json
{
  "schema_version": 1,
  "roles": [
    {"id": "ramp", "name": "Ramp", "group": "deck_service", "description": "Acelera a disponibilidade de mana."},
    {"id": "removal", "name": "Remoção", "group": "deck_service", "description": "Responde a uma ameaça ou permanente."},
    {"id": "enabler", "name": "Enabler", "group": "synergy_role", "description": "Habilita uma mecânica ou linha de jogo."},
    {"id": "payoff", "name": "Payoff", "group": "synergy_role", "description": "Converte uma mecânica em valor ou avanço de vitória."},
    {"id": "engine", "name": "Engine", "group": "synergy_role", "description": "Gera valor recorrente quando recebe suporte."},
    {"id": "finisher", "name": "Finisher", "group": "synergy_role", "description": "Ajuda a encerrar a partida."}
  ]
}
```

O arquivo completo deve incluir no mínimo: `ramp`, `mana_fixing`, `card_draw`, `card_selection`, `tutor`, `removal`, `board_wipe`, `counterspell`, `protection`, `recursion`, `graveyard_hate`, `token_generator`, `sacrifice_outlet`, `enabler`, `payoff`, `engine`, `finisher`, `threat`, `land` e `win_condition`.

### 7.4 `classification_rules.json`

```json
{
  "schema_version": 1,
  "ruleset_version": "1.0.0",
  "rules": [
    {
      "id": "tag_create_tokens_oracle_text",
      "enabled": true,
      "target_type": "tag",
      "target_id": "create_tokens",
      "source_fields": ["oracle_text_normalized"],
      "match": {"type": "phrase_any", "values": ["create a token", "create two tokens"]},
      "weight": 0.8,
      "confidence": 0.75,
      "evidence_label": "O texto Oracle indica criação de fichas.",
      "requires_review": false
    }
  ]
}
```

Evitar regex complexa e frágil espalhada no código. O formato inicial pode suportar predicados documentados (`phrase_any`, `phrase_all`, `type_line_contains`, `keyword_contains`, `numeric_threshold`, `field_equals`); ampliar somente com testes e validação de schema. Evitar avaliar expressões de código vindas de JSON (`eval`). Regras que exigem interpretação contextual devem sinalizar revisão manual.

### 7.5 `scoring.json`

```json
{
  "schema_version": 1,
  "scoring_version": "1.0.0",
  "normalization": {"minimum": 0, "maximum": 100, "round_digits": 1},
  "commander_levels": {
    "primary": 1.0,
    "secondary": 0.65,
    "complementary": 0.35
  },
  "components": {
    "strategy_evidence": 0.40,
    "shared_synergy_tags": 0.30,
    "role_coverage": 0.15,
    "commander_text_support": 0.15
  },
  "thresholds": {
    "strong": 70,
    "moderate": 40,
    "weak": 15
  },
  "caps": {
    "single_generic_keyword_max": 20,
    "type_line_only_max": 15,
    "single_weak_rule_max": 25
  }
}
```

Pesos e exemplos são valores iniciais a validar, não alegações de balanceamento. As componentes ativas devem somar 1.0, salvo se o algoritmo documentar normalização pelo total de pesos ativos. Validar todas as referências, faixas e somas ao carregar.

## 8. Classificação automática e revisão manual

### 8.1 Pipeline automático

1. Receber carta normalizada, sem substituir o texto Oracle original.
2. Aplicar regras elegíveis e coletar evidências com regra, campo, resultado, confiança e contribuição.
3. Agregar múltiplas evidências por destino sem contar duplicatas equivalentes várias vezes.
4. Aplicar limites para evidências fracas/genéricas e guardar candidatos, inclusive quando abaixo do limiar, para auditoria.
5. Converter evidências em classificações propostas por estratégia, tag e função.
6. Aplicar overrides manuais por campo e dimensão.
7. Produzir resultado efetivo com origem (`automatic`, `manual`, `mixed`) e explicação.
8. Persistir a versão de regras/catálogo usada.

Regras devem trabalhar com campos estruturados sempre que possível. Para texto Oracle, normalizar caixa, espaços, apóstrofos e símbolos de mana sem destruir o original. Considerar face dupla, aventura, modalidades e cartas divididas conforme o modelo disponível; não unir textos de faces de forma que gere falso positivo sem indicar a origem da face.

### 8.2 Formato de evidência

Cada evidência deve conter no mínimo:

```json
{
  "rule_id": "tag_create_tokens_oracle_text",
  "target_type": "tag",
  "target_id": "create_tokens",
  "source_field": "oracle_text_normalized",
  "matched_value": "create a token",
  "weight": 0.8,
  "confidence": 0.75,
  "requires_review": false
}
```

Evitar guardar uma cópia excessiva do texto completo em cada evidência; armazenar fragmento curto ou referência a campo e valor de match. Respeitar proveniência dos dados da carta.

### 8.3 Overrides manuais

Suportar no mínimo:

- Fixar inclusão e nível/confiança de uma estratégia, tag ou função.
- Excluir explicitamente uma inferência automática.
- Remover override para voltar ao cálculo automático.
- Registrar razão opcional, data e autor quando disponível.
- Mostrar conflito entre sugestão automática e decisão manual.

Ordem de precedência: exclusão manual explícita > inclusão/valor manual explícito > resultado automático. Ao resolver, manter a classificação automática acessível para comparação. Overrides devem usar IDs validados pelo catálogo; IDs desconhecidos geram erro útil, nunca criação silenciosa.

### 8.4 Confiança e classificação final

Confiança não é probabilidade calibrada por padrão. Até que exista calibração empírica, nomear como `confidence_score` ou `strength_score` e documentar que é uma escala ordinal heurística. Não afirmar, por exemplo, “75% de chance”. O estado de revisão deve ser independente da pontuação.

## 9. Afinidade de comandantes

O comandante deve ser avaliado como fonte central de direção do deck, diferente de uma carta incluída no deck. Para cada comandante, permitir várias estratégias e tags, com nível `primary`, `secondary` ou `complementary` e evidências textuais/estruturadas.

Requisitos:

- Uma carta lendária pode suportar mais de uma estratégia.
- Afinidade primária exige evidência direta ou capacidade repetível claramente alinhada; uma palavra-chave incidental não basta.
- Identidade de cor é metadado de legalidade, não evidência de sinergia.
- Comandante com texto genérico pode ter várias afinidades complementares, não necessariamente primárias.
- Permitir classificação manual e explicação, igual ao pipeline das cartas.
- Suportar parceiros/backgrounds apenas se o modelo do projeto já contemplar essa regra; não inferir combinação automaticamente nesta etapa.

Forma lógica sugerida:

```json
{
  "oracle_id": "example-oracle-id",
  "strategy_affinities": [
    {"strategy_id": "tokens", "level": "primary", "score": 0.92, "evidence_ids": ["commander_rule_tokens"]},
    {"strategy_id": "aristocrats", "level": "secondary", "score": 0.61, "evidence_ids": ["commander_rule_death"]}
  ],
  "tag_ids": ["create_tokens", "creature_death"]
}
```

Os IDs de exemplo são ilustrativos. Não gravar dados de exemplo como se fossem registros reais da coleção.

## 10. Pontuação determinística de sinergia

### 10.1 Entradas e filtros

Para pontuar carta `c` para comandante `m` e objetivo/estratégia `s`, receber:

- Afinidades de `m` com estratégias/tags/funções.
- Evidências/classificações de `c`.
- Versão do catálogo e parâmetros de score.
- Opcionalmente, prioridade de estratégias escolhida pelo usuário e funções ainda necessárias no deck.

Antes do score, excluir ou sinalizar como inelegível carta fora da identidade de cor, ilegal no formato selecionado ou sem disponibilidade quando o modo “somente coleção” estiver ativo. A propriedade física (cópias disponíveis) deve limitar quantidade, sem alterar sinergia intrínseca.

### 10.2 Componentes propostos

Calcular componentes em escala de 0 a 1:

- `E`: evidência direta da carta para estratégia-alvo, agregada e limitada.
- `T`: sobreposição ponderada entre tags relevantes do comandante e tags da carta.
- `R`: contribuição para funções desejadas/necessárias no deck, se esse contexto estiver disponível; caso contrário, neutralizar ou omitir e renormalizar pesos ativos.
- `C`: suporte textual/estrutural do comandante à estratégia e à mecânica da carta.
- `U`: prioridade explícita do usuário, se fornecida. Deve ser documentada como parâmetro de preferência, não como evidência da carta.

Fórmula de referência:

```text
base = (wE*E + wT*T + wR*R + wC*C) / soma(pesos das componentes disponíveis)
commander_factor = nível de afinidade aplicável (primária 1.00, secundária 0.65, complementar 0.35)
final = clamp(0, 100, round(100 * base * commander_factor + user_priority_adjustment, 1))
```

Se o comandante não tiver afinidade registrada para a estratégia, não presumir fator 1.0. Marcar score como sem afinidade conhecida ou aplicar uma política explícita configurada (padrão recomendado: não ranquear como sinergia específica do comandante até existir afinidade). Para seleção sem estratégia-alvo, computar um score agregado somente a partir das estratégias declaradas como ativas pelo usuário; não inferir estratégia automaticamente sem explicar.

`T` pode ser calculado por Jaccard ponderado ou outra fórmula explicitamente versionada. Exemplo de Jaccard ponderado:

```text
T = soma(peso(tag) para tags em interseção) / soma(peso(tag) para tags na união)
```

Reduzir dupla contagem quando várias regras equivalentes detectam a mesma tag. Caps configuráveis limitam casos como tipo de carta sozinho, keyword genérica isolada ou correspondência fraca.

### 10.3 Explicabilidade

Para cada resultado, retornar uma estrutura estável, por exemplo:

```json
{
  "card_oracle_id": "example-oracle-id",
  "commander_oracle_id": "example-commander-id",
  "strategy_id": "tokens",
  "score": 74.5,
  "tier": "strong",
  "eligible": true,
  "catalog_version": "1.0.0",
  "components": {
    "strategy_evidence": 0.8,
    "shared_synergy_tags": 0.7,
    "role_coverage": 0.5,
    "commander_text_support": 1.0
  },
  "reasons": [
    {"code": "TAG_OVERLAP", "tag_id": "create_tokens", "contribution": 18.0},
    {"code": "STRATEGY_RULE", "rule_id": "card_creates_tokens", "contribution": 22.0}
  ],
  "manual_adjustments": []
}
```

Ordenar resultados com desempate estável, por exemplo: score decrescente, disponibilidade decrescente, nome normalizado crescente e Oracle ID crescente. Não depender da ordem retornada pelo banco.

### 10.4 Restrições e cuidados

- Não deixar keyword genérica ou tipo de carta isolado dominar score.
- Não pontuar propriedade “é lendária” como afinidade por qualquer estratégia.
- Não usar CMC, cor ou raridade como sinergia, salvo regra de produto explicitamente motivada e testada.
- Funções básicas como ramp e remoção podem aumentar utilidade geral, mas não devem fingir sinergia temática forte.
- Separar “sinergia com comandante” de “carta necessária para equilibrar o deck”.
- Não usar LLM como árbitro da pontuação. Uma futura camada de IA pode redigir explicações, sem mudar os valores calculados.

## 11. Integração com banco e dados da coleção

1. Inspecionar tabelas, chaves, migrações e identificador de carta já usados.
2. Usar Oracle ID como vínculo lógico quando disponível; manter IDs de impressão/coleção para versão física e quantidade possuída.
3. Implementar migração aditiva, criar índices e constraints, e manter caminho de rollback/documentação.
4. Não iniciar rede ou baixar dados no carregamento do motor de classificação. Dados Oracle entram pela integração existente em etapa própria.
5. Permitir reclassificar incrementalmente cartas cujo texto/dados, regras ou versão de catálogo mudaram.
6. Persistir a versão do ruleset/catálogo e invalidar somente resultados afetados.
7. Toda recomendação em modo coleção deve consultar disponibilidade real e respeitar quantidade e cópias já usadas no deck.

## 12. Testes e critérios de aceitação

Não encerrar a implementação só porque módulos importam. Incluir testes unitários, de configuração, de banco e regressão com fixtures pequenas e determinísticas. Não depender de API externa.

### 12.1 Configuração e catálogo

- JSON válido e todos os 15 IDs carregam.
- IDs duplicados, referência a tag inexistente, pesos fora de faixa, enum inválido e schema version não suportada falham com mensagem clara.
- Renomear rótulo de exibição não muda IDs nem resultados.
- Configuração não altera banco até ser carregada/validada com sucesso.

### 12.2 Classificação

- Casos positivos, negativos e ambíguos por estratégia/tag/função.
- Falso positivo: criatura com flying sem outra evidência não vira automaticamente Voltron.
- Falso positivo: carta Artifact sem payoff/interação não vira automaticamente afinidade forte de Artifacts.
- Burn distingue dano direto não-combate de dano de combate.
- Ramp distingue aceleração de mana de land drop normal.
- Reanimator exige evidência de cemitério/retorno, não somente uma criatura cara.
- Voltron valoriza buffs/proteção/evasão concentrados; proteção global isolada tem evidência menor.
- Combos não são declarados infinitos automaticamente sem regra/curadoria explícita.
- Overrides manuais incluem, excluem e revertem classificação sem apagar resultado automático.
- Reexecução com mesmas entradas e versões produz saída idêntica.

### 12.3 Score e deckbuilding

- Pontuação sempre está entre 0 e 100; componentes e arredondamento obedecem configuração.
- Afinidade primária supera secundária, que supera complementar em condições equivalentes.
- Carta legal/compatível com maior afinidade aparece acima de uma correspondência incidental.
- Cor incompatível/ilegalidade nunca é compensada por score alto.
- Disponibilidade limita recomendação no modo coleção e é explicada separadamente da sinergia.
- Desempate e ordenação são estáveis.
- A explicação recompõe o score dentro da tolerância de arredondamento.

### 12.4 Persistência

- Migração sobre banco vazio e banco existente de teste.
- Chaves estrangeiras, unicidade e rollback verificados.
- Atualização do catálogo conserva overrides e marca/recalcula classificações conforme política documentada.

## 13. Fases de implementação

Implementar incrementalmente e manter cada etapa utilizável. Não misturar alterações de interface, integração externa e regras do domínio numa única mudança.

### Fase 0 — Reconhecimento e proposta

- Ler instruções locais do repositório e inspecionar estado, convenções, banco e testes existentes.
- Identificar identificador canônico de carta, stack de dados e estratégia de migrações.
- Produzir resumo de compatibilidade desta especificação com o código atual e apontar conflitos antes de alterações estruturais.

### Fase 1 — Catálogo e configuração

- Criar os JSON versionados com 15 estratégias, tags e funções iniciais.
- Implementar modelos de domínio/carregador e validação cruzada.
- Adicionar testes de schema, IDs e relacionamentos.
- Nenhuma migração de banco nesta fase se o catálogo ainda não for persistido.

### Fase 2 — Classificação por regras

- Definir formato limitado de regra e normalização de texto.
- Implementar classificador puro que devolve evidências, sem gravar dados.
- Adicionar fixtures de cartas artificiais/representativas e testes positivos, negativos e ambíguos.
- Registrar explicitamente limitações das regras textuais.

### Fase 3 — Overrides e afinidade do comandante

- Definir API/modelo para estratégia, tag e função manual.
- Definir níveis de afinidade e explicações do comandante.
- Validar IDs e precedência; testar reversão para automático.

### Fase 4 — SQLite e reclassificação

- Preparar migrações aditivas e repositórios.
- Persistir versões, evidências, overrides e afinidades.
- Implementar atualização incremental e transacional; preservar dados existentes.

### Fase 5 — Motor de score

- Implementar filtros de elegibilidade, componentes configuráveis, limites e fórmula determinística.
- Retornar score e decomposição legível com desempate estável.
- Testar pesos, ausência de componentes, ausência de afinidade, legalidade, cores e disponibilidade.

### Fase 6 — Integração de produto

- Integrar consulta da coleção e fluxo de sugestão de cartas.
- Apresentar estratégia(s), funções, disponibilidade e razões do match.
- Permitir correção manual sem perder proveniência e sem depender de LLM.
- Considerar uma futura etapa de UX/IA apenas depois de o núcleo estar validado.

## 14. Instruções explícitas ao agente de código (Copilot/Codex)

Ao receber este documento, siga estas instruções:

1. Leia o documento inteiro e inspecione as instruções do repositório, código, banco, migrações e testes antes de editar arquivos.
2. Não implemente o projeto inteiro de uma só vez. Comece pela Fase 0 e relate a estrutura encontrada, pressupostos e conflitos. Depois proponha uma sequência curta para a próxima fase.
3. Não altere o banco real, não apague coleção, não baixe/importa dados externos e não execute operação destrutiva sem solicitação explícita.
4. Não invente formato de CSV, API, dados de cartas nem estrutura que não foi encontrada. Use fixtures controladas nos testes.
5. Mantenha estratégias, tags, funções, regras e pesos em JSON validado; não espalhe valores em código.
6. Use Python e SQLite conforme a arquitetura existente. Mantenha a camada de domínio desacoplada da interface e da rede.
7. Faça classificação automática como proposta rastreável e suporte ajuste manual persistente. Nunca misture os dois estados.
8. Faça score com algoritmo documentado, estável e explicável. Não delegue classificação, pesos ou ranking a LLM, serviço remoto ou aleatoriedade.
9. Preserve identidade de cor, legalidade Commander, quantidade possuída e Oracle ID como restrições/dados relevantes, sem confundi-los com sinergia.
10. Inclua testes para cada regra e cenário negativo importante. Execute somente os testes relevantes à fase alterada e relate o comando e resultado.
11. Prefira mudanças pequenas, migrações aditivas, tipos claros e funções puras. Não reformatar ou refatorar áreas sem relação com o escopo.
12. Ao concluir uma fase, informe: arquivos alterados, decisões, comportamento, migração necessária (se houver), testes executados e limitações conhecidas. Não avance de fase sem autorização do usuário quando a tarefa original for limitada a uma fase.

### Prompt inicial sugerido para o agente

```text
Leia integralmente `Mana_Architect_Diretrizes_Catalogo_Estrategias.md` e as instruções do repositório. Comece exclusivamente pela Fase 0: inspecione a estrutura, o banco, as migrações, o modelo de cartas e os testes existentes. Não implemente funcionalidades ainda. Entregue um resumo da arquitetura atual, identifique conflitos ou decisões em aberto em relação ao documento e proponha o menor próximo passo para a Fase 1. Não altere dados reais nem faça chamadas externas.
```

## 15. Decisões que podem evoluir sem quebrar o contrato

- Pesos de pontuação e limiares iniciais após revisão com resultados reais.
- Novas estratégias, tags, funções, regras e aliases, desde que IDs e versões sejam geridos.
- Mais predicados de classificação, se validados e cobertos por testes.
- Calibração empírica de score/confiança, se for documentada e versionada.
- Interface de revisão e relatório de explicações.

Não mudar silenciosamente: semântica de IDs existentes, precedência de override, restrições de elegibilidade, reprodutibilidade do score ou proveniência de classificação. Mudanças nesses contratos exigem nova versão, migração quando aplicável e testes de regressão.

