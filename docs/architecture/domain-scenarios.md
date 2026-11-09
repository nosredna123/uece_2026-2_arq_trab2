# Glossário, invariantes e cenários do domínio

**Estado de validação de negócio:** APROVADO  
**Validador responsável:** Amg - UECE  
**Validação:** 2026-11-09  
**Versão:** 1.0.0  
**Base:** especificação preliminar do EventHub e decisões de escopo registradas em `plan.md`

Este documento consolida vocabulário e comportamentos verificáveis para orientar
o desenho posterior. As regras e cenários desta versão foram validados pelo
responsável de negócio identificado acima. Alterações posteriores a estas
invariantes exigem nova validação humana antes de atualizar contratos ou código
de domínio dependente.

## Escopo do protótipo

O protótipo implementará Event Management, Registration, Attendance &
Certification e Reporting. Identity & Access, Billing e Notification são
dependências simuladas por contratos e mocks/fakes; não são serviços de domínio
implementados. Os mocks devem deixar seus resultados e indisponibilidades
explícitos.

## Glossário

| Termo | Definição |
|---|---|
| Organização | Entidade responsável por um ou mais eventos. Organizadores administram apenas recursos de sua organização. |
| Participante | Pessoa que consulta atividades e solicita, acompanha ou cancela suas próprias inscrições. |
| Evento | Agrupamento publicado ou administrado por uma organização, com informações e período próprios. |
| Atividade | Unidade de programação de um evento, com horário, capacidade e regra de inscrição; pode ser gratuita ou paga. |
| Capacidade | Limite máximo de inscrições que ocupam vagas numa atividade. Reservas pendentes de pagamento também ocupam uma vaga. |
| Inscrição | Registro da solicitação de participação numa atividade, associado ao participante e ao estado atual do processo de vaga/pagamento. |
| Reserva pendente | Ocupação temporária de uma vaga enquanto um pagamento é aguardado. O prazo inicial aprovado é 30 minutos, configurável externamente. |
| Inscrição confirmada | Inscrição que pode participar da atividade; para uma atividade paga, somente após aprovação do Billing mock. |
| Lista de espera | Fila ordenada de participantes sem vaga quando habilitada para a atividade. Uma oferta não é confirmação. |
| Oferta de vaga | Oportunidade temporária enviada ao próximo participante elegível da fila após liberação de vaga. O prazo configurável inicial aprovado é 30 minutos. |
| Recusa de pagamento | Resultado explícito do Billing mock que encerra a tentativa pendente sem confirmar a inscrição e libera a vaga uma única vez. |
| Timeout de pagamento | Ausência de aprovação antes da expiração da reserva; encerra a tentativa e libera a vaga uma única vez. |
| Identidade autenticada | Principal reconhecido pelo Identity & Access mock a partir de uma credencial válida. A autenticação simulada não dispensa autorização no serviço de domínio. |
| Administrador global | Papel do Identity mock autorizado a administrar eventos e atividades e consultar relatórios de qualquer organização; não administra usuários nem organizações no escopo aprovado. |
| Solicitação de cancelamento pago | Pedido de cancelamento de inscrição paga enviado dentro do prazo configurado e antes do início da atividade. A inscrição permanece confirmada e ocupa a vaga enquanto o reembolso não for aprovado. |
| Reembolso de cancelamento | Reembolso simulado de 90% do valor pago, configurável. Se falhar, o pedido permanece pendente e é tentado novamente automaticamente sem limite; só após aprovação a inscrição é cancelada e a vaga liberada. |
| Check-in | Registro idempotente de presença para uma inscrição elegível numa atividade. |
| Elegibilidade de certificado | No protótipo, exige inscrição confirmada, um check-in registrado e atividade encerrada. Percentual mínimo de presença não é medido neste escopo. |
| Notificação | Efeito assíncrono informativo, produzido sem tornar a confirmação da inscrição dependente da disponibilidade do mock. |
| Projeção de Reporting | Visão própria e eventualmente consistente de inscrições, pagamentos, ocupação e comparecimento, alimentada por eventos; não lê bancos alheios. |

