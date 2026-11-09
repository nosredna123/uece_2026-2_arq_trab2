# Plano de Implementação: EventHub

**Data**: 2026-11-09 | **Especificação**: [especificação-preliminar-sistema.md](./especificação-preliminar-sistema.md) | **Processo**: [processo.md](./processo.md)

## Resumo

Projetar a arquitetura do EventHub e implementar um protótipo executável que demonstre autenticação/autorização, publicação e consulta de atividades, reserva concorrente de vagas, pagamento simulado assíncrono e notificações desacopladas. O plano segue as etapas 0–4 de `processo.md`: saneamento do domínio, decomposição por capacidades, contratos e ADRs, desenvolvimento orientado a testes e gates de regressão.

Como recorte recomendado, serão **modelados sete serviços candidatos** e **implementados cinco**: Identity & Access, Event Management, Registration, Billing e Notification. Attendance & Certification e Reporting terão limites, responsabilidades e contratos documentados, mas não farão parte do protótipo executável. Essa redução mantém o fluxo principal integrado e respeita a permissão da especificação para deixar serviços adicionais apenas modelados.

As escolhas confirmadas para este plano são Java/Spring Boot e Python/FastAPI, RabbitMQ para eventos assíncronos, reserva paga com prazo padrão de 30 minutos configurável, promoção automática da lista de espera com prazo de oferta configurável e elegibilidade de certificado baseada em check-in registrado. O provedor de pagamento será simulado.

## Contexto técnico

- **Linguagens e runtime**: Java 21 com Spring Boot; Python 3.12+ com FastAPI.
- **Dependências principais**: PostgreSQL, RabbitMQ, Docker Compose; Flyway para migrações Java e Alembic para migrações Python.
- **Persistência**: propriedade de dados por serviço. Para execução local, um cluster PostgreSQL pode hospedar bancos lógicos separados e credenciais restritas por serviço; nenhum serviço acessa diretamente os dados de outro.
- **Contratos**: OpenAPI 3.0 para HTTP e AsyncAPI/JSON Schema para eventos.
- **Testes**: JUnit/jqwik, pytest/Hypothesis, testes de contrato, integração em containers, concorrência, falhas e mutation testing.
- **Plataforma-alvo**: execução local reproduzível com Docker Compose, sem dependência de nuvem paga ou Kubernetes.
- **Metas de desempenho e consistência**: validar capacidade sem overbooking sob solicitações simultâneas e comparar o comportamento de consultas/inscrições sob perfil de carga de até 20× a carga normal. A especificação não define latência-alvo; o limite de latência será registrado como decisão de arquitetura antes do teste de carga.
- **Restrições**: não armazenar credenciais reais no repositório; confirmar pagamentos antes de confirmar inscrições pagas; liberar reservas recusadas ou expiradas; falhas de notificação não revertem inscrições.

### Abordagem arquitetural proposta

Os sete serviços candidatos são Identity & Access, Event Management, Registration, Billing, Notification, Attendance & Certification e Reporting. Os cinco primeiros compõem o protótipo executável. A validação arquitetural da Etapa 1 poderá ajustar os limites antes da implementação, desde que preserve o escopo funcional e a justificativa documentada.

O fluxo pago proposto é: consulta HTTP de atividade → reserva atômica no Registration → evento persistido em outbox → publicação em RabbitMQ → Billing simula aprovação/recusa → evento de resultado → Registration confirma ou libera a vaga → Notification consome eventos sem bloquear o fluxo. O processamento será idempotente por identificador de inscrição/evento. Expiração e promoção de lista de espera serão controladas no Registration, com prazos configuráveis. A confirmação de uma vaga paga nunca depende da resposta de Notification.

Cada serviço terá sua própria fronteira de dados e migrações. O mecanismo de concorrência da reserva (por exemplo, atualização condicional de capacidade ou bloqueio transacional) será selecionado e registrado no ADR de consistência antes da implementação. Não haverá banco compartilhado com acesso cruzado.

## Verificação da constituição

Não foi encontrado `constitution.md` no repositório. Até que a equipe forneça uma constituição própria, os critérios da especificação do trabalho e os invariantes de `processo.md` são a base de governança:

