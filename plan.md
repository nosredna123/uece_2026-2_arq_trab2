# Plano de Implementação: EventHub

**Data**: 2026-11-09 | **Especificação**: [especificação-preliminar-sistema.md](./especificação-preliminar-sistema.md) | **Processo**: [processo.md](./processo.md)

## Resumo

Projetar a arquitetura do EventHub e implementar um protótipo executável que demonstre autenticação/autorização, publicação e consulta de atividades, reserva concorrente de vagas, pagamento simulado assíncrono e notificações desacopladas. O plano segue as etapas 0–4 de `processo.md`: saneamento do domínio, decomposição por capacidades, contratos e ADRs, desenvolvimento orientado a testes e gates de regressão.

O desenho avaliará sete serviços candidatos. O protótipo executável conterá **Event Management, Registration, Attendance & Certification e Reporting**. **Identity & Access, Billing e Notification** serão especificados por contratos e simulados por mocks/fakes executáveis usados na integração; não serão serviços de domínio implementados. Assim, o fluxo demonstra capacidades reais de eventos, inscrições, presença/certificados e relatórios sem deixar de exercitar dependências externas.

As escolhas confirmadas para este plano são Java/Spring Boot e Python/FastAPI, RabbitMQ para eventos assíncronos, reserva paga com prazo padrão de 30 minutos configurável, promoção automática da lista de espera com prazo de oferta configurável e elegibilidade de certificado baseada em check-in registrado. O Billing será simulado; seus resultados poderão ser configurados para aprovação, recusa e timeout.

## Contexto técnico

- **Linguagens e runtime**: Java 21 com Spring Boot para Event Management e Registration; Python 3.12+ com FastAPI para Attendance & Certification e Reporting.
- **Dependências principais**:
  - **Spring Boot**: APIs HTTP, validação e injeção de dependências dos serviços Java.
  - **FastAPI**: APIs HTTP e validação de payloads dos serviços Python.
  - **PostgreSQL**: persistência relacional dos quatro serviços executáveis, com bancos/esquemas e credenciais separados por serviço.
  - **RabbitMQ**: transporte assíncrono de eventos para pagamento simulado, Notification mock, atualização de presença e projeções de relatório.
  - **Docker Compose**: inicialização local reprodutível de serviços e dependências.
  - **Flyway**: migrações versionadas dos serviços Java.
  - **Alembic**: migrações versionadas dos serviços Python que persistirem dados.
  - **OpenAPI 3.0**: documentação e validação dos contratos HTTP.
  - **AsyncAPI/JSON Schema**: documentação e validação dos eventos e payloads assíncronos.
  - **JUnit + jqwik**: testes Java unitários e baseados em propriedades.
  - **pytest + Hypothesis**: testes Python unitários e baseados em propriedades.
  - **Testcontainers**: execução isolada de PostgreSQL/RabbitMQ em testes de integração, quando disponível; Docker Compose permanece a opção reproduzível da suíte ponta a ponta.
  - **ArchUnit/import-linter**: verificação de dependências e limites arquiteturais em Java/Python.
  - **PIT e ferramenta Python de mutation testing**: medição da capacidade da suíte de detectar defeitos no núcleo de domínio.
  - **Google Java Style e PEP 8**: convenções de formatação e estilo verificadas no CI.
- **Persistência**: propriedade de dados por serviço. Para execução local, um cluster PostgreSQL pode hospedar bancos lógicos separados e credenciais restritas por serviço; nenhum serviço acessa diretamente os dados de outro.
- **Contratos**: OpenAPI 3.0 para HTTP e AsyncAPI/JSON Schema para eventos.
- **Testes**: JUnit/jqwik, pytest/Hypothesis, testes de contrato, integração em containers, concorrência, falhas e mutation testing, conforme [constitution.md](./constitution.md).
- **Plataforma-alvo**: execução local reproduzível com Docker Compose, sem dependência de nuvem paga ou Kubernetes.
- **Preparação da máquina**: Git, Docker Engine ou Docker Desktop e plugin Docker Compose v2 são os pré-requisitos obrigatórios. Os quatro serviços, os três mocks, PostgreSQL e RabbitMQ executam em containers; builds e testes de aceitação também devem ter comandos containerizados. JDK, Maven e Python locais são opcionais para desenvolvimento/depuração na IDE, não requisitos para executar o protótipo.
- **Metas de desempenho e consistência**: validar capacidade sem overbooking sob solicitações simultâneas e comparar o comportamento de consultas/inscrições sob perfil de carga de até 20× a carga normal. A especificação não define latência-alvo; o limite de latência será registrado como decisão de arquitetura antes do teste de carga.
- **Configuração de negócio**: todo parâmetro que afeta políticas de domínio é carregado e validado a partir de variáveis de ambiente definidas no `.env` local ou equivalente; `.env` é ignorado pelo Git e `.env.example` contém apenas valores fictícios.
- **Restrições**: não armazenar credenciais reais no repositório; confirmar pagamentos somente após aprovação do Billing mock; liberar reservas recusadas ou expiradas; falhas do Notification mock não revertem inscrições.

### Abordagem arquitetural proposta

Os sete serviços candidatos são Identity & Access, Event Management, Registration, Billing, Notification, Attendance & Certification e Reporting. Event Management e Registration serão implementados em Java/Spring Boot; Attendance & Certification e Reporting em Python/FastAPI. Identity & Access, Billing e Notification terão contratos explícitos e mocks/fakes executáveis. Os fakes devem permitir demonstrar sucesso e falhas esperadas (identidade válida/inválida, pagamento aprovado/recusado/expirado, notificações disponíveis/indisponíveis) sem serem confundidos com implementações de domínio.