## Invariantes aprovados

1. **Capacidade (RN01/RQ04):** em nenhum instante o total de inscrições confirmadas,
   reservas pendentes de pagamento e ofertas que ocupam vaga excede a capacidade
   da atividade.
2. **Pagamento (RN02/RN03):** atividade paga só fica confirmada após aprovação
   correspondente do Billing mock. Recusa, timeout ou aprovação recebida depois
   de a reserva expirar não pode confirmar a inscrição nem liberar uma vaga mais
   de uma vez.
3. **Reserva (RN02):** o prazo de expiração da reserva paga é externo e
   configurável; o valor planejado de demonstração é 30 minutos. A expiração
   libera a vaga para nova inscrição ou oferta conforme a política aprovada.
4. **Cancelamento pago (RN04):** o pedido deve ser feito dentro dos sete dias
   configurados após a inscrição e antes do início da atividade (vale o limite
   que ocorrer primeiro). O reembolso é de 90% do valor pago, configurável. A
   inscrição continua confirmada e ocupa capacidade até o Billing mock aprovar
   o reembolso; falhas são tentadas novamente automaticamente sem limite. Após
   aprovação, a inscrição é cancelada e a vaga é liberada uma única vez.
5. **Cancelamento e espera (RN04):** cancelamento efetivado libera a vaga; se a
   lista estiver habilitada e houver participantes elegíveis, oferece-se a vaga
   ao primeiro da fila em ordem FIFO, desempate por sequência de aceitação
   registrada pelo sistema.
6. **Oferta não é confirmação:** aceitar oferta para atividade gratuita
   confirma a inscrição se a vaga ainda estiver disponível; aceitar oferta para
   atividade paga cria nova reserva e novo prazo de pagamento. Oferta expirada
   ou recusada não consome capacidade e permite avançar a fila.
7. **Idempotência (RN01/RN02/RN03/RQ04):** entrega repetida de uma solicitação,
   resultado de pagamento ou ação de cancelamento não cria inscrições
   duplicadas, não confirma após estado terminal e não libera/promove uma vaga
   duas vezes.
8. **Notificação desacoplada (RN05/RF08):** falha ou atraso do mock de
   Notification não reverte uma inscrição confirmada nem bloqueia sua operação
   essencial; a falha deve continuar diagnosticável.
9. **Reporting desacoplado (RQ03):** indisponibilidade da projeção de Reporting
   não bloqueia a operação principal de inscrição.
10. **Autorização (RN07/RQ07):** participante só consulta ou altera dados próprios;
    organizador só administra eventos e relatórios da própria organização;
    administrador global pode administrar eventos/atividades e consultar
    relatórios de qualquer organização, mas não administra usuários nem
    organizações. Credencial inválida ou ausência de autorização não concede
    acesso anônimo nem acesso ampliado.
11. **Presença e certificado (RF09/RF10/RN06):** check-in de inscrição
    confirmada é idempotente; certificado só pode ser emitido após encerramento
    da atividade para participante com check-in registrado. Não se aplica
    limiar percentual de presença no protótipo.
12. **Propriedade de dados (RQ05):** serviço não lê nem altera diretamente o
    banco de outro serviço; projeções e integrações usam contratos/APIs ou
    eventos.
13. **Configuração explícita (RQ08):** políticas de domínio e prazos são
    configuráveis fora do código e validados no startup; ausência ou valor
    inválido não vira fallback permissivo silencioso. Prazos e percentual de
    reembolso aprovados são configurações globais externas; override por evento
    não está aprovado.

## Cenários Gherkin aprovados