| Princípio/critério | Verificação |
|---|---|
| Coesão e granularidade justificadas, sem criar um serviço por requisito | PASS — sete candidatos serão avaliados; cinco serão implementados no protótipo. |
| Sem overbooking e com transições de reserva/pagamento recuperáveis | PASS — reserva atômica, idempotência, outbox e teste concorrente são itens bloqueadores. |
| Propriedade de dados por serviço e contratos explícitos | PASS — dados isolados por serviço, OpenAPI e AsyncAPI/JSON Schema. |
| Falhas de notificações/relatórios não bloqueiam inscrições | PASS — Notification desacoplado; Reporting apenas modelado nesta entrega. |
| Segredos ausentes do repositório e execução local reproduzível | PASS — configuração externa, `.env.example` sem valores secretos e Docker Compose. |
| Validação humana das regras do domínio | PASS — regras de prazo, lista de espera e certificado foram confirmadas; T001 registra os cenários aprovados antes do desenho detalhado. |

## Diretrizes aplicadas

- **`spring-boot-scaffolding`**: guia destinado a scaffolding em projetos de rewrite, não a uma migração deste repositório. Suas recomendações pertinentes serão usadas como referência, não como requisito: Java 21, configuração externa, migrações versionadas, testes e estrutura simples por serviço. Referência: `skills/guidelines/spring-boot-scaffolding/SKILL.md`.
- Nenhuma diretriz de conversão/migração é aplicável: o trabalho é um protótipo greenfield, sem código legado a transformar.
- Filtro anti-superengenharia de `processo.md`: não adicionar gateway, banco vetorial, Kubernetes ou camadas táticas sem uma necessidade confirmada. Os rationales serão mantidos como JSON versionado no Git; busca vetorial não é necessária para o conjunto pequeno de decisões do protótipo.

## Etapas de implementação

### Fase 1 — Saneamento do domínio e arquitetura

#### 1.1 Consolidar glossário, invariantes e cenários BDD
- **Requisitos**: RF01–RF11, RN01–RN07, RQ01–RQ08
- **Insumos de desenho**: especificação preliminar; respostas de esclarecimento sobre o prazo de reserva, promoção de espera, certificado, stack e mensageria.
- **Descrição**: transformar regras em glossário e cenários Gherkin, incluindo inscrição gratuita/paga, recusa, timeout, cancelamento, lista de espera, concorrência, falha de Notification e acesso entre organizações. Registrar explicitamente as partes que ficarão somente modeladas. Solicitar validação humana do contrato de domínio antes de fechar os contratos técnicos.

#### 1.2 Delimitar contextos, serviços e fluxos
- **Requisitos**: RF01–RF11, RN01–RN07, RQ01–RQ06
- **Insumos de desenho**: glossário e cenários aprovados em 1.1.
- **Descrição**: produzir grafo de dependências conceituais, context map e diagrama de containers/serviços; comparar alternativas de granularidade e registrar capacidades que serão implementadas versus apenas modeladas. Documentar o fluxo de inscrição paga com recusa, timeout e indisponibilidade de dependências.

#### 1.3 Registrar decisões e rationales
- **Requisitos**: RQ01, RQ04–RQ08
- **Insumos de desenho**: opções de arquitetura e restrições da especificação.
- **Descrição**: criar 3–5 ADRs com contexto, alternativas, decisão, evidências, justificativa e consequências. Associar a cada artefato relevante um JSON versionado com `artifact`, `rationale`, `invariants` e `version`. Incluir decisões sobre limites de serviço, dados, comunicação, consistência, segurança e escopo reduzido do protótipo.

### Fase 2 — Contratos e fundação técnica

#### 2.1 Especificar APIs, eventos e dados
- **Requisitos**: RF01–RF11, RN01–RN07, RQ04–RQ07
- **Insumos de desenho**: context map e ADRs aprovados em 1.2–1.3.
- **Descrição**: definir OpenAPI para os cinco serviços implementados; definir AsyncAPI/JSON Schema para solicitação de pagamento, resultado de pagamento, alteração de inscrição e notificação; documentar também contratos/projeções de Attendance & Certification e Reporting. Delimitar entidades, propriedade dos dados, chaves de idempotência, estados e políticas de retenção por serviço.