O fluxo pago proposto é: consulta HTTP de atividade → reserva atômica no Registration → evento persistido em outbox → publicação em RabbitMQ → Billing mock simula aprovação/recusa/timeout → evento de resultado → Registration confirma ou libera a vaga → Notification mock consome eventos sem bloquear o fluxo. Registration publica eventos de inscrição para Attendance & Certification e Reporting; o check-in é idempotente e a elegibilidade de certificado exige check-in e atividade encerrada. Reporting mantém projeção própria, eventualmente consistente, sem ler bancos dos outros serviços. O processamento será idempotente por identificador de inscrição/evento. Expiração e promoção de lista de espera serão controladas no Registration, com prazos configuráveis. A confirmação de uma vaga paga nunca depende do mock de Notification.

Cada serviço executável terá sua própria fronteira de dados e migrações. O mecanismo de concorrência da reserva (por exemplo, atualização condicional de capacidade ou bloqueio transacional) será selecionado e registrado no ADR de consistência antes da implementação. Não haverá banco compartilhado com acesso cruzado. Todos os parâmetros de negócio, inclusive valores padrão configuráveis como `RESERVATION_EXPIRY_MINUTES`, `WAITLIST_ENABLED`, `WAITLIST_OFFER_EXPIRY_MINUTES` e `CERTIFICATE_ELIGIBILITY_POLICY`, devem ser lidos do `.env`/ambiente e validados no startup; nomes e valores efetivos são documentados no `.env.example`.

## Verificação da constituição

O projeto possui [constitution.md](./constitution.md), que formaliza o processo e os princípios KISS, DRY, FailFast, PEP 8, Google Java Style, configuração externa de regras de negócio e os gates de validação. O plano adere a ela:

| Princípio/critério | Verificação |
|---|---|
| KISS e fronteiras por capacidade de negócio | PASS — sete candidatos analisados; quatro executáveis e três mocks; não criar microsserviços por RF. |
| Sem overbooking e transições recuperáveis | PASS — reserva atômica, idempotência, outbox e teste concorrente são bloqueadores. |
| Propriedade de dados e contratos explícitos | PASS — dados isolados por serviço, OpenAPI e AsyncAPI/JSON Schema. |
| Resiliência e mocks explícitos | PASS — Notification é desacoplado; Reporting é executável com projeção própria. |
| FailFast e configuração externa | PASS — parâmetros de negócio no `.env`/ambiente, validados no startup; nenhum fallback silencioso. |
| Convenções, AI-TDD e mutation testing | PASS — PEP 8, Google Java Style, testes antes da implementação e score de mutação ≥95%. |
| Processo e revisão humana | PASS — cenários de domínio validados; mudanças de invariantes escalam para arbitragem humana. |

## Diretrizes aplicadas

- **`spring-boot-scaffolding`**: guia destinado a scaffolding em projetos de rewrite, não a uma migração deste repositório. Suas recomendações pertinentes serão usadas como referência, não como requisito: Java 21, configuração externa, migrações versionadas, testes e estrutura simples por serviço. Referência: `skills/guidelines/spring-boot-scaffolding/SKILL.md`.
- Nenhuma diretriz de conversão/migração é aplicável: o trabalho é um protótipo greenfield, sem código legado a transformar.
- Filtro anti-superengenharia de `processo.md`: não adicionar gateway, banco vetorial, Kubernetes ou camadas táticas sem uma necessidade confirmada. Os rationales serão mantidos como JSON versionado no Git; busca vetorial não é necessária para o conjunto pequeno de decisões do protótipo.
- Constituição do projeto: KISS, DRY, FailFast, PEP 8, Google Java Style, AI-TDD, gates de mutation/arquitetura/auditoria semântica e configuração de negócio no ambiente.

## Etapas de implementação

### Fase 0 — Preparação e verificação do ambiente

Esta fase prepara a máquina; não substitui nem renumera a **Etapa 0 de saneamento do domínio** de `processo.md`, contemplada na Fase 1 deste plano. Não depende de contratos ou código de domínio. A Fase 2 continua responsável por criar as imagens, os scaffolds e a composição da aplicação.

#### 0.1 Verificar e preparar os pré-requisitos obrigatórios
- **Requisitos**: RQ07, RQ08
- **Insumos de desenho**: stack e rota containerizada definidas neste plano; constituição do projeto.
- **Descrição**: inventariar sistema operacional, arquitetura, ferramentas e versões existentes. Documentar instalação/configuração de Git, Docker Engine ou Docker Desktop e plugin Compose v2 conforme o sistema operacional, consultando as instruções oficiais. Instalar somente componentes ausentes ou incompatíveis, com autorização para alterações de sistema, sem substituir instalações existentes indiscriminadamente. Validar `git --version`, `docker version`, `docker info` e `docker compose version`; verificar o contexto Docker e acesso ao daemon sem conceder permissões excessivas ou alterar portas/serviços de terceiros.