```gherkin
# language: pt
Funcionalidade: Catálogo e administração de eventos
  Para publicar uma programação e administrar eventos com segurança
  Como organizador ou participante
  Quero acessar apenas o conteúdo e os recursos autorizados

  Cenário: Consultar apenas eventos publicados no catálogo público
    Dado que existem eventos publicados e não publicados
    Quando um participante consulta o catálogo público
    Então os eventos publicados podem ser consultados
    E os eventos não publicados não são expostos

  Cenário: Organizador administra evento da própria organização
    Dado que o organizador pertence à organização responsável pelo evento
    Quando cria, edita, publica ou cancela o evento e suas atividades
    Então a operação é autorizada
    E a programação fica associada à organização responsável

  Cenário: Propagar identificador de correlação da inscrição
    Dado que uma solicitação de inscrição possui um identificador de correlação
    Quando a solicitação produz eventos para serviços consumidores
    Então o identificador acompanha os eventos e registros de diagnóstico
    E é possível relacionar as etapas da mesma inscrição

Funcionalidade: Solicitação de inscrição e ocupação de vagas
  Para controlar a participação numa atividade
  Como participante autenticado
  Quero solicitar uma vaga respeitando capacidade e pagamento

  Cenário: Confirmar inscrição gratuita quando há vaga
    Dado que a atividade gratuita publicada tem uma vaga disponível
    E que a identidade do participante é válida
    Quando o participante solicita inscrição
    Então a inscrição fica confirmada
    E a capacidade ocupada aumenta em uma vaga, sem exceder o limite

  Cenário: Colocar inscrição paga em reserva até a aprovação
    Dado que a atividade paga tem uma vaga disponível
    E que a identidade do participante é válida
    Quando o participante solicita inscrição
    Então a inscrição fica pendente de pagamento
    E a vaga fica ocupada pela reserva durante o prazo configurado
    E a inscrição não é apresentada como confirmada antes da aprovação

  Cenário: Confirmar inscrição paga somente após aprovação
    Dado que existe uma reserva paga ainda válida
    Quando o Billing mock aprova o pagamento dessa reserva
    Então a inscrição fica confirmada
    E a vaga continua ocupada por essa inscrição

  Cenário: Liberar vaga após recusa de pagamento
    Dado que existe uma reserva paga ainda válida
    Quando o Billing mock recusa o pagamento
    Então a inscrição não fica confirmada
    E a reserva é encerrada
    E a vaga é liberada uma única vez

  Cenário: Liberar vaga após timeout da reserva
    Dado que existe uma reserva paga pendente
    Quando o prazo configurado da reserva termina sem aprovação
    Então a inscrição não fica confirmada
    E a reserva é encerrada por expiração
    E a vaga é liberada uma única vez

  Cenário: Não confirmar aprovação recebida após expiração
    Dado que uma reserva paga já expirou e liberou sua vaga
    Quando uma aprovação tardia do Billing mock chega para essa reserva
    Então a inscrição expirada permanece não confirmada
    E nenhuma vaga liberada é consumida novamente
    E o resultado tardio fica disponível para compensação manual

  Cenário: Cancelar inscrição confirmada e disponibilizar a vaga
    Dado que existe uma inscrição confirmada
    Quando o próprio participante cancela a inscrição segundo as regras aplicáveis
    Então a inscrição deixa de ocupar a vaga
    E a vaga fica disponível ou é oferecida conforme a política de espera

  Cenário: Reembolsar e cancelar inscrição paga dentro do prazo
    Dado que a inscrição paga foi solicitada há no máximo sete dias
    E que a atividade ainda não começou
    Quando o participante solicita o cancelamento
    Então o Billing mock recebe uma solicitação de reembolso de 90 por cento do valor pago
    E a inscrição permanece confirmada e ocupa a vaga até o reembolso ser aprovado
    E após a aprovação a inscrição é cancelada e a vaga é liberada uma única vez

  Cenário: Manter inscrição e tentar novamente enquanto o reembolso falha
    Dado que há uma solicitação elegível de cancelamento e reembolso pendente
    Quando o Billing mock recusa ou expira uma tentativa de reembolso
    Então a inscrição permanece confirmada e ocupa a vaga
    E o reembolso continua pendente
    E uma nova tentativa automática é realizada sem limite

  Cenário: Cancelar e liberar vaga após reembolso aprovado em nova tentativa
    Dado que a solicitação de reembolso de uma inscrição está pendente após falhas anteriores
    Quando uma tentativa automática posterior é aprovada
    Então a inscrição é cancelada
    E a vaga é liberada uma única vez
    E a lista de espera habilitada pode receber a oferta

  Cenário: Entrar na lista de espera quando não há vaga
    Dado que a capacidade da atividade está ocupada
    E que a lista de espera está habilitada
    Quando outro participante solicita inscrição
    Então a solicitação entra ao final da fila
    E não é contada como vaga confirmada ou reserva ocupada

  Cenário: Oferecer vaga liberada ao primeiro participante elegível
    Dado que há participantes elegíveis na lista de espera em ordem definida
    E que uma vaga foi liberada
    Quando o sistema promove a lista de espera habilitada
    Então a oferta temporária é enviada ao primeiro participante elegível em ordem FIFO
    E o desempate usa a sequência de aceitação registrada pelo sistema
    E a oferta tem o prazo configurado para resposta
    E nenhuma capacidade acima do limite é criada

  Cenário: Aceitar oferta para atividade paga e iniciar novo pagamento
    Dado que o participante aceitou uma oferta ainda válida para atividade paga
    Quando o sistema processa o aceite
    Então é criada uma nova reserva pendente de pagamento
    E um novo prazo configurado de pagamento é iniciado
    E a inscrição só fica confirmada após aprovação

  Cenário: Avançar a fila após expiração da oferta
    Dado que uma oferta temporária está aguardando resposta
    Quando o prazo configurado da oferta termina sem aceite
    Então a oferta expira e deixa de ocupar vaga
    E a vaga pode ser oferecida ao próximo participante elegível

  Cenário: Impedir overbooking em solicitações concorrentes
    Dado que resta uma vaga na atividade
    E que dois participantes autenticados solicitam inscrição simultaneamente
    Quando as solicitações concorrentes são processadas
    Então no máximo uma solicitação ocupa a vaga
    E a ocupação total nunca excede a capacidade
    E a outra solicitação é recusada ou colocada na lista habilitada

  Cenário: Manter confirmação diante de falha de Notification
    Dado que uma inscrição foi confirmada
    Quando o mock de Notification está indisponível ao receber o evento
    Então a inscrição continua confirmada
    E a falha de notificação pode ser diagnosticada e reprocessada

  Cenário: Manter inscrição diante de indisponibilidade de Reporting
    Dado que a operação de inscrição está disponível
    Quando a projeção de Reporting está indisponível
    Então a solicitação de inscrição segue sua regra normal
    E a indisponibilidade de Reporting não altera sua confirmação

  Cenário: Restringir relatório à organização do organizador
    Dado que o organizador está autenticado para a organização A
    E que a projeção contém dados de organizações A e B
    Quando o organizador consulta indicadores operacionais
    Então são apresentados indicadores apenas da organização A
    E nenhum dado da organização B é revelado

  Cenário: Restringir participante aos próprios dados
    Dado que o participante A está autenticado
    E que a inscrição pertence ao participante B
    Quando o participante A tenta consultar ou cancelar essa inscrição
    Então o acesso é negado
    E nenhum dado da inscrição de B é revelado

  Cenário: Restringir organizador à própria organização
    Dado que um organizador está autenticado para a organização A
    E que o evento pertence à organização B
    Quando o organizador tenta administrar esse evento
    Então a operação é negada
    E o evento permanece inalterado

  Cenário: Administrador global administra recursos de qualquer organização
    Dado que a identidade possui o papel de administrador global
    E que o evento pertence a outra organização
    Quando o administrador consulta relatórios ou administra o evento
    Então a operação é autorizada
    E a identidade não recebe permissão para administrar usuários ou organizações

  Cenário: Rejeitar identidade inválida em operação protegida
    Dado que a credencial apresentada não é válida para o Identity mock
    Quando o solicitante invoca uma operação protegida
    Então a operação é rejeitada como não autenticada
    E não é concedido acesso anônimo

Funcionalidade: Presença e certificado

  Cenário: Registrar check-in elegível uma única vez
    Dado que a inscrição do participante está confirmada e é elegível para check-in
    Quando o mesmo check-in é recebido mais de uma vez
    Então existe um único registro efetivo de presença

  Cenário: Impedir certificado antes do encerramento da atividade
    Dado que a inscrição do participante está confirmada
    E que o participante tem check-in registrado
    E que a atividade ainda não terminou
    Quando o certificado é solicitado
    Então nenhum certificado é emitido

  Cenário: Emitir certificado após o encerramento para participante elegível
    Dado que a inscrição do participante está confirmada
    E que a atividade terminou
    E que o participante tem check-in registrado
    Quando o certificado é solicitado
    Então o certificado pode ser emitido uma única vez
```