#### 2.2 Criar fundação executável local
- **Requisitos**: RQ01, RQ05–RQ08
- **Insumos de desenho**: stack Java/Python confirmada e contratos da fase 2.1.
- **Descrição**: criar scaffolds independentes para os cinco serviços, configurações externas e migrações; criar Docker Compose para os serviços, PostgreSQL e RabbitMQ; adicionar dados sintéticos e verificações de saúde. Usar nomes de usuário/senhas somente por variáveis locais não versionadas e fornecer `.env.example` sem segredos.

#### 2.3 Estabelecer qualidade contínua
- **Requisitos**: RQ04–RQ08
- **Insumos de desenho**: convenções dos serviços e contratos.
- **Descrição**: criar CI para build, testes, linters, validação OpenAPI/AsyncAPI, validação de rationales, testes de fronteira arquitetural (ArchUnit para Java e import-linter para Python) e auditoria semântica por IA comparando o PR aos rationales versionados. Usar credencial de CI protegida, nunca versionada; falha ou indisponibilidade da auditoria não pode ser apresentada como aprovação. Permitir merge automático apenas quando todos os gates determinísticos e semânticos passarem; mudanças de invariantes exigem arbitragem humana.

### Fase 3 — US1: Identidade, autorização e catálogo

#### 3.1 Implementar identidade e controle de acesso
- **Requisitos**: RF01, RN07, RQ07
- **Insumos de desenho**: contrato de Identity & Access, política multi-organização e ADR de segurança.
- **Descrição**: implementar cadastro/autenticação mínima, perfis participante/organizador e autorização baseada na organização responsável. Propagar e validar identidade nas APIs protegidas; impedir que participantes leiam dados de outros participantes ou que organizadores alterem eventos de outra organização.

#### 3.2 Implementar eventos, atividades e consulta
- **Requisitos**: RF02–RF04, RQ01, RQ02, RQ05
- **Insumos de desenho**: contrato OpenAPI de Event Management e modelo de propriedade de dados.
- **Descrição**: implementar criação, edição, publicação e cancelamento de eventos; cadastro de atividades com horário, local, responsável, preço, capacidade e regras; implementar consulta de eventos publicados, programação, detalhes e disponibilidade sem expor acesso ao banco de Registration.

### Fase 4 — US2: Inscrição, capacidade, pagamento e notificações

#### 4.1 Implementar reserva e inscrição
- **Requisitos**: RF05–RF06, RN01, RN04, RQ02, RQ04
- **Insumos de desenho**: contrato de Registration, modelo de estados e ADR de concorrência.
- **Descrição**: implementar solicitação, consulta e cancelamento de inscrição; reservar capacidade de forma atômica e idempotente; tratar atividades gratuitas sem criar uma confirmação indevida de pagamento; manter estados explícitos para reserva pendente, confirmada, cancelada e expirada.

#### 4.2 Implementar lista de espera e liberação de vagas
- **Requisitos**: RF06, RN01, RN03–RN04, RQ04
- **Insumos de desenho**: regras de ordenação da fila e contrato de oferta de vaga.
- **Descrição**: ao liberar vaga, oferecer automaticamente ao próximo elegível; tornar a oferta e seu prazo configuráveis; expirar ofertas não aceitas e promover o próximo sem exceder a capacidade. Garantir idempotência em cancelamento, expiração e reprocessamento.

#### 4.3 Implementar pagamento simulado assíncrono
- **Requisitos**: RF07, RN02–RN03, RQ03–RQ05
- **Insumos de desenho**: eventos de solicitação/resultado, ADR de consistência e configuração de prazo padrão de 30 minutos.
- **Descrição**: publicar solicitações de pagamento pelo outbox do Registration; processar no Billing em Python/FastAPI com resultados determinísticos de aprovado/recusado; publicar resultado e atualizar a inscrição somente após confirmação válida. Expirar reservas pendentes após prazo configurável e liberar a vaga sem confirmar pagamento recusado ou expirado.