#### 0.2 Documentar execução em containers e desenvolvimento local opcional
- **Requisitos**: RQ05, RQ08
- **Insumos de desenho**: distribuição Java/Python dos quatro serviços reais.
- **Descrição**: definir a execução integral em containers como caminho oficial da equipe e da apresentação. Prever builds multi-stage, dependências e versões fixadas nas imagens; bibliotecas Python instaladas em imagem ou ambiente virtual, nunca globalmente no sistema. Documentar como alternativa opcional JDK 21, Maven Wrapper do projeto e Python em versão compatível com a imagem, com ambiente virtual isolado e seleção de interpretador/JDK na IDE. Essa alternativa não deve se tornar pré-requisito de aceitação nem exigir instalar PostgreSQL/RabbitMQ no host.

#### 0.3 Estabelecer o gate de prontidão da máquina
- **Requisitos**: RQ07, RQ08
- **Insumos de desenho**: pré-requisitos de 0.1 e política FailFast.
- **Descrição**: criar verificação reproduzível que execute um container de diagnóstico descartável e valide execução, rede e volume temporários, recursos disponíveis (CPU, memória e disco) e conflitos nas portas previstas, com mínimos e portas esperadas documentados no guia. Limpar apenas os recursos criados pela verificação. Registrar versões, comandos e resultados sem segredos. Docker/daemon inacessível, teste funcional malsucedido, conflito de porta ou recurso insuficiente bloqueia a prontidão com mensagem acionável e código de saída não zero; não trocar silenciosamente a infraestrutura. Documentar desde já a localização do `.env` local, a proteção de segredos e o futuro `.env.example`; o preenchimento e a validação das variáveis de negócio dependem da Fase 2. A aprovação deste gate comprova prontidão da máquina, não funcionamento de serviços ainda inexistentes.

### Fase 1 — Saneamento do domínio e arquitetura

#### 1.1 Consolidar glossário, invariantes e cenários BDD
- **Requisitos**: RF01–RF11, RN01–RN07, RQ01–RQ08
- **Insumos de desenho**: especificação preliminar; respostas de esclarecimento sobre o prazo de reserva, promoção de espera, certificado, stack e mensageria.
- **Descrição**: transformar regras em glossário e cenários Gherkin, incluindo inscrição gratuita/paga, recusa, timeout, cancelamento, lista de espera, concorrência, falha de Notification e acesso entre organizações. Registrar os três serviços que serão apenas especificados e mockados. Solicitar validação humana do contrato de domínio antes de fechar os contratos técnicos.

#### 1.2 Delimitar contextos, serviços e fluxos
- **Requisitos**: RF01–RF11, RN01–RN07, RQ01–RQ06
- **Insumos de desenho**: glossário e cenários aprovados em 1.1.
- **Descrição**: produzir grafo de dependências conceituais, context map e diagrama de containers/serviços; comparar alternativas de granularidade e registrar capacidades que serão implementadas versus apenas modeladas. Documentar o fluxo de inscrição paga com recusa, timeout e indisponibilidade de dependências.

#### 1.3 Registrar decisões e rationales
- **Requisitos**: RQ01, RQ04–RQ08
- **Insumos de desenho**: opções de arquitetura e restrições da especificação.
- **Descrição**: criar 3–5 ADRs com contexto, alternativas, decisão, evidências, justificativa e consequências. Associar a cada artefato relevante um JSON versionado com `artifact`, `rationale`, `invariants` e `version`. Incluir decisões sobre limites de serviço, dados, comunicação, consistência, segurança e escopo reduzido do protótipo.

### Fase 2 — Contratos e fundação técnica

#### 2.1 Especificar APIs, eventos, mocks e dados
- **Requisitos**: RF01–RF11, RN01–RN07, RQ04–RQ07
- **Insumos de desenho**: context map e ADRs aprovados em 1.2–1.3.
- **Descrição**: definir OpenAPI para os quatro serviços implementados e para os mocks de Identity & Access, Billing e Notification; definir AsyncAPI/JSON Schema para pagamento, inscrição, presença, certificado, relatórios e notificações; definir comportamento/controles de cada mock. Delimitar entidades, propriedade dos dados, chaves de idempotência, estados e políticas de retenção por serviço.

#### 2.2 Criar fundação executável local
- **Requisitos**: RQ01, RQ05–RQ08
- **Insumos de desenho**: stack Java/Python confirmada, gate de prontidão da Fase 0 aprovado e contratos da fase 2.1.
- **Descrição**: criar scaffolds independentes para os quatro serviços, imagens com build multi-stage, configurações externas e migrações; criar Docker Compose para os serviços, PostgreSQL, RabbitMQ e mocks, com comandos containerizados de build e testes de aceitação; adicionar dados sintéticos e verificações de saúde. Fixar versões de runtimes e dependências e fornecer runtime Java sem ferramentas de build e imagem Python sem dependências exclusivas de teste no estágio de execução. Usar `infra/.env.example` como modelo para o `.env` local ignorado pelo Git, sem credenciais reais; validar as configurações necessárias no startup e falhar explicitamente se estiverem ausentes ou inválidas.

#### 2.3 Estabelecer qualidade contínua
- **Requisitos**: RQ04–RQ08
- **Insumos de desenho**: convenções dos serviços e contratos.
- **Descrição**: criar CI para build, testes, linters/formatters (PEP 8 e Google Java Style), validação OpenAPI/AsyncAPI, validação de configuração e rationales, testes de fronteira arquitetural (ArchUnit para Java e import-linter para Python) e auditoria semântica por IA comparando o PR aos rationales versionados. Usar credencial de CI protegida, nunca versionada; falha ou indisponibilidade da auditoria não pode ser apresentada como aprovação. Permitir merge automático apenas quando todos os gates determinísticos e semânticos passarem; mudanças de invariantes exigem arbitragem humana.