## Decisões validadas pelo responsável de negócio

As seguintes decisões foram deliberadas pelo responsável e integram a versão
aprovada:

1. Lista de espera em ordem FIFO; desempate técnico por sequência de aceitação
   registrada pelo sistema.
2. Prazo inicial configurável de 30 minutos para reserva paga e oferta de vaga.
3. Cancelamento pago elegível até sete dias após a solicitação de inscrição e
   antes do início da atividade (o limite que ocorrer primeiro); reembolso
   simulado de 90%, ambos configuráveis.
4. Falha/timeout no reembolso mantém inscrição confirmada e vaga ocupada;
   tentativas automáticas de reembolso não têm limite. Cancelamento só se
   efetiva com aprovação do reembolso.
5. Aceite de oferta paga cria nova reserva e novo prazo de pagamento.
6. Aprovação de pagamento após expiração não confirma a inscrição; o caso é
   encaminhado para compensação manual.
7. Papéis: participante e organizador conforme o escopo da RN07, mais
   administrador global para eventos/atividades e relatórios em qualquer
   organização; sem permissão para administrar usuários/organizações.
8. Certificado exige inscrição confirmada, check-in e atividade encerrada,
   com emissão idempotente. Um check-in basta; percentual de presença de 80%
   fica fora do protótipo, pois sua medição não está no escopo. Nenhum override
   por evento de políticas foi aprovado.