#### 4.4 Implementar notificações desacopladas
- **Requisitos**: RF08, RN05, RQ01, RQ03, RQ05
- **Insumos de desenho**: contrato de eventos e ADR de resiliência.
- **Descrição**: implementar Notification em Python/FastAPI como consumidor assíncrono de eventos relevantes; registrar tentativas e falhas com idempotência. A indisponibilidade ou atraso de Notification não deve desfazer nem bloquear uma inscrição ou confirmação.

### Fase 5 — US3/US4: Capacidades modeladas fora do protótipo

#### 5.1 Definir contratos de check-in e certificação
- **Requisitos**: RF09–RF10, RN06, RQ01, RQ05
- **Insumos de desenho**: regras de inscrição/presença e critério validado de certificado por check-in.
- **Descrição**: documentar os contextos Attendance & Certification, contratos de check-in idempotente e emissão elegível após atividade; mostrar propriedade dos dados e eventos necessários. Não implementar um serviço executável nesta entrega; explicitar essa limitação na documentação e demonstração.

#### 5.2 Definir relatórios operacionais
- **Requisitos**: RF11, RQ01, RQ03, RQ05–RQ06
- **Insumos de desenho**: fontes de eventos e propriedade de dados por serviço.
- **Descrição**: modelar Reporting como consumidor/projeção independente para inscrições, pagamentos, ocupação e comparecimento; documentar contrato, atualização eventual e indisponibilidade isolada. Não consultar bancos de outros serviços diretamente nem implementar o serviço no protótipo.

### Fase 6 — Integração, validação e entrega

#### 6.1 Demonstrar o fluxo integrado e os critérios de qualidade
- **Requisitos**: RF05–RF08, RN01–RN05, RQ02–RQ06
- **Insumos de desenho**: contratos, APIs e serviços das fases 3–4.
- **Descrição**: executar o fluxo integrado de consulta → reserva → pagamento simulado → confirmação → notificação; demonstrar recusa e expiração com liberação da vaga, falha de Notification sem rollback e promoção de lista de espera. Executar teste concorrente que prove que confirmações/reservas nunca excedem a capacidade e ensaio de carga com comparação até 20× o perfil normal definido.

#### 6.2 Finalizar segurança, observabilidade e documentação
- **Requisitos**: RF01, RQ05–RQ08
- **Insumos de desenho**: serviços implementados, rationales e resultados de validação.
- **Descrição**: propagar correlation/trace ID por HTTP e eventos; registrar logs estruturados úteis para seguir uma inscrição; validar ausência de segredos; completar instruções reprodutíveis, dados de demonstração, documentação de APIs, diagramas, ADRs e relatório de trade-offs. Registrar evidências reproduzíveis e a declaração de uso de IA.

## Estrutura de projeto proposta

```text
docs/
  architecture/
    context-map.md
    containers.puml
    paid-registration-flow.puml
    service-boundaries.md
  adr/
    ADR-001-service-boundaries.md
    ADR-002-data-ownership.md
    ADR-003-registration-consistency.md
    ADR-004-messaging-and-resilience.md
    ADR-005-prototype-scope.md
  rationales/
    ADR-001.json
    ADR-002.json
    ADR-003.json
    ADR-004.json
    ADR-005.json
  contracts/
    openapi/
    asyncapi/
    schemas/
  validation/
    evidence.md
    trade-offs.md
services/
  identity-java/
  event-management-java/
  registration-java/
  billing-python/
  notification-python/
  attendance-certification-model/  # documentação/contratos, sem serviço executável
  reporting-model/                 # documentação/contratos, sem serviço executável
infra/
  docker-compose.yml
  .env.example
tests/
  contracts/
  integration/
  concurrency/
```

## Estratégia de testes

