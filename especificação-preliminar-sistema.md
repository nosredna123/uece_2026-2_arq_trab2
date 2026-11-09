## EventHub: Designing and Implementing a Microservices-Based System

Modalidade: trabalho em equipe (até 5 integrantes) | Entrega e prazo: definidos em sala | Natureza: projeto arquitetural + protótipo executável

## 1. Objetivo

Projetar e implementar uma solução baseada em microsserviços a partir de uma especificação de domínio. A equipe deverá identificar as fronteiras adequadas dos serviços, justificar a granularidade e os mecanismos de comunicação, definir a propriedade dos dados, selecionar padrões arquiteturais e validar parte relevante da arquitetura por meio de implementação e testes.

Pergunta orientadora: Which business capabilities should become independently deployable services, and why?

## 2. Cenário do sistema

O EventHub é uma plataforma de gerenciamento de eventos acadêmicos e profissionais, como congressos, seminários, workshops e minicursos. Organizações criam eventos, publicam atividades e disponibilizam inscrições. Participantes consultam a programação, inscrevem-se em atividades, realizam pagamentos quando necessário, recebem comunicações, fazem check-in e podem obter certificados. Organizadores acompanham ocupação, presença e indicadores operacionais.

O sistema será utilizado por múltiplas organizações. Um evento pode conter várias atividades, com datas,

horários, preços e capacidades diferentes. Algumas atividades são gratuitas; outras exigem pagamento. O número de acessos pode variar intensamente durante a abertura de inscrições.

## 3. Especificação funcional

RF01 — Usuários e perfis: Permitir cadastro, autenticação e manutenção de perfis de participantes e organizadores; diferenciar permissões.

RF02 — Eventos: Permitir criar, editar, publicar e cancelar eventos, definindo organização responsável, período e informações públicas.

RF03 — Programação: Permitir cadastrar atividades vinculadas a eventos, com horários, responsáveis, locais e regras de inscrição.

RF04 — Consulta e busca: Permitir consultar eventos publicados, programação, vagas e detalhes das atividades.

RF05 — Inscrições: Permitir solicitar inscrição, consultar situação e cancelar inscrições conforme regras do evento.

RF06 — Capacidade e espera: Respeitar limite de vagas; impedir superlotação mesmo sob concorrência; permitir lista de espera quando habilitada.

RF07 — Pagamentos: Para atividades pagas, iniciar pagamento por um provedor externo simulado e registrar confirmação, recusa ou expiração.


UECE | Arquitetura de Software | Trabalho 2

RF08 — Notificações: Comunicar inscrição, confirmação, cancelamento, pagamento e alterações relevantes; indisponibilidade de notificações não deve bloquear operações essenciais. RF09 — Check-in: Registrar presença de participantes inscritos e evitar registros duplicados. RF10 — Certificados: Emitir certificados para participantes elegíveis segundo regras de presença e conclusão. RF11 — Relatórios: Disponibilizar indicadores de inscrições, pagamentos, ocupação e comparecimento para organizadores. Importante: os itens anteriores descrevem capacidades e requisitos funcionais. Não representam uma lista de

microsserviços. Cabe à equipe decidir quais capacidades devem ser agrupadas ou separadas.

- 4. Regras de negócio e cenários críticos

RN01 — Reserva de vaga: Uma inscrição não poderá resultar em ocupação superior à capacidade da atividade, inclusive quando várias solicitações ocorrerem simultaneamente. RN02 — Pagamento e confirmação: Em atividades pagas, a inscrição somente se torna confirmada após o pagamento. Reservas pendentes devem expirar após um prazo configurável e liberar a vaga. RN03 — Pagamento recusado: Quando o pagamento for recusado ou expirar, a reserva correspondente deve ser liberada sem gerar confirmação indevida. RN04 — Cancelamento: Cancelamentos devem atualizar a disponibilidade; se houver lista de espera, o sistema poderá oferecer a vaga ao próximo participante. RN05 — Notificações desacopladas: Falhas no mecanismo de notificações não podem desfazer uma inscrição já confirmada. RN06 — Certificados: Certificados são emitidos apenas após o evento/atividade e para participantes que atendam ao critério de presença. RN07 — Segurança e acesso: Participantes acessam seus próprios dados; organizadores administram apenas

eventos sob sua responsabilidade.

- 5. Requisitos de qualidade e forças arquiteturais

RQ01 — Evolução independente: Mudanças em notificações e certificados devem, idealmente, ser implantáveis sem exigir a implantação de todo o sistema. RQ02 — Escalabilidade seletiva: Durante abertura de inscrições, consultas e inscrições podem receber 20× a carga normal, enquanto certificados permanecem quase ociosos. RQ03 — Disponibilidade e resiliência: Indisponibilidade temporária de notificações ou relatórios não deve impedir a operação principal de inscrições. RQ04 — Consistência: Não pode haver overbooking; transições de reserva, pagamento e confirmação precisam ser controladas e recuperáveis. RQ05 — Interoperabilidade: Serviços desenvolvidos em tecnologias distintas devem se comunicar por contratos explícitos. RQ06 — Observabilidade: Deve ser possível rastrear uma inscrição entre os componentes envolvidos e diagnosticar falhas. RQ07 — Segurança: Acesso autenticado e autorizado às operações protegidas; segredos não devem ficar no repositório.