**Registro de validação:** Amg - UECE, responsável de negócio; decisões
validadas uma por vez e versão consolidada aprovada em 2026-11-09.

## Rastreabilidade

| Requisito | Regras/cenários neste documento |
|---|---|
| RF01 | Identity mock e autenticação para operações protegidas |
| RF02 | Cenários de administração autorizada e isolamento por organização |
| RF03 | Administração de evento e atividades pela organização responsável |
| RF04 | Consulta pública expõe somente eventos publicados |
| RF05 | Inscrição gratuita/paga, consulta própria, cancelamento e reembolso |
| RF06 | Capacidade, concorrência, lista FIFO, oferta e expiração |
| RF07 | Aprovação, recusa, timeout, aprovação tardia e reembolso simulado |
| RF08 | Falha de Notification sem rollback |
| RF09 | Check-in idempotente de inscrição confirmada |
| RF10 | Certificado após encerramento e check-in; sem limiar percentual |
| RF11 | Reporting é desacoplado da operação principal e restrito por organização |
| RN01 | Invariante de capacidade e cenário concorrente |
| RN02 | Confirmação paga após aprovação e prazo configurável |
| RN03 | Recusa/expiração libera vaga sem confirmação indevida |
| RN04 | Cancelamento, reembolso, FIFO e promoção da fila |
| RN05 | Notification não desfaz confirmação |
| RN06 | Presença e certificado elegível |
| RN07 | Isolamento de participante, organizador e administrador global |
| RQ01 | Recorte por capacidades e serviços reais/mocados; implantação independente detalhada na decisão arquitetural posterior |
| RQ02 | Capacidade e concorrência são invariantes; perfil de carga até 20× será verificado pela tarefa T041 |
| RQ03 | Falha de Notification/Reporting não bloqueia inscrição |
| RQ04 | Transições, capacidade, idempotência e recuperação |
| RQ05 | Integrações entre serviços por APIs/eventos; sem acesso direto a banco alheio |
| RQ06 | Identificador de correlação propagado nos eventos e registros |
| RQ07 | Identidade, autorização e isolamento multi-organização |
| RQ08 | Prazos/políticas configuráveis e validação FailFast |