- **Tipo de aplicação**: APIs REST e mensageria; sem interface web no escopo do protótipo.
- **Jornadas críticas**:
  1. Organizador autenticado cria e publica evento/atividade; participante consulta atividades publicadas.
  2. Participante solicita inscrição gratuita e consulta seu estado; outra identidade não acessa seus dados.
  3. Reserva paga é publicada via RabbitMQ e aprovada pelo simulador; inscrição só confirma após o resultado aprovado.
  4. Recusa ou expiração libera a capacidade; com lista de espera habilitada, a próxima pessoa recebe uma oferta válida.
  5. Notification indisponível não bloqueia a confirmação; eventos repetidos não duplicam inscrição, pagamento ou notificação.
- **Stack de validação primária**: JUnit e pytest para unidade; PostgreSQL/RabbitMQ com Docker Compose para integração; validação OpenAPI/AsyncAPI; ArchUnit e import-linter; PIT para Java e ferramenta de mutation testing Python escolhida no scaffold. Alvo de mutation score ≥95% no núcleo de domínio, alinhado a `processo.md`, com exclusões justificadas e explícitas.
- **Infraestrutura/fallback**: integração primária com PostgreSQL e RabbitMQ reais em containers; testes unitários com doubles determinísticos são fallback rápido, mas não substituem o teste ponta a ponta de aceitação. Pré-requisito da validação completa: Docker funcional.
- **Navegador**: não aplicável, pois não há UI no escopo. Validar APIs por clientes automatizados e OpenAPI.
- **Dados de teste**: fixtures sintéticas e isoladas por execução; identificadores de inscrição/evento determinísticos para cenários idempotentes; limpeza/reset de banco entre execuções; sem dados pessoais reais.
- **Critérios de aceite**: nenhum excesso de capacidade sob concorrência; confirmação de inscrição paga somente depois de aprovação; recusa/expiração libera vaga; evento duplicado não duplica efeitos; Notification indisponível não reverte inscrição; cinco serviços sobem localmente com instruções documentadas; contratos entre Java e Python são validados; nenhum segredo real é versionado.
- **Lacunas conhecidas**: RF09, RF10 e RF11 terão evidência de arquitetura e contratos, não de execução; teste de carga verifica a tendência/escala seletiva contra baseline definida, pois não há latência-alvo absoluta na especificação; provedores externos e canais de notificação serão simulados.
- **Revisão final**: confirmar rastreabilidade de RF/RN/RQ, diagramas e contratos coerentes com código, testes concorrentes e de falha reproduzíveis, rationales atualizados e limitações declaradas no relatório e na demonstração.

## Tarefas

### Fase 1 — Domínio e arquitetura

- [ ] T001 [Plan:1.1] Consolidar glossário, invariantes e cenários Gherkin para inscrição gratuita/paga, recusa, timeout, cancelamento, lista de espera, concorrência, notificações e autorização em `docs/architecture/domain-scenarios.md`; registrar validação do responsável de negócio.
- [ ] T002 [Plan:1.2] Desenhar grafo de dependências, limites dos sete serviços candidatos, recorte dos cinco implementados e diagrama de containers em `docs/architecture/context-map.md`, `docs/architecture/service-boundaries.md` e `docs/architecture/containers.puml`.
- [ ] T003 [Plan:1.2] Documentar fluxo de inscrição paga, incluindo recusa, timeout e indisponibilidade de dependências, em `docs/architecture/paid-registration-flow.puml` e `docs/architecture/paid-registration-flow.md`.
- [ ] T004 [P] [Plan:1.3] Registrar ADRs 001–005 com alternativas, decisão, evidências e consequências em `docs/adr/ADR-001-service-boundaries.md` a `docs/adr/ADR-005-prototype-scope.md`.
- [ ] T005 [Plan:1.3] Criar os rationales JSON versionados correspondentes aos ADRs em `docs/rationales/ADR-001.json` a `docs/rationales/ADR-005.json`, incluindo `artifact`, `rationale`, `invariants` e `version`.

### Fase 2 — Contratos e fundação técnica