RQ08 — Operabilidade: A solução deve ser reproduzível em ambiente local, com instruções claras para iniciar e testar seus componentes.

## 6. Parte I — Architectural Design

A equipe deve analisar o domínio e propor uma arquitetura com aproximadamente 6 a 10 microsserviços candidatos. Esse intervalo é uma referência de complexidade, não uma quota obrigatória: decomposições diferentes serão aceitas quando bem justificadas. Não se deve criar um microsserviço para cada RF automaticamente.

- Identificar capacidades de negócio, responsabilidades, limites de contexto e critérios de granularidade.

- Apresentar diagrama de contexto e diagrama de containers/serviços, indicando interfaces e fluxos principais.

- Definir propriedade dos dados por serviço, políticas de acesso e eventual compartilhamento via APIs, eventos ou projeções; evitar acesso direto ao banco de outro serviço sem justificativa excepcional.

- Explicar escolhas de comunicação síncrona e assíncrona, contratos, tratamento de erros e idempotência quando aplicável.

- Avaliar padrões como API Gateway, Circuit Breaker, Saga, Publish/Subscribe, Database per Service, Transactional Outbox e outros; adotar apenas os necessários.

- Descrever um fluxo completo de inscrição paga e o comportamento nos casos de recusa, timeout e indisponibilidade de dependências.

- Registrar 3 a 5 ADRs com contexto, alternativas, decisão, evidências, justificativa e consequências.

## 7. Parte II — Implementation

Implementar um subconjunto representativo da arquitetura: preferencialmente 4 a 6 microsserviços, incluindo um fluxo de negócio ponta a ponta. Os demais serviços propostos podem permanecer modelados, desde que a escolha seja explicitada. O protótipo deve demonstrar decisões arquiteturais reais, não apenas endpoints isolados.

- Usar pelo menos duas tecnologias de implementação entre os microsserviços (por exemplo, Java/Spring Boot e Python/FastAPI).

- Cada serviço implementado deve ter responsabilidade clara, API/contrato documentado e persistência quando pertinente.

- Implementar um fluxo integrado envolvendo no mínimo três serviços (por exemplo, consulta de atividade → reserva de vaga pagamento simulado confirmação). → →

- Incluir autenticação/autorização mínima para operações protegidas ou explicar, com justificativa, o recorte adotado no protótipo.

- Implementar ao menos um mecanismo relevante de resiliência, consistência ou integração assíncrona, escolhido e justificado a partir dos requisitos.

- Disponibilizar instruções reprodutíveis de execução (Docker Compose é recomendado, mas não obrigatório), configuração e teste; o provedor de pagamento pode ser simulado.

- Fornecer dados de exemplo e não incluir senhas, tokens ou chaves reais no repositório.

Não é obrigatório implementar todos os padrões estudados. Também não é necessário utilizar Kubernetes, infraestrutura de nuvem paga ou bancos de dados de tecnologias diferentes.


## 8. Parte III — Validation and Critical Reflection

- Demonstrar o fluxo funcional principal e os estados relevantes da inscrição.

- Executar um teste de concorrência para verificar que a capacidade não é excedida.

- Simular ao menos uma falha de dependência (por exemplo, notificações indisponíveis ou pagamento recusado) e registrar o comportamento observado.

- Demonstrar a integração entre serviços desenvolvidos em tecnologias distintas.

- Discutir quais decisões trouxeram benefícios e quais introduziram complexidade operacional, consistência eventual, latência ou acoplamento.

## 9. Entregáveis

- Relatório arquitetural (sugestão: 6910 páginas, excluindo referências e anexos), contendo análise das forças, decomposição, diagramas, dados, contratos, padrões, ADRs e trade-offs.

- Repositório com código-fonte, instruções de execução, configuração, dados de teste e documentação das APIs.

- Evidências de validação: testes, logs, capturas de tela ou resultados reproduzíveis.

- Demonstração e defesa oral em sala, com participação dos integrantes.

- Breve declaração sobre o uso de ferramentas de IA generativa, se utilizadas, e verificação das referências citadas.

## 10. Critérios de avaliação (proposta)

| Dimensão | Peso |
| --- | --- |
| Análise das forces, QAs e | 25% |
| decomposição/granularidade |   |
| Qualidade das decisões (dados, comunicação, | 25% |
| padrões, ADRs) |   |
| Implementação e integração ponta a ponta | 25% |
| Validação de consistência, falhas e testes | 15% |
| Documentação, clareza e defesa | 10% |

A avaliação prioriza adequação e justificativa arquitetural. Ter mais serviços, mais tecnologias ou mais padrões não implica melhor nota.

## 11. Orientações finais

As equipes podem usar literatura científica, documentação oficial e fontes técnicas confiáveis para fundamentar decisões. O uso de IA generativa é permitido como apoio, desde que o grupo compreenda, verifique e consiga defender o que foi produzido. Soluções distintas são esperadas; decisões devem ser contextualizadas e justificadas.

Pergunta de fechamento: What did we gain — and what did we pay for distributing the system?
