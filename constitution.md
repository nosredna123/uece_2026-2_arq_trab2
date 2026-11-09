# Constituição do Projeto EventHub

**Versão**: 1.0.0
**Vigência**: 2026-11-09
**Escopo**: arquitetura, documentação, implementação, validação e evolução do EventHub.

Esta constituição complementa [processo.md](./processo.md) e a [especificação preliminar](./especificação-preliminar-sistema.md). Em caso de conflito, requisitos e regras de negócio aprovados prevalecem; mudanças devem ser registradas por ADR e rationale versionados, sem apagar o histórico da decisão anterior.

## 1. Princípios obrigatórios

### 1.1 Processo orientado a domínio e rastreabilidade

- Seguir as etapas do processo do projeto: saneamento de domínio, descoberta estratégica, refinamento arquitetural, implementação AI-TDD e auditoria de regressão.
- Não iniciar contratos ou código de domínio antes de registrar glossário, invariantes e cenários BDD; regras de negócio e exceções relevantes requerem validação humana.
- Todo requisito RF/RN/RQ deve apontar para itens de plano, tarefas e evidências verificáveis.
- Artefatos de decisão devem manter rationale estruturado em JSON com `artifact`, `rationale`, `invariants` e `version` semântica. Alterações de regra ou premissa invalidam/atualizam explicitamente rationales afetados.
- Produzir ADRs com contexto, alternativas, decisão, evidências, justificativa e consequências. Não tratar uma sugestão gerada por IA como fato ou aprovação humana.

### 1.2 Arquitetura adequada e KISS

- Escolher fronteiras por capacidade de negócio, coesão, acoplamento, propriedade de dados e requisitos de qualidade; RFs não são uma lista automática de microsserviços.
- O protótipo executável deve conter Event Management, Registration, Attendance & Certification e Reporting. Identity & Access, Billing e Notification são especificados por contratos e simulados por mocks/fakes executáveis durante a integração.
- Aplicar **KISS**: usar a solução mais simples que satisfaça requisitos e invariantes; adicionar padrões, serviços, camadas e infraestrutura somente com rationale verificável. Não adicionar gateway, Kubernetes ou banco vetorial sem necessidade aprovada.
- Cada serviço é proprietário de seus dados. Integrações entre serviços usam APIs/contratos ou eventos; acesso direto ao banco de outro serviço é proibido.
- Bibliotecas, bancos e brokers compartilhados em ambiente local não alteram a propriedade lógica dos dados por serviço.

### 1.3 Consistência, segurança e falhas explícitas

- Nunca exceder a capacidade. Reserva, confirmação, cancelamento, expiração e promoção da fila devem ser atômicos/idempotentes e testados sob concorrência.
- Inscrição paga só é confirmada após resultado aprovado do Billing (real ou mock); recusa/expiração libera a vaga. Indisponibilidade de mocks de Notification ou Reporting não pode mascarar sucesso nem reverter uma inscrição confirmada.
- Aplicar **FailFast**: validar configuração e entradas na fronteira; falhar com erro explícito, observável e acionável. Não engolir exceções, retornar sucesso falso, substituir dependências ausentes por valores permissivos ou usar fallback silencioso. Fallback só é permitido se especificado, seguro, observável e coberto por testes.
- Autenticação/autorização é necessária nas operações protegidas, ainda que Identity & Access seja mockado no protótipo. Restringir dados por participante e organização.
- Não versionar senhas, tokens, chaves ou credenciais reais. Dados de desenvolvimento devem ser claramente fictícios.

### 1.4 Configuração de regras de negócio

- Todos os parâmetros que alteram regras ou políticas do domínio devem ser configuráveis por variáveis de ambiente carregadas do `.env` local ou do mecanismo equivalente no ambiente de execução. Não embutir esses valores em código nem manter cópias divergentes por serviço.
- Manter `.env` real fora do Git e fornecer `.env.example` com valores de demonstração/fictícios e descrição das variáveis. A configuração deve ser centralmente documentada, tipada/validada e rejeitada no startup se estiver ausente, inválida ou contraditória.
- Incluem-se, quando aplicáveis: prazo de expiração da reserva paga, habilitação da lista de espera, prazo de oferta da vaga, critério de elegibilidade/emissão do certificado e parâmetros dos fakes de pagamento/notificação. Regras por evento/atividade só podem ser configuradas individualmente se a especificação e os cenários aprovados definirem essa precedência; a origem da configuração deve ser explícita.
- Configuração operacional (portas, URLs, pool, broker) e segredos também são externos; segredos nunca recebem valor real no `.env.example`.

### 1.5 Qualidade e convenções de código

- Aplicar **DRY** sem criar abstrações prematuras: extrair duplicação real dentro de fronteiras adequadas; não acoplar serviços por bibliotecas de domínio compartilhadas.
- Python segue **PEP 8**, com formatter/linter configurado e verificado no CI.
- Java segue **Google Java Style** (convenções Java correspondentes de formatação, nomes, imports e organização), verificado por formatter/linter automatizado no CI.
- Código deve ser pequeno, coeso, tipado quando suportado, legível e com nomes do domínio. Comentários explicam decisões não óbvias, não repetem o código.
- Contratos HTTP usam OpenAPI; eventos e payloads usam AsyncAPI/JSON Schema ou formatos equivalentes validados. Compatibilidade de contrato é gate de CI.

### 1.6 Testes antes da implementação e gates

- Adotar AI-TDD: escrever testes de unidade, contrato e propriedade com base em invariantes antes do código de domínio; implementar depois e corrigir falhas com evidência, sem desabilitar testes para obter aprovação.
- Testar pelo menos: concorrência e limite de capacidade, transições de estado, idempotência/reprocessamento, pagamento aprovado/recusado/expirado, falha de dependência e integração entre Java e Python.
- Atingir mutation score mínimo de **95%** no núcleo de domínio. Exclusões devem ser justificadas, explícitas e aprovadas; o resultado de mutation testing é gate.
- CI valida build, testes, lint/format, contratos, configuração, fronteiras arquiteturais, rationales e auditoria semântica de mudanças contra decisões anteriores.
- Merge automático só pode ocorrer quando todos os gates determinísticos passam e a auditoria semântica confirma aderência integral aos rationales. Indisponibilidade, erro ou resultado inconclusivo de gate bloqueia aprovação. Mudança de regra/invariante exige arbitragem humana do responsável designado.

### 1.7 Operabilidade e aprendizado

- A execução e os testes do protótipo devem ser reproduzíveis localmente com instruções claras, dados sintéticos e sem dependência de nuvem paga.
- Propagar identificador de correlação da inscrição por APIs, mensagens e logs. Erros de integração devem ser diagnosticáveis e nunca convertidos silenciosamente em sucesso.
- Evidências de validação e trade-offs devem registrar ambiente, comandos, resultados e limitações; separar claramente serviços reais dos mocks.

## 2. Critérios de decisão e exceções

Uma exceção a princípio obrigatório exige ADR aprovado, rationale atualizado, risco explícito, escopo limitado e plano de validação. Nenhuma exceção pode permitir overbooking, segredo versionado, acesso cruzado injustificado a dados ou ocultação de falha.

## 3. Revisão da constituição

Revisar esta constituição quando houver mudança aprovada de requisito, fronteira de serviço ou processo. Cada revisão incrementa a versão e identifica princípios afetados; decisões anteriores permanecem rastreáveis por ADR e rationale.