- [ ] T006 [P] [Plan:2.1] Definir contratos OpenAPI dos serviços Identity & Access, Event Management e Registration em `docs/contracts/openapi/identity.yaml`, `docs/contracts/openapi/event-management.yaml` e `docs/contracts/openapi/registration.yaml`.
- [ ] T007 [P] [Plan:2.1] Definir contratos OpenAPI e AsyncAPI/JSON Schema de Billing, Notification, Attendance & Certification e Reporting em `docs/contracts/openapi/`, `docs/contracts/asyncapi/eventhub.yaml` e `docs/contracts/schemas/`.
- [ ] T008 [Plan:2.1] Documentar estados, chaves idempotentes, propriedade de dados, migrações e acesso permitido por serviço em `docs/architecture/data-ownership.md`.
- [ ] T009 [Plan:2.2] Criar `infra/docker-compose.yml` para PostgreSQL, RabbitMQ e os cinco serviços; adicionar verificações de saúde, volumes locais e configurações por variáveis de ambiente.
- [ ] T010 [P] [Plan:2.2] Inicializar scaffolds Java 21/Spring Boot para Identity & Access, Event Management e Registration em `services/identity-java/`, `services/event-management-java/` e `services/registration-java/`, com testes, configuração externa e migrações Flyway.
- [ ] T011 [P] [Plan:2.2] Inicializar scaffolds Python/FastAPI para Billing e Notification em `services/billing-python/` e `services/notification-python/`, com testes, configuração externa e migrações Alembic quando houver persistência.
- [ ] T012 [Plan:2.2] Adicionar `infra/.env.example`, dados sintéticos e instruções iniciais de build/execução em `README.md`; confirmar que nenhum valor secreto está incluído.
- [ ] T013 [Plan:2.3] Configurar CI em `.github/workflows/ci.yml` para build, testes, linters, contratos, rationales e auditoria semântica do PR contra os rationales; usar credencial protegida, bloquear aprovação em caso de falha/indisponibilidade e permitir merge automático somente após todos os gates aprovados, escalonando alterações de invariantes para validação humana.
- [ ] T014 [Plan:2.3] Adicionar testes de fronteira arquitetural com ArchUnit em `services/` Java e import-linter em `services/` Python; falhar o CI em dependências entre domínios/serviços não autorizadas.

### Fase 3 — US1: Identidade e catálogo

- [ ] T015 [US1] [Plan:3.1] Escrever testes de contrato e autorização para cadastro, autenticação, perfis e limites por participante/organização em `services/identity-java/src/test/`.
- [ ] T016 [US1] [Plan:3.1] Implementar cadastro, autenticação, perfis e autorização multi-organização conforme os testes em `services/identity-java/src/`.
- [ ] T017 [US1] [Plan:3.2] Escrever testes de contrato e domínio para ciclo de vida de eventos/atividades e consulta pública em `services/event-management-java/src/test/`.
- [ ] T018 [US1] [Plan:3.2] Implementar persistência, migrações e endpoints de evento/atividade conforme os testes em `services/event-management-java/src/`.

### Fase 4 — US2: Inscrições, pagamentos e notificações

- [ ] T019 [US2] [Plan:4.1] Escrever testes JUnit/jqwik para reserva, cancelamento, inscrição gratuita, idempotência e invariantes de capacidade em `services/registration-java/src/test/`.
- [ ] T020 [US2] [Plan:4.1] Implementar reserva atômica/idempotente, consulta de estado, cancelamento e atividade gratuita conforme os testes em `services/registration-java/src/`, aplicando o mecanismo decidido no ADR-003.
- [ ] T021 [US2] [Plan:4.2] Escrever testes property-based e concorrentes para ordenação da fila, cancelamento, expiração e oferta de vaga em `services/registration-java/src/test/`.
- [ ] T022 [US2] [Plan:4.2] Implementar lista de espera ordenada, promoção automática e prazo configurável para aceitar oferta em `services/registration-java/src/`, conforme os testes.
- [ ] T023 [US2] [Plan:4.3] Escrever testes para outbox, resultados de pagamento aprovados/recusados, duplicidade, expiração e transições de estado em `services/registration-java/src/test/` e `services/billing-python/tests/`.
- [ ] T024 [Plan:4.3] Implementar outbox transacional e publicação idempotente de solicitação de pagamento em `services/registration-java/src/`, conforme os testes.
- [ ] T025 [US2] [Plan:4.3] Implementar consumidor de pagamento em `services/billing-python/`, simulador determinístico de aprovação/recusa e publicação do resultado conforme AsyncAPI e testes.
- [ ] T026 [US2] [Plan:4.3] Implementar consumo do resultado, confirmação somente após aprovação e expiração com prazo padrão de 30 minutos configurável em `services/registration-java/src/`; liberar reservas recusadas/expiradas uma única vez.
- [ ] T027 [US2] [Plan:4.4] Escrever testes de idempotência, indisponibilidade e reprocessamento para Notification em `services/notification-python/tests/` e `tests/integration/`.
- [ ] T028 [US2] [Plan:4.4] Implementar consumidor idempotente de eventos e registro de notificações em `services/notification-python/`, sem chamadas síncronas obrigatórias no fluxo de inscrição.