### Fase 3 — US1: Identidade simulada e catálogo

#### 3.1 Especificar e simular identidade e controle de acesso
- **Requisitos**: RF01, RN07, RQ07
- **Insumos de desenho**: contrato de Identity & Access, política multi-organização e ADR de segurança.
- **Descrição**: documentar contrato, papéis e regras de autorização de Identity & Access e fornecer mock/fake de autenticação com perfis e organizações sintéticos. Validar o token/identidade mockada nas APIs protegidas e testar que participantes não leem dados de outros participantes e organizadores não alteram eventos de outra organização. Não criar serviço de identidade de domínio.

#### 3.2 Implementar Event Management
- **Requisitos**: RF02–RF04, RQ01, RQ02, RQ05
- **Insumos de desenho**: contrato OpenAPI de Event Management e modelo de propriedade de dados.
- **Descrição**: implementar criação, edição, publicação e cancelamento de eventos; cadastro de atividades com horário, local, responsável, preço, capacidade e regras; implementar consulta de eventos publicados, programação, detalhes e disponibilidade sem expor acesso ao banco de Registration.

### Fase 4 — US2: Inscrição, capacidade e dependências simuladas

#### 4.1 Implementar reserva e inscrição
- **Requisitos**: RF05–RF06, RN01, RN04, RQ02, RQ04
- **Insumos de desenho**: contrato de Registration, modelo de estados e ADR de concorrência.
- **Descrição**: implementar solicitação, consulta e cancelamento de inscrição; reservar capacidade de forma atômica e idempotente; tratar atividades gratuitas sem criar uma confirmação indevida de pagamento; manter estados explícitos para reserva pendente, confirmada, cancelada e expirada.

#### 4.2 Implementar lista de espera e liberação de vagas
- **Requisitos**: RF06, RN01, RN03–RN04, RQ04
- **Insumos de desenho**: regras de ordenação da fila e contrato de oferta de vaga.
- **Descrição**: ao liberar vaga, oferecer automaticamente ao próximo elegível; tornar a oferta e seu prazo configuráveis; expirar ofertas não aceitas e promover o próximo sem exceder a capacidade. Garantir idempotência em cancelamento, expiração e reprocessamento.

#### 4.3 Especificar Billing e implementar mock de pagamento
- **Requisitos**: RF07, RN02–RN03, RQ03–RQ05
- **Insumos de desenho**: eventos de solicitação/resultado, ADR de consistência e configuração de prazo padrão de 30 minutos.
- **Descrição**: documentar Billing e seus contratos, e implementar fake de pagamento dirigido por configuração, com cenários aprovados/recusados/timeout. Registration publica solicitações via outbox; o fake publica resultados no RabbitMQ; Registration confirma apenas após aprovação e libera a vaga em recusa/expiração. Não criar serviço Billing de domínio.

#### 4.4 Especificar Notification e implementar mock desacoplado
- **Requisitos**: RF08, RN05, RQ01, RQ03, RQ05
- **Insumos de desenho**: contrato de eventos e ADR de resiliência.
- **Descrição**: documentar o contrato de Notification e prover fake consumidor assíncrono que registra entregas simuladas e permite simular indisponibilidade. Falha ou atraso do fake não deve desfazer/bloquear uma inscrição; reprocessamento e resultados devem ser observáveis. Não criar serviço Notification de domínio.

### Fase 5 — US3/US4: Presença, certificados e relatórios executáveis

#### 5.1 Implementar Attendance & Certification
- **Requisitos**: RF09–RF10, RN06, RQ01, RQ05
- **Insumos de desenho**: regras de inscrição/presença e critério validado de certificado por check-in.
- **Descrição**: implementar serviço Python/FastAPI com persistência própria, contrato OpenAPI e consumidor dos eventos de inscrição. Registrar check-in somente para inscrição elegível e de forma idempotente; emitir certificado somente após encerramento da atividade e check-in registrado. Não marcar presença nem emitir certificado para inscrição inexistente/não elegível.

#### 5.2 Implementar Reporting
- **Requisitos**: RF11, RQ01, RQ03, RQ05–RQ06
- **Insumos de desenho**: fontes de eventos e propriedade de dados por serviço.
- **Descrição**: implementar serviço Python/FastAPI com persistência/projeção própria alimentada pelos eventos publicados; expor indicadores de inscrições, pagamentos, ocupação e comparecimento aos organizadores autorizados. Documentar consistência eventual e garantir que Reporting não consulta bancos de outros serviços; sua indisponibilidade não bloqueia inscrições.

### Fase 6 — Integração, validação e entrega

#### 6.1 Demonstrar o fluxo integrado e os critérios de qualidade
- **Requisitos**: RF05–RF11, RN01–RN07, RQ02–RQ08
- **Insumos de desenho**: contratos, APIs e serviços das fases 3–4.
- **Descrição**: executar o fluxo integrado de consulta → reserva → pagamento fake → confirmação → check-in → certificado e projeção no Reporting; demonstrar recusa e expiração com liberação de vaga, falha do mock Notification sem rollback e promoção de lista de espera. Executar teste concorrente que prove que reservas nunca excedem a capacidade e ensaio de carga comparativo até 20× o perfil normal definido.