### Fase 5 — US3/US4: Serviços modelados

- [ ] T029 [US3] [Plan:5.1] Documentar Attendance & Certification, contrato de check-in idempotente e regra de emissão após atividade baseada em check-in em `docs/architecture/attendance-certification.md` e `docs/contracts/openapi/attendance-certification.yaml`; marcar como não implementado no protótipo.
- [ ] T030 [US4] [Plan:5.2] Documentar Reporting, projeções para inscrições/pagamentos/ocupação/comparecimento e contrato de consulta em `docs/architecture/reporting.md` e `docs/contracts/openapi/reporting.yaml`; garantir que relatórios não leem bancos de outros serviços e marcar como não implementado.

### Fase 6 — Integração e entrega

- [ ] T031 [Plan:6.1] Criar teste ponta a ponta em `tests/integration/paid-registration-flow/` que inicia o ambiente local e valida consulta → reserva → RabbitMQ → pagamento aprovado → confirmação → notificação.
- [ ] T032 [Plan:6.1] Criar cenários de integração para recusa, expiração, promoção de lista de espera e Notification indisponível em `tests/integration/failure-scenarios/`.
- [ ] T033 [Plan:6.1] Executar teste de concorrência em `tests/concurrency/` com capacidade controlada e concorrência reproduzível; registrar que reservas confirmadas/ofertadas nunca superam a capacidade.
- [ ] T034 [Plan:6.1] Configurar mutation testing para Java e Python em `services/` e CI; exigir score ≥95% no núcleo de domínio ou documentar cada exclusão aprovada.
- [ ] T035 [Plan:6.1] Criar ensaio de carga repetível em `tests/load/` e registrar baseline, perfil de até 20× e resultados comparativos de consultas/inscrições em `docs/validation/evidence.md`.
- [ ] T036 [Plan:6.2] Propagar correlation/trace ID em APIs, mensagens e logs estruturados nos cinco serviços, incluindo `registrationId` para diagnóstico ponta a ponta.
- [ ] T037 [Plan:6.2] Completar `README.md` com requisitos, configuração segura, comandos de build/teste/execução, dados de demonstração, APIs e limitações do protótipo.
- [ ] T038 [Plan:6.2] Consolidar diagramas, ADRs, rationales, evidências de teste e análise dos trade-offs em `docs/validation/`; incluir declaração de uso de IA e roteiro de demonstração/defesa.

## Mapeamento de requisitos

| ID | Descrição | Itens do plano | Evidência prevista |
|---|---|---|---|
| RF01 | Cadastro, autenticação e perfis com permissões distintas | 1.1, 2.1, 3.1 | `services/identity-java/`, contratos OpenAPI e testes de autorização |
| RF02 | Criar, editar, publicar e cancelar eventos | 2.1, 3.2 | `services/event-management-java/` e contrato OpenAPI |
| RF03 | Cadastrar atividades e regras de inscrição | 2.1, 3.2 | `services/event-management-java/` e modelo de atividades |
| RF04 | Consultar eventos, programação, vagas e detalhes | 2.1, 3.2, 4.1 | APIs Event Management/Registration e teste integrado |
| RF05 | Solicitar, consultar e cancelar inscrições | 2.1, 4.1 | `services/registration-java/` e testes de estado |
| RF06 | Respeitar capacidade e oferecer lista de espera | 1.1, 4.1, 4.2, 6.1 | reserva atômica, promoção automática e teste concorrente |
| RF07 | Simular pagamentos e registrar aprovação, recusa ou expiração | 2.1, 4.3, 6.1 | `services/billing-python/`, eventos e testes de falha |
| RF08 | Notificar eventos relevantes sem bloquear operações essenciais | 2.1, 4.4, 6.1 | `services/notification-python/` e teste de indisponibilidade |
| RF09 | Registrar check-in sem duplicidade | 2.1, 5.1 | contrato e modelo idempotente de Attendance (somente modelado) |
| RF10 | Emitir certificado a elegíveis após evento/atividade | 2.1, 5.1 | contrato e regra de elegibilidade por check-in (somente modelado) |
| RF11 | Disponibilizar relatórios operacionais | 2.1, 5.2 | contrato/projeção Reporting (somente modelado) |
| RN01 | Nunca exceder capacidade sob concorrência | 1.1, 4.1, 4.2, 6.1 | invariantes, reserva atômica e teste concorrente |
| RN02 | Confirmar inscrição paga somente após pagamento; expirar reserva pendente | 1.1, 4.3 | transições, prazo configurável e teste de expiração |
| RN03 | Recusa/expiração libera reserva sem confirmação indevida | 1.1, 4.2, 4.3, 6.1 | testes de recusa, timeout e liberação única |
| RN04 | Cancelamento atualiza disponibilidade e pode promover fila | 1.1, 4.2 | fluxo e testes de cancelamento/promoção |
| RN05 | Falha de notificações não desfaz confirmação | 1.1, 4.4, 6.1 | consumo assíncrono e teste com Notification indisponível |
| RN06 | Certificar somente elegíveis após atividade | 1.1, 5.1 | contrato e critério de check-in documentados |
| RN07 | Restringir acesso por participante e organização | 1.1, 3.1 | autorização multi-organização e testes negativos |
| RQ01 | Evolução e implantação independente | 1.2, 1.3, 2.2, 5.1, 5.2 | limites de serviço, scaffolds e contexto map |
| RQ02 | Escalabilidade seletiva de consultas/inscrições | 1.2, 3.2, 4.1, 6.1 | desenho de escalabilidade e ensaio comparativo até 20× |
| RQ03 | Resiliência a falhas de notificações/relatórios | 1.2, 4.3, 4.4, 5.2, 6.1 | eventos assíncronos e evidência de falha isolada |
| RQ04 | Consistência e recuperação de reservas/pagamentos | 1.3, 2.1, 4.1–4.3, 6.1 | ADR, outbox, idempotência e testes de estado |
| RQ05 | Interoperabilidade por contratos explícitos | 1.2, 2.1, 3.2, 4.3–4.4, 5.1–5.2 | OpenAPI, AsyncAPI/JSON Schema e testes de contrato |
| RQ06 | Rastreabilidade e diagnóstico ponta a ponta | 1.3, 5.2, 6.2 | correlation/trace ID e logs estruturados |
| RQ07 | Autenticação, autorização e proteção de segredos | 1.3, 2.2, 3.1, 6.2 | identidade, controle multi-organização e verificação de configuração |
| RQ08 | Execução local reproduzível | 2.2, 6.2 | Docker Compose, `.env.example` e README |

## Complexidade e desvios deliberados

| Desvio/decisão | Motivo | Alternativa mais simples rejeitada |
|---|---|---|
| Cinco serviços executáveis; Attendance & Certification e Reporting apenas modelados | Manter um fluxo representativo ponta a ponta dentro do escopo de protótipo permitido | Implementar todos os sete aumenta o custo sem fortalecer a demonstração principal de consistência |
| Rationales JSON versionados no Git, sem banco vetorial | O pequeno número de decisões do trabalho é pesquisável por arquivos e histórico de versões | Banco vetorial adicionaria infraestrutura sem necessidade de recuperação semântica em escala |
| Sem gateway, Kubernetes ou pagamento real no protótipo | Não são necessários para provar requisitos centrais e aumentariam operação/segredos | Adotá-los sem demanda seria superengenharia |