#### 6.2 Finalizar segurança, observabilidade e documentação
- **Requisitos**: RF01, RF08, RQ05–RQ08
- **Insumos de desenho**: quatro serviços implementados, mocks, rationales e resultados de validação.
- **Descrição**: propagar correlation/trace ID por HTTP e eventos; registrar logs estruturados úteis para seguir uma inscrição; validar ausência de segredos e FailFast de configuração; completar instruções reprodutíveis para iniciar os quatro serviços e mocks, dados de demonstração, documentação de APIs, diagramas, ADRs e relatório de trade-offs. Registrar evidências reproduzíveis e a declaração de uso de IA.

## Estrutura de projeto proposta

```text
docs/
  development/
    environment-setup.md
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
    environment-readiness.md
    evidence.md
    trade-offs.md
services/
  event-management-java/
  registration-java/
  attendance-certification-python/
  reporting-python/
  mocks/
    identity-access/
    billing/
    notification/
infra/
  docker-compose.yml
  .env.example
scripts/
  check-environment.sh
tests/
  contracts/
  integration/
  concurrency/
  configuration/
```

## Estratégia de testes

- **Tipo de aplicação**: APIs REST e mensageria; sem interface web no escopo do protótipo.
- **Jornadas críticas**:
  1. Organizador/participante autenticado pelo Identity mock cria/publica evento ou consulta programação; autorização multi-organização é aplicada pelo serviço de domínio.
  2. Participante solicita inscrição gratuita/paga, acompanha estado e não acessa inscrições de outros participantes.
  3. Billing fake aprova/recusa/expira pagamento via RabbitMQ; Registration confirma apenas após aprovação e libera a vaga nos demais casos.
  4. Cancelamento/expiração promove automaticamente a fila; eventos de inscrição alimentam check-in e certificado após encerramento da atividade.
  5. Reporting atualiza sua projeção por eventos e produz indicadores; indisponibilidade de Reporting/Notification fake não bloqueia a inscrição.
- **Stack de validação primária**: JUnit/jqwik e pytest/Hypothesis; PostgreSQL/RabbitMQ com Docker Compose/Testcontainers; validação OpenAPI/AsyncAPI; ArchUnit/import-linter; PIT e ferramenta Python de mutation testing. Alvo de mutation score ≥95% no núcleo de domínio, alinhado a `processo.md`, com exclusões justificadas e explícitas.
- **Infraestrutura e prontidão**: Docker funcional, validado pelo gate da Fase 0; integração com PostgreSQL e RabbitMQ reais em containers. Builds e testes de aceitação têm comandos containerizados e não dependem de JDK/Maven/Python no host. Testes unitários com doubles determinísticos são uma camada de teste isolada, não fallback para aprovar integração quando Docker ou dependências falham.
- **Navegador**: não aplicável, pois não há UI no escopo. Validar APIs por clientes automatizados e OpenAPI.
- **Dados de teste**: fixtures sintéticas e isoladas por execução; identificadores de inscrição/evento determinísticos para cenários idempotentes; limpeza/reset de banco entre execuções; sem dados pessoais reais.
- **Configuração de regras de negócio**: `.env.example` deve declarar `RESERVATION_EXPIRY_MINUTES=30`, `WAITLIST_ENABLED=true`, `WAITLIST_OFFER_EXPIRY_MINUTES=30` (valor demonstrativo a validar no saneamento do domínio), `CERTIFICATE_ELIGIBILITY_POLICY=CHECK_IN`, `CERTIFICATE_ISSUANCE_AFTER_EVENT=true`, `BILLING_MOCK_SCENARIO=APPROVED` e `NOTIFICATION_MOCK_AVAILABLE=true`. Valores efetivos serão lidos do `.env` local ou ambiente e validados; o código não define fallback silencioso. Conteúdo específico de evento (datas, capacidade, preço) é dado persistido, não configuração global. Variáveis operacionais (URLs, portas, banco e broker) também são externas; segredos reais não constam do exemplo.
- **Critérios de aceite**: nenhum excesso de capacidade sob concorrência; confirmação de inscrição paga somente depois de aprovação; recusa/expiração libera vaga; evento duplicado não duplica efeitos; indisponibilidade dos mocks de Notification e do serviço Reporting não reverte/bloqueia inscrição; os quatro serviços e três mocks sobem localmente com instruções documentadas; contratos Java/Python são validados; `.env` fica ignorado e nenhum segredo real é versionado.
- **Lacunas conhecidas**: Identity & Access, Billing e Notification são dependências simuladas, não serviços de domínio implementados; mutation score e ensaio de carga focam os quatro serviços reais; teste de carga verifica tendência/escala seletiva contra baseline definida, pois a especificação não fixa latência-alvo absoluta; integrações externas e canais de notificação são simulados.
- **Revisão final**: confirmar rastreabilidade de RF/RN/RQ, diagramas e contratos coerentes com código, testes concorrentes e de falha reproduzíveis, rationales atualizados e limitações declaradas no relatório e na demonstração.

## Tarefas

### Fase 0 — Preparação e verificação do ambiente

As tarefas são numeradas sequencialmente de T001 a T044, seguindo a ordem das fases 0 a 6. Os marcadores de paralelismo não dispensam o cumprimento das dependências.

- [x] T001 [Plan:0.1] Documentar inventário, requisitos de versão, instalação/configuração de Git, Docker e Compose v2 por sistema operacional em `docs/development/environment-setup.md`; verificar instalações existentes antes de propor mudanças e exigir autorização para alterações de sistema.
- [ ] T002 [Plan:0.2] Documentar em `docs/development/environment-setup.md` a rota oficial containerizada e a alternativa opcional de JDK 21, Maven Wrapper, Python/venv e IDE; esclarecer que bancos, broker, builds e testes de aceitação não requerem runtimes locais.
- [ ] T003 [Plan:0.3] Criar `scripts/check-environment.sh` para verificar versões, daemon/contexto Docker, execução de container, rede, volume, recursos e portas conforme limites documentados; usar recursos temporários próprios, limpá-los e retornar erro explícito em falha, sem fallback silencioso.
- [ ] T004 [Plan:0.3] Executar o gate de prontidão e registrar versões, recursos e resultados sem segredos em `docs/validation/environment-readiness.md`; documentar localização/proteção do `.env` e vínculo com a configuração entregue na Fase 2 em `docs/development/environment-setup.md` e `README.md`.

### Fase 1 — Domínio e arquitetura

- [ ] T005 [Plan:1.1] Consolidar glossário, invariantes e cenários Gherkin para inscrição gratuita/paga, recusa, timeout, cancelamento, lista de espera, concorrência, notificações e autorização em `docs/architecture/domain-scenarios.md`; registrar validação do responsável de negócio.
- [ ] T006 [Plan:1.2] Desenhar grafo de dependências, limites dos sete serviços candidatos, recorte dos quatro implementados e diagrama de containers em `docs/architecture/context-map.md`, `docs/architecture/service-boundaries.md` e `docs/architecture/containers.puml`.
- [ ] T007 [Plan:1.2] Documentar fluxo de inscrição paga, incluindo recusa, timeout e indisponibilidade de dependências, em `docs/architecture/paid-registration-flow.puml` e `docs/architecture/paid-registration-flow.md`.
- [ ] T008 [P] [Plan:1.3] Registrar ADRs 001–005 com alternativas, decisão, evidências e consequências em `docs/adr/ADR-001-service-boundaries.md` a `docs/adr/ADR-005-prototype-scope.md`.
- [ ] T009 [Plan:1.3] Criar os rationales JSON versionados correspondentes aos ADRs em `docs/rationales/ADR-001.json` a `docs/rationales/ADR-005.json`, incluindo `artifact`, `rationale`, `invariants` e `version`.

### Fase 2 — Contratos e fundação técnica

- [ ] T010 [P] [Plan:2.1] Definir contratos OpenAPI dos quatro serviços executáveis e da API simulada Identity & Access em `docs/contracts/openapi/event-management.yaml`, `registration.yaml`, `attendance-certification.yaml`, `reporting.yaml` e `identity-mock.yaml`.
- [ ] T011 [P] [Plan:2.1] Definir contratos e schemas de Billing/Notification mockados e eventos de inscrição, pagamento, presença, certificado e projeções em `docs/contracts/asyncapi/eventhub.yaml` e `docs/contracts/schemas/`.
- [ ] T012 [Plan:2.1] Documentar estados, chaves idempotentes, propriedade de dados, migrações e acesso permitido por serviço em `docs/architecture/data-ownership.md`.
- [ ] T013 [Plan:2.2] Criar `infra/docker-compose.yml` para PostgreSQL, RabbitMQ, quatro serviços executáveis e mocks de Identity/Billing/Notification; adicionar healthchecks, volumes, configuração externa e comandos containerizados de build/testes de aceitação, sem exigir JDK/Maven/Python no host.
- [ ] T014 [P] [Plan:2.2] Inicializar scaffolds Java 21/Spring Boot para Event Management e Registration em `services/event-management-java/` e `services/registration-java/`, com testes e migrações Flyway.
- [ ] T015 [P] [Plan:2.2] Inicializar scaffolds Python/FastAPI para Attendance & Certification e Reporting em `services/attendance-certification-python/` e `services/reporting-python/`, com testes e migrações Alembic.
- Criar os `Dockerfile` de cada serviço e mock como parte de T014/T015 e das tarefas dos respectivos mocks, com estágios de build/teste/execução, versões fixadas e sem ferramentas/dependências exclusivas de build/teste na imagem final.
- [ ] T016 [Plan:2.2] Ignorar `.env` real no `.gitignore`, criar `infra/.env.example` com valores de demonstração para todas as variáveis de negócio, infraestrutura e mocks, validar variáveis requeridas/valores no startup, e documentar a cópia do modelo para `.env`, dados sintéticos e comandos em `README.md`; não incluir segredos reais.
- [ ] T017 [Plan:2.3] Configurar CI em `.github/workflows/ci.yml` para build, testes, linters, contratos, rationales e auditoria semântica do PR contra os rationales; usar credencial protegida, bloquear aprovação em caso de falha/indisponibilidade e permitir merge automático somente após todos os gates aprovados, escalonando alterações de invariantes para validação humana.
- [ ] T018 [Plan:2.3] Adicionar testes de fronteira arquitetural com ArchUnit nos serviços Java e import-linter nos serviços/mocks Python; falhar o CI em dependências entre domínios/serviços não autorizadas.

### Fase 3 — US1: Identidade simulada e catálogo

- [ ] T019 [US1] [Plan:3.1] Especificar contrato e regras de Identity & Access e escrever testes para token, papéis, organização e acesso por participante em `mocks/identity-access/tests/` e `tests/contracts/identity-mock/`.
- [ ] T020 [US1] [Plan:3.1] Implementar fake determinístico de identidade/autorização com perfis e organizações sintéticos em `mocks/identity-access/`; rejeitar token inválido e não substituir falha de autenticação por acesso anônimo.
- [ ] T021 [US1] [Plan:3.2] Escrever testes de contrato e domínio para ciclo de vida de eventos/atividades e consulta pública em `services/event-management-java/src/test/`.
- [ ] T022 [US1] [Plan:3.2] Implementar persistência, migrações e endpoints de evento/atividade conforme os testes em `services/event-management-java/src/`.

### Fase 4 — US2: Inscrições, capacidade e dependências simuladas

- [ ] T023 [US2] [Plan:4.1] Escrever testes JUnit/jqwik para reserva, cancelamento, inscrição gratuita, idempotência e invariantes de capacidade em `services/registration-java/src/test/`.
- [ ] T024 [US2] [Plan:4.1] Implementar reserva atômica/idempotente, consulta de estado, cancelamento e atividade gratuita conforme os testes em `services/registration-java/src/`, aplicando o mecanismo decidido no ADR-003.
- [ ] T025 [US2] [Plan:4.2] Escrever testes property-based e concorrentes para ordenação da fila, cancelamento, expiração e oferta de vaga em `services/registration-java/src/test/`.
- [ ] T026 [US2] [Plan:4.2] Implementar lista de espera ordenada, promoção automática e prazo configurável para aceitar oferta em `services/registration-java/src/`, conforme os testes.
- [ ] T027 [US2] [Plan:4.3] Escrever testes para outbox, Billing fake, resultados aprovados/recusados/timeout, duplicidade, expiração e transições em `services/registration-java/src/test/` e `mocks/billing/tests/`.
- [ ] T028 [Plan:4.3] Implementar outbox transacional e publicação idempotente de solicitação de pagamento em `services/registration-java/src/`, conforme os testes.
- [ ] T029 [US2] [Plan:4.3] Implementar Billing fake em `mocks/billing/`, consumindo pedidos e publicando resultados de aprovação/recusa/timeout conforme AsyncAPI e configuração de ambiente.
- [ ] T030 [US2] [Plan:4.3] Implementar consumo do resultado, confirmação somente após aprovação e expiração com prazo padrão de 30 minutos configurável em `services/registration-java/src/`; liberar reservas recusadas/expiradas uma única vez.
- [ ] T031 [US2] [Plan:4.4] Escrever testes de idempotência, indisponibilidade e reprocessamento para Notification fake em `mocks/notification/tests/` e `tests/integration/`.
- [ ] T032 [US2] [Plan:4.4] Implementar fake assíncrono de Notification em `mocks/notification/`, registrando entregas simuladas e permitindo simular indisponibilidade sem bloquear o fluxo de inscrição.

### Fase 5 — US3/US4: Serviços executáveis

- [ ] T033 [US3] [Plan:5.1] Escrever testes de contrato, elegibilidade, check-in idempotente e emissão após encerramento da atividade em `services/attendance-certification-python/tests/`.
- [ ] T034 [US3] [Plan:5.1] Implementar Attendance & Certification com persistência própria, consumidor de eventos de inscrição, endpoint de check-in idempotente e emissão de certificado elegível em `services/attendance-certification-python/`.
- [ ] T035 [US4] [Plan:5.2] Escrever testes de contrato, projeção eventual, autorização de organizador e isolamento de falha para Reporting em `services/reporting-python/tests/`.
- [ ] T036 [US4] [Plan:5.2] Implementar Reporting com projeção de inscrições, pagamentos, ocupação e comparecimento alimentada por eventos e API de consulta autorizada em `services/reporting-python/`; não acessar bancos alheios.

### Fase 6 — Integração e entrega

- [ ] T037 [Plan:6.1] Criar teste ponta a ponta em `tests/integration/paid-registration-flow/` que inicia os quatro serviços e mocks e valida consulta → reserva → pagamento fake aprovado → confirmação → check-in → certificado → projeção de relatório.
- [ ] T038 [Plan:6.1] Criar cenários de integração para recusa, expiração, promoção de lista de espera, Notification fake indisponível e Reporting indisponível em `tests/integration/failure-scenarios/`.
- [ ] T039 [Plan:6.1] Executar teste de concorrência em `tests/concurrency/` com capacidade controlada e concorrência reproduzível; registrar que reservas confirmadas/ofertadas nunca superam a capacidade.
- [ ] T040 [Plan:6.1] Configurar mutation testing para os quatro serviços executáveis e CI; exigir score ≥95% no núcleo de domínio ou documentar cada exclusão aprovada.
- [ ] T041 [Plan:6.1] Criar ensaio de carga repetível em `tests/load/` e registrar baseline, perfil de até 20× e resultados comparativos de consultas/inscrições em `docs/validation/evidence.md`.
- [ ] T042 [Plan:6.2] Propagar correlation/trace ID em APIs, mensagens e logs estruturados nos quatro serviços e mocks, incluindo `registrationId` para diagnóstico ponta a ponta.
- [ ] T043 [Plan:6.2] Completar `README.md` com requisitos, configuração segura, `.env.example`, comandos de build/teste/execução, dados de demonstração, APIs e limites de serviços reais versus mocks.
- [ ] T044 [Plan:6.2] Consolidar diagramas, ADRs, rationales, evidências de teste e análise dos trade-offs em `docs/validation/`; incluir declaração de uso de IA e roteiro de demonstração/defesa.

## Mapeamento de requisitos

| ID | Descrição | Itens do plano | Evidência prevista |
|---|---|---|---|
| RF01 | Cadastro, autenticação e perfis com permissões distintas | 1.1, 2.1, 3.1 | contrato OpenAPI, Identity & Access mock e testes de token/perfil/autorização |
| RF02 | Criar, editar, publicar e cancelar eventos | 2.1, 3.2 | `services/event-management-java/` e contrato OpenAPI |
| RF03 | Cadastrar atividades e regras de inscrição | 2.1, 3.2 | `services/event-management-java/` e modelo de atividades |
| RF04 | Consultar eventos, programação, vagas e detalhes | 2.1, 3.2, 4.1 | APIs Event Management/Registration e teste integrado |
| RF05 | Solicitar, consultar e cancelar inscrições | 2.1, 4.1 | `services/registration-java/` e testes de estado |
| RF06 | Respeitar capacidade e oferecer lista de espera | 1.1, 4.1, 4.2, 6.1 | reserva atômica, promoção automática e teste concorrente |
| RF07 | Simular pagamentos e registrar aprovação, recusa ou expiração | 2.1, 4.3, 6.1 | contrato de Billing, fake RabbitMQ configurável e testes de resultados |
| RF08 | Notificar eventos relevantes sem bloquear operações essenciais | 2.1, 4.4, 6.1 | contrato, Notification fake e teste de indisponibilidade |
| RF09 | Registrar check-in sem duplicidade | 2.1, 5.1, 6.1 | `services/attendance-certification-python/` e testes idempotentes |
| RF10 | Emitir certificado a elegíveis após evento/atividade | 2.1, 5.1, 6.1 | `services/attendance-certification-python/` e testes de elegibilidade |
| RF11 | Disponibilizar relatórios operacionais | 2.1, 5.2, 6.1 | `services/reporting-python/` com projeções e API de consulta |
| RN01 | Nunca exceder capacidade sob concorrência | 1.1, 4.1, 4.2, 6.1 | invariantes, reserva atômica e teste concorrente |
| RN02 | Confirmar inscrição paga somente após pagamento; expirar reserva pendente | 1.1, 4.3 | transições, prazo configurável e teste de expiração |
| RN03 | Recusa/expiração libera reserva sem confirmação indevida | 1.1, 4.2, 4.3, 6.1 | testes de recusa, timeout e liberação única |
| RN04 | Cancelamento atualiza disponibilidade e pode promover fila | 1.1, 4.2 | fluxo e testes de cancelamento/promoção |
| RN05 | Falha de notificações não desfaz confirmação | 1.1, 4.4, 6.1 | consumo assíncrono e teste com Notification indisponível |
| RN06 | Certificar somente elegíveis após atividade | 1.1, 5.1, 6.1 | serviço de Attendance & Certification e testes de check-in/evento encerrado |
| RN07 | Restringir acesso por participante e organização | 1.1, 3.1, 6.1 | Identity mock e verificações de autorização nos serviços de domínio |
| RQ01 | Evolução e implantação independente | 1.2, 1.3, 2.2, 5.1, 5.2 | limites de serviço, scaffolds e contexto map |
| RQ02 | Escalabilidade seletiva de consultas/inscrições | 1.2, 3.2, 4.1, 6.1 | desenho de escalabilidade e ensaio comparativo até 20× |
| RQ03 | Resiliência a falhas de notificações/relatórios | 1.2, 4.3, 4.4, 5.2, 6.1 | Notification fake e Reporting executável/projeção; evidência de falha isolada |
| RQ04 | Consistência e recuperação de reservas/pagamentos | 1.3, 2.1, 4.1–4.3, 6.1 | ADR, outbox, idempotência e testes de estado |
| RQ05 | Interoperabilidade por contratos explícitos | 0.2, 1.2, 2.1, 3.2, 4.3–4.4, 5.1–5.2 | rota de execução Java/Python documentada, OpenAPI, AsyncAPI/JSON Schema e testes de contrato |
| RQ06 | Rastreabilidade e diagnóstico ponta a ponta | 1.3, 5.2, 6.2 | projeções de Reporting, correlation/trace ID e logs estruturados |
| RQ07 | Autenticação, autorização e proteção de segredos | 0.1, 0.3, 1.3, 2.2, 3.1, 6.2 | pré-verificação segura do host, identidade, controle multi-organização e verificação de configuração |
| RQ08 | Execução local reproduzível | 0.1–0.3, 2.2, 6.2 | guia de ambiente, gate de prontidão, imagens, Docker Compose, `.env.example` e README |

## Complexidade e desvios deliberados

| Desvio/decisão | Motivo | Alternativa mais simples rejeitada |
|---|---|---|
| Quatro serviços executáveis; Identity & Access, Billing e Notification mockados | Cumprir o recorte explicitamente solicitado, incluindo presença/certificados e relatórios reais | Implementar as sete capacidades completas aumenta o custo sem ser necessário para demonstrar integração; os mocks mantêm contratos e falhas exercitáveis |
| Rationales JSON versionados no Git, sem banco vetorial | O pequeno número de decisões do trabalho é pesquisável por arquivos e histórico de versões | Banco vetorial adicionaria infraestrutura sem necessidade de recuperação semântica em escala |
| Sem gateway, Kubernetes ou pagamento real no protótipo | Não são necessários para provar requisitos centrais e aumentariam operação/segredos | Adotá-los sem demanda seria superengenharia |
