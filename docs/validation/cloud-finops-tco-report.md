# Relatório de Impacto Financeiro e FinOps (Cloud TCO) — Projeto EventHub

| Metadado | Descrição |
|---|---|
| **Projeto** | EventHub — Arquitetura de Microsserviços Políglota |
| **Instituição** | Universidade Estadual do Ceará (UECE) — Arquitetura de Software |
| **Professor** | Prof. Paulo Henrique Maia |
| **Equipe** | Anderson Gomes, Felipe Rodrigues, Tiago Magalhães |
| **Referência nos Slides** | Slide 09 — *Impacto Financeiro da Arquitetura: GCP vs. Azure vs. AWS* |
| **Rastreabilidade** | `ADR-001`, `ADR-002`, `ADR-004`, `ADR-006`, `RQ02`, `RQ08` |
| **Região de Referência** | `us-east` (N. Virginia / South Carolina / East US) — Moeda: USD ($\$$) |
| **Modelo de Precificação** | *Pay-As-You-Go* (*On-Demand*), *Single-AZ* (equivalente ao protótipo), sem descontos de reserva anual |

---

## Sumário

1. [Objetivo e Conexão com a Defesa Arquitetural](#1-objetivo-e-conexão-com-a-defesa-arquitetural)
2. [Glossário Didático de Termos e Jargões (FinOps & Cloud)](#2-glossário-didático-de-termos-e-jargões-finops--cloud)
3. [Metodologia: Da Telemetria Real de Benchmark para o Dimensionamento em Nuvem](#3-metodologia-da-telemetria-real-de-benchmark-para-o-dimensionamento-em-nuvem)
4. [Mapeamento Equivalente da Topologia nas Três Nuvens](#4-mapeamento-equivalente-da-topologia-nas-três-nuvens)
5. [Memória de Cálculo Detalhada por Faixa Mensal (1k, 5k e 20k)](#5-memória-de-cálculo-detalhada-por-faixa-mensal-1k-5k-e-20k)
6. [Por Que a Arquitetura Escolhida Gerou até 58% de Economia no TCO?](#6-por-que-a-arquitetura-escolhida-gerou-até-58-de-economia-no-tco)
7. [Guia de Reprodução nas Calculadoras Oficiais](#7-guia-de-reprodução-nas-calculadoras-oficiais)
8. [Fontes Oficiais e Referências Verificadas](#8-fontes-oficiais-e-referências-verificadas)

---

## 1. Objetivo e Conexão com a Defesa Arquitetural

A pergunta de fechamento da especificação do trabalho questiona:

> *"What did we gain — and what did we pay for distributing the system?"* (O que ganhamos — e o que pagamos por distribuir o sistema?)

Em Engenharia de Software moderna, "o que pagamos" não se resume à complexidade cognitiva ou à consistência eventual: existe um **custo financeiro direto de infraestrutura em nuvem**. Distribuir um sistema em múltiplos microsserviços e *brokers* de mensageria pode multiplicar a conta mensal da organização caso as decisões de isolamento de dados e escalabilidade não sejam desenhadas com responsabilidade econômica.

Este documento explicita o método, a memória de cálculo e as tabelas oficiais de preços da **Google Cloud Platform (GCP)**, **Microsoft Azure** e **Amazon Web Services (AWS)** que fundamentam os números apresentados no **Slide 09** da defesa oral:

| Faixa Mensal (Alinhada à §10.6 do Relatório) | GCP (Cloud Run + Cloud SQL) | Azure (ACA + PG Flex) | AWS (Fargate + RDS + MQ) | Economia vs. Arquitetura com 4 DBs Físicos + Always-On |
|---|---:|---:|---:|---|
| **1.000 inscrições/mês** ($217,9\text{ req/s}$) | $\approx \$42/\text{mês}$ | $\approx \$45/\text{mês}$ | $\approx \$68/\text{mês}$ | **Poupa $\approx \$95/\text{mês}$** (`ADR-002`) |
| **5.000 inscrições/mês** ($211,6\text{ req/s}$) | $\approx \$95/\text{mês}$ | $\approx \$98/\text{mês}$ | $\approx \$135/\text{mês}$ | **Poupa $\approx \$115/\text{mês}$** (`ADR-002` + `RQ02`) |
| **20.000 inscrições/mês** (Pico em $116,1\text{ s}$) | $\approx \$185/\text{mês}$ | $\approx \$195/\text{mês}$ | $\approx \$260/\text{mês}$ | **Poupa $\approx \$255/\text{mês}$ ($-58\%$ TCO)** |

---

## 2. Glossário Didático de Termos e Jargões (FinOps & Cloud)

Para garantir clareza didática durante a apresentação e leitura do relatório, os termos técnicos e financeiros utilizados no slide são definidos a seguir:

### 2.1 Termos Financeiros e de Governança
* **TCO (*Total Cost of Ownership* — Custo Total de Propriedade):** Métrica financeira que soma todos os custos diretos e indiretos para manter um sistema em operação durante um período (no nosso caso, o custo mensal somado de computação dos contêineres, banco de dados relacional, armazenamento SSD, *broker* de mensageria AMQP, balanceadores de carga e ingestão de logs).
* **FinOps (*Cloud Financial Operations*):** Disciplina de engenharia e gestão cultural (mantida pela *FinOps Foundation* / *Linux Foundation*) que une arquitetura de software e responsabilidade financeira na nuvem, tratando o custo como uma métrica de qualidade arquitetural de primeira classe, ao lado de latência e disponibilidade.
* **Pay-As-You-Go / On-Demand (Pago pelo Uso):** Modelo tarifário de nuvem sem fidelidade contratual em que se paga apenas pelos segundos ou horas em que os recursos permanecem alocados. Contrapõe-se a *Reserved Instances* ou *Committed Use Discounts (CUD)* (contratos de 1 a 3 anos).
* **Baseline Tax ("Imposto Base" de Microsserviços):** O custo mínimo fixo mensal necessário apenas para manter o ecossistema distribuído "vivo" (bancos de dados ligados 24/7, *message broker* ativo, balanceador de carga), mesmo quando nenhum usuário está se inscrevendo.

### 2.2 Termos de Computação e Contêineres
* **vCPU-second e GiB-second:** Unidades de cobrança granular de plataformas de contêineres *serverless* (como *GCP Cloud Run* e *Azure Container Apps*). Em vez de pagar por uma máquina virtual inteira o mês todo ($730\text{ horas}$), paga-se exatamente pela multiplicação do número de núcleos virtuais ($\text{vCPU}$) ou memória ($\text{GiB}$) pelo número exato de segundos em que o contêiner processou requisições.
* **Scale-to-Zero (Escala até Zero):** Capacidade de uma plataforma desligar totalmente as réplicas de um microsserviço quando não há tráfego HTTP ou mensagens na fila, reduzindo o custo de computação para $\$0,00$ durante períodos ociosos.
* **Cold Start (Partida a Frio) vs. Min-Instance (Instância Mínima Aquecida):** Quando um serviço está em *scale-to-zero*, a primeira requisição exige subir o contêiner e inicializar o *runtime* (*cold start*). Em Python/FastAPI, o *cold start* é rápido ($\approx 1\text{ a }2\text{ s}$); já na JVM (Java/Spring Boot), pode levar mais tempo. Por isso, em cenários de pico maior (5k e 20k), configura-se `min-instances = 1` para o `Registration` (Java), pagando uma tarifa reduzida de *Idle CPU* (CPU ociosa) para mantê-lo sempre aquecido.
* **Stateless Container (Contêiner sem Estado Local):** Serviço que não guarda dados de negócio nem arquivos de sessão críticos no disco local do contêiner (`ADR-006`), permitindo que a nuvem crie e destrua réplicas em segundos.

### 2.3 Termos de Banco de Dados, Rede e Mensageria
* **Logical Database Isolation vs. Physical Instance Isolation (`ADR-002`):**
  * **Isolamento Físico (4 DBs Físicos):** Criar 4 servidores gerenciados separados na nuvem (4 instâncias de *Amazon RDS* ou *Cloud SQL*), pagando 4 vezes o custo fixo de CPU, RAM, sistema operacional e armazenamento base.
  * **Isolamento Lógico (1 Cluster = 4 Logical DBs):** Criar **1 único servidor gerenciado PostgreSQL 16** contendo **4 bancos de dados lógicos independentes** (`event_management`, `registration`, `attendance_certification` e `reporting`), cada um com usuários, credenciais e migrações estritamente isolados, sem compartilhamento de tabelas nem SQL cruzado.
* **Burstable Instances (Instâncias Intermitentes — Série `T4g` na AWS / Série `B` na Azure):** Instâncias de banco de dados ou VMs econômicas desenhadas para cargas que ficam tranquilas na maior parte do dia e acumulam "créditos de CPU" (*CPU Credits*) para gastar em velocidade máxima durante picos curtos (como a abertura de inscrições de um evento).
* **ALB (*Application Load Balancer*) & LCU (*Load Balancer Capacity Unit*):** Na AWS, para expor contêineres HTTP (ECS Fargate) com roteamento e TLS, utiliza-se um *Application Load Balancer*, que cobra uma taxa fixa por hora ($\$0,0225/\text{hora} \approx \$16,43/\text{mês}$) mais unidades de capacidade processada ($\text{LCU}$, $\$0,008/\text{LCU-hora} \approx \$5,84/\text{mês}$). No *GCP Cloud Run* e no *Azure Container Apps*, esse balanceador HTTP/TLS já está embutido gratuitamente no preço por requisição.

---

## 3. Metodologia: Da Telemetria Real de Benchmark para o Dimensionamento em Nuvem

Diferentemente de estimativas teóricas arbitrárias, o dimensionamento (*sizing*) das três faixas do Slide 09 foi extraído diretamente das **medições empíricas reais** executadas pela ferramenta `scripts/benchmark.sh` do projeto e documentadas na **Seção 10.6 do `README.md`** (`docs/validation/benchmarks/`).

### 3.1 Dados Empíricos Coletados no Repositório

| Faixa de Carga | Fonte no Repositório | Duração do Pico | Vazão Medida | Latência p95 | Pico de CPU dos Contêineres | *Catch-up* do Reporting |
|---|---|---:|---:|---:|---:|---:|
| **1.000 participantes** (Cenários mistos) | `benchmarks/20261112-171442/report.md` | $\approx 31\text{ s}$ | $217,9\text{ req/s}$ | $142\text{ ms}$ | $11,24\text{ cores}$ (rajada curta) | $1,3\text{ s}$ |
| **5.000 participantes** (Cenários mistos / Pico) | `benchmarks/20261112-190017/runs.csv` e `193747` | $37,9\text{ s}$ (pico) a $659\text{ s}$ (misto) | $134,5\text{ a }211,6\text{ req/s}$ | $1.362\text{ a }4.610\text{ ms}$ | $\approx 10\text{ a }12\text{ cores}$ | $1,4\text{ s a }157\text{ s}$ |
| **20.000 participantes** (Pico concentrado) | `benchmarks/20261112-193747/report.md` | $116,1\text{ s}$ | $173,6\text{ req/s}$ | $2.655\text{ ms}$ | RabbitMQ: $4,3\text{ cores}$<br>Registration: $4,1\text{ cores}$<br>Attendance: $1,2\text{ cores}$ | $69,0\text{ s}$ |

### 3.2 A Descoberta Fundamental: Quanto Custa a CPU do Pico de 20.000 Inscrições?

Observando o teste mais extremo do repositório (**20.000 participantes disputando vagas simultaneamente**, `benchmarks/20261112-193747`), a corrida inteira terminou em **$116,1\text{ segundos}$** (menos de $2\text{ minutos}$), mais **$69\text{ segundos}$** para o consumidor do `Reporting` drenar toda a fila do RabbitMQ (totalizando $\approx 185\text{ segundos}$ de processamento intenso).

Se somarmos todas as $\text{vCPUs}$ dos 7 serviços rodando no pico máximo ($\approx 12\text{ vCPUs}$ combinadas nos microsserviços de aplicação e *mocks*, excluindo banco e *broker*) durante um evento de $300\text{ segundos}$ ($5\text{ minutos}$ com folga) no *GCP Cloud Run* ou *Azure Container Apps* (tarifa oficial de $\$0,000024/\text{vCPU-segundo}$ e $\$0,0000025/\text{GiB-segundo}$ para $16\text{ GiB}$ de RAM agregada):

$$\text{Custo de CPU do Pico (20k)} = 12\text{ vCPUs} \times 300\text{ s} \times \$0,000024 = \$0,0864$$

$$\text{Custo de RAM do Pico (20k)} = 16\text{ GiB} \times 300\text{ s} \times \$0,0000025 = \$0,0120$$

$$\text{Custo de Requisições HTTP (100.000 reqs)} = 0,1\text{ milhão} \times \$0,40 = \$0,0400$$

$$\text{Custo Variável Total do Pico de 20.000 Inscrições} \approx \$0,14\text{ a }\$0,15$$

> **Conclusão Metodológica Crítica (Destaque do Slide 09):**
> Processar 20.000 inscrições concorrentes custa **apenas cerca de $\$0,15$ de computação variável** (e na prática sai por **$\$0,00$** dentro da franquia gratuita mensal de $180.000\text{ vCPU-segundos}$ do Cloud Run e do Azure Container Apps!).
> Portanto, o que define a conta mensal de nuvem **não é o volume mensal de requisições**, mas sim o **dimensionamento da infraestrutura persistente (PostgreSQL 16 + RabbitMQ 3.13 + instâncias aquecidas)** necessário para suportar o pico de conexões simultâneas (`200 conexões`) e de *locks* (`SELECT ... FOR UPDATE`) sem sofrer *timeout* no momento em que as inscrições abrem!

---

## 4. Mapeamento Equivalente da Topologia nas Três Nuvens

Para garantir uma comparação justa (*apples-to-apples*), mapeamos os contêineres do `infra/docker-compose.yml` para os serviços gerenciados equivalentes em cada provedor, considerando um mês padrão de $730\text{ horas}$:

| Componente no EventHub | Decisão / Requisito | **GCP** | **Microsoft Azure** | **AWS** |
|---|---|---|---|---|
| **4 Serviços de Domínio + 3 Mocks + Demo Web**<br>*(Java 21 Spring Boot + Python 3.12 FastAPI)* | `ADR-001`, `ADR-005`, `ADR-006`, `RQ02` | **Google Cloud Run**<br>*(Serverless Containers c/ Scale-to-Zero e Ingress HTTPS incluso)* | **Azure Container Apps (ACA)**<br>*(Consumption Plan c/ Scale-to-Zero e Ingress HTTPS incluso)* | **Amazon ECS on AWS Fargate + Application Load Balancer (ALB)** |
| **Cluster PostgreSQL 16**<br>*(1 Cluster físico contendo os 4 bancos lógicos isolados)* | `ADR-002`, `ADR-003`, `RQ04`, `RQ07` | **Cloud SQL for PostgreSQL**<br>*(Single-Zone + SSD Storage)* | **Azure Database for PostgreSQL — Flexible Server**<br>*(Burstable B-Series + SSD)* | **Amazon RDS for PostgreSQL**<br>*(Single-AZ, Graviton2 `db.t4g` + GP3 SSD)* |
| **Broker RabbitMQ 3.13**<br>*(`eventhub.v1`, filas duráveis + `.quarantine`)* | `ADR-004`, `RQ03`, `RQ05` | **RabbitMQ dedicado em Compute Engine (GCE)** *(ou CloudAMQP)* | **RabbitMQ dedicado em Container Apps / Azure VM** *(ou CloudAMQP)* | **Amazon MQ for RabbitMQ**<br>*(Gerenciado nativo da AWS)* |
| **Observabilidade, Logs JSON & Registry** | `RQ06`, `RQ08` | Cloud Logging + Artifact Registry | Azure Monitor / Log Analytics + ACR | CloudWatch Logs + Amazon ECR |

---

## 5. Memória de Cálculo Detalhada por Faixa Mensal (1k, 5k e 20k)

Abaixo está a abertura linha a linha (em dólares americanos por mês, arredondada para o dólar inteiro mais próximo) de cada valor exibido na tabela do **Slide 09**.

---

### 5.1 Faixa 1: Até 1.000 Inscrições/Mês (Pico Leve — $217,9\text{ req/s}$, $\approx 3\text{ a }11\text{ vCPUs}$ em rajada curta)

**Perfil de Dimensionamento:**
* O tráfego mensal total fica abaixo de $50.000$ requisições HTTP e menos de $30.000\text{ vCPU-segundos}$ ativos no mês.
* **No GCP e Azure**, 100% da computação ativa dos contêineres cabe dentro do **Free Tier mensal** ($180.000\text{ vCPU-s}$, $360.000\text{ GiB-s}$ e $2\text{ milhões}$ de requisições gratuitas). Mantemos apenas um custo residual de instância mínima/aquecimento leve ($\approx \$4\text{ a }\$5/\text{mês}$).
* **Na AWS**, não há *Free Grant* perpétuo para o Fargate nem balanceador gratuito: é preciso pagar o **Application Load Balancer (ALB)** fixo ($\approx \$16,43/\text{mês}$ base + LCU) ou manter tarefas mínimas ativas no Fargate.
* **Banco de Dados (1 Cluster c/ 4 Logical DBs):** Instância de entrada com $1\text{ a }2\text{ vCPUs}$ *burstable* e $20\text{ GiB}$ de SSD.
* **RabbitMQ:** Instância enxuta de $1\text{ vCPU}$ / $1\text{ a }2\text{ GiB}$ RAM.

| Item de Custo (Faixa 1.000/mês) | **GCP (`~$42/mês`)** | **Azure (`~$45/mês`)** | **AWS (`~$68/mês`)** |
|---|---|---|---|
| **1. Banco PostgreSQL 16 (1 Cluster, 4 DBs)** | **$\$15$** *(Cloud SQL `db-f1-micro` $\approx \$9,37$ + $20\text{ GiB}$ SSD/IP $\approx \$5,60$)* | **$\$16$** *(PG Flexible `B1ms` $1\text{ vCore}/2\text{ GiB}$: $\$12,41$ + SSD $\$3,60$)* | **$\$16$** *(RDS `db.t4g.micro` $\$0,016/\text{h} = \$11,68$ + $20\text{ GiB}$ GP3 + I/O $\approx \$4,30$)* |
| **2. RabbitMQ 3.13 Durável** | **$\$18$** *(GCE `e2-small` $2\text{ vCPU}/2\text{ GiB}$ $\$13,50$ + Persistent Disk $\$4,50$)* | **$\$19$** *(VM `B1s`/`B1ms` ou container dedicado $24/7$ + disco)* | **$\$19$** *(Amazon MQ `mq.t3.micro` single-instance $\approx \$18,00$ + EBS $\$1,00$)* |
| **3. Computação dos Microsserviços** | **$\$4$** *(Cloud Run: uso ativo $\$0$ no Free Tier; warmup mínimo $\approx \$4$)* | **$\$4$** *(ACA: uso ativo $\$0$ no Free Grant; warmup mínimo $\approx \$4$)* | **$\$25$** *(ALB fixo $\approx \$17,50$ + tasks Fargate ARM/Spot mínimas $\approx \$7,50$)* |
| **4. Logs JSON (`X-Correlation-ID`), Registry & Rede** | **$\$5$** *(Artifact Registry + Egress; primeiros $50\text{ GiB}$ de logs gratuitos)* | **$\$6$** *(Azure Container Registry Basic $\$5$ + Log Analytics dentro da franquia)* | **$\$8$** *(CloudWatch Logs $\$0,50/\text{GB}$ + ECR + tráfego inter-AZ)* |
| **TOTAL MENSAL ESTIMADO** | **$\approx \$42/\text{mês}$** | **$\approx \$45/\text{mês}$** | **$\approx \$68/\text{mês}$** |

---

### 5.2 Faixa 2: Até 5.000 Inscrições/Mês (Pico Médio — $200\text{ conexões}$, $\approx 10\text{ a }12\text{ vCPUs}$ no pico)

**Perfil de Dimensionamento:**
* Conforme medido no *benchmark* de $5.000$ participantes com $200$ conexões simultâneas (`benchmarks/20261112-190017` e `193747`), o banco PostgreSQL recebe intensa contenção de *row locks* (`SELECT ... FOR UPDATE` no `activity_ledger`) e gravações no `outbox`.
* **Banco de Dados:** Precisa subir para **$2\text{ vCPUs}$ e $4\text{ GiB}$ de RAM** para sustentar o *pool* de conexões dos serviços Java/Python e o *buffer pool* sem esgotar memória.
* **RabbitMQ:** Precisa de **$2\text{ vCPUs}$ e $4\text{ GiB}$ de RAM** para absorver a rajada de *publisher confirms* e drenar o *backlog* para `Reporting`, `Attendance` e `Notification`.
* **Computação (`RQ02`):** Mantemos `Registration` e `Event Management` (Java) com `min-instance = 1` aquecida para evitar *cold start* da JVM na abertura das inscrições, enquanto `Attendance`, `Reporting` e *mocks* (Python) escalam sob demanda a partir de zero.

| Item de Custo (Faixa 5.000/mês) | **GCP (`~$95/mês`)** | **Azure (`~$98/mês`)** | **AWS (`~$135/mês`)** |
|---|---|---|---|
| **1. Banco PostgreSQL 16 (1 Cluster, 4 DBs)** | **$\$38$** *(Cloud SQL `db-g1-small` / custom $1\text{--}2\text{ vCPU}$ + $4\text{ GiB}$ RAM + $40\text{ GiB}$ SSD)* | **$\$38$** *(PG Flexible `B2s` $2\text{ vCores}/4\text{ GiB}$ ajustado c/ storage SSD)* | **$\$32$** *(RDS `db.t4g.small` $\$0,032/\text{h} = \$23,36$ + $50\text{ GiB}$ GP3 SSD $\approx \$8,60$)* |
| **2. RabbitMQ 3.13 Durável** | **$\$32$** *(GCE `e2-medium` / `e2-standard-2` + SSD Persistent Disk)* | **$\$32$** *(Instância dedicada $2\text{ vCPU}/4\text{ GiB}$ + Managed Disk)* | **$\$42$** *(Amazon MQ `mq.t3.micro`/`small` c/ maior throughput de disco e I/O)* |
| **3. Computação dos Microsserviços** | **$\$15$** *(Cloud Run: 2 serviços Java c/ min-instance idle $\$0,0000025/\text{vCPU-s}$ + burst)* | **$\$16$** *(ACA: 2 serviços Java c/ min-replica idle $\$0,000003/\text{vCPU-s}$ + burst)* | **$\$48$** *(ALB $\approx \$19$ + Fargate tasks 24/7 p/ Java e workers $\approx \$29$)* |
| **4. Logs JSON, Registry & Rede** | **$\$10$** *(Artifact Registry + Cloud Logging + tráfego de saída)* | **$\$12$** *(ACR + Log Analytics + tráfego de rede)* | **$\$13$** *(CloudWatch Logs + métricas ECS + ECR + dados inter-AZ)* |
| **TOTAL MENSAL ESTIMADO** | **$\approx \$95/\text{mês}$** | **$\approx \$98/\text{mês}$** | **$\approx \$135/\text{mês}$** |

---

### 5.3 Faixa 3: Até 20.000 Inscrições/Mês (Pico Severo — $20.000$ disputando vagas em $116,1\text{ s}$)

**Perfil de Dimensionamento:**
* Na varredura de $20.000$ participantes (`benchmarks/20261112-193747`), a telemetria de recursos (`resources.csv` e `containers-cpu-timeline.png`) mostrou que durante os $116,1\text{ segundos}$ do pico:
  * O contêiner do **RabbitMQ** atingiu pico de **$4,3\text{ núcleos de CPU}$**;
  * O contêiner do **Registration** atingiu pico de **$4,1\text{ núcleos de CPU}$**;
  * O **PostgreSQL** processou dezenas de milhares de transações serializadas no `activity_ledger` e inserções de `outbox`/`inbox`.
* **Banco de Dados:** Dimensionado com **$2\text{ a }4\text{ vCPUs}$ dedicadas, $8\text{ GiB}$ de RAM e SSD com IOPS provisionados** para que o `SELECT ... FOR UPDATE` mantenha o p95 em $\approx 2,6\text{ s}$ sem gargalo de disco.
* **RabbitMQ:** Dimensionado com **$4\text{ vCPUs}$ e $8\text{ GiB}$ de RAM** (no AWS Amazon MQ, isso exige uma instância da classe `mq.m5.large` ou `mq.m7g.large`, cujo custo horário na AWS é substancialmente mais alto).
* **Computação:** Auto-scaling configurado até $4\text{ réplicas}$ por serviço durante o pico e instâncias mínimas aquecidas para os serviços principais.

| Item de Custo (Faixa 20.000/mês) | **GCP (`~$185/mês`)** | **Azure (`~$195/mês`)** | **AWS (`~$260/mês`)** |
|---|---|---|---|
| **1. Banco PostgreSQL 16 (1 Cluster, 4 DBs)** | **$\$85$** *(Cloud SQL Dedicated $2\text{ vCPU}$ @ $\$0,0413/\text{h} = \$60,30$ + $8\text{ GiB}$ RAM $\$15,33$ + $55\text{ GiB}$ SSD $\$9,35$)* | **$\$82$** *(PG Flexible `B2ms` $2\text{ vCores}/8\text{ GiB}$ + $64\text{ GiB}$ Premium SSD c/ IOPS)* | **$\$65$** *(RDS `db.t4g.medium` $2\text{ vCPU}/4\text{--}8\text{ GiB}$ $\$47,45$ + $100\text{ GiB}$ GP3 SSD + IOPS $\approx \$17,55$)* |
| **2. RabbitMQ 3.13 ($4\text{ vCPU}$ no pico)** | **$\$55$** *(GCE `e2-standard-2` / `c2d` $2\text{--}4\text{ vCPU}$ + SSD Persistent Disk)* | **$\$55$** *(VM / ACA Dedicated $4\text{ vCPU}$ + Premium SSD para filas duráveis)* | **$\$110$** *(Amazon MQ `mq.m5.large` / `mq.m7g.large` Single-Instance + EBS storage)* |
| **3. Computação dos Microsserviços** | **$\$30$** *(Cloud Run: min-instances aquecidas + auto-scale até 4 réplicas nos picos)* | **$\$42$** *(ACA: min-replicas aquecidas + auto-scale nos picos)* | **$\$68$** *(ALB $\$22$ + ECS Fargate tasks aquecidas + scale-out nos picos $\$46$)* |
| **4. Logs JSON, Registry & Rede** | **$\$15$** *(Ingestão de logs dos 20k pedidos + Artifact Registry + Egress)* | **$\$16$** *(Log Analytics + ACR + tráfego de rede)* | **$\$17$** *(CloudWatch Logs + Container Insights + ECR + tráfego VPC)* |
| **TOTAL MENSAL ESTIMADO** | **$\approx \$185/\text{mês}$** | **$\approx \$195/\text{mês}$** | **$\approx \$260/\text{mês}$** |

---

## 6. Por Que a Arquitetura Escolhida Gerou até 58% de Economia no TCO?

A última coluna da tabela no Slide 09 (**"Economia vs. 4 DBs Físicos"**) responde à pergunta: *Quanto custaria este mesmo sistema se tivéssemos tomado decisões ingênuas ao aplicar o padrão de microsserviços?*

Em muitos projetos que adotam microsserviços sem um **Filtro Anti-Superengenharia** (Etapa 2 do `processo.md`), cometem-se dois erros caros:
1. **Interpretar *Database per Service* literalmente como 4 servidores físicos separados** (criando 4 instâncias de Cloud SQL / RDS, uma para `Event Management`, outra para `Registration`, outra para `Attendance` e outra para `Reporting`).
2. **Manter todos os 7 serviços rodando 24/7 com capacidade provisionada fixa**, sem explorar o desacoplamento assíncrono (`ADR-004`) para colocar serviços de baixa frequência (como `Attendance & Certification` e `Reporting`) em *Scale-to-Zero* (`RQ02`).

### 6.1 Comparativo Contrafactual (Arquitetura EventHub vs. Arquitetura Distribuída Ingênua no GCP)

| Faixa Mensal | **A. Arquitetura EventHub Adotada**<br>*(1 Cluster PG c/ 4 Logical DBs + Scale-to-Zero Seletivo)* | **B. Arquitetura Distribuída Ingênua**<br>*(4 Instâncias Físicas de BD + 7 Serviços Always-On 24/7)* | **Economia Mensal Gerada pela Arquitetura ($B - A$)** | **Redução Percentual de TCO** |
|---|---:|---:|---:|---:|
| **1.000 inscrições/mês** | **$\$42/\text{mês}$**<br>*(1 DB: $\$15$ + Broker: $\$18$ + Compute: $\$4$ + Logs: $\$5$)* | **$\$137/\text{mês}$**<br>*(4 DBs físicos: $4 \times \$15 = \$60$ + Broker: $\$18$ + 7 serviços 24/7: $\$50$ + Logs: $\$9$)* | **Poupa $\$95/\text{mês}$**<br>*($\$1.140/\text{ano}$)* | **$-69\%$ TCO** |
| **5.000 inscrições/mês** | **$\$95/\text{mês}$**<br>*(1 DB: $\$38$ + Broker: $\$32$ + Compute: $\$15$ + Logs: $\$10$)* | **$\$210/\text{mês}$**<br>*(4 DBs físicos: $\$115$ + Broker: $\$32$ + 7 serviços 24/7: $\$50$ + Logs: $\$13$)* | **Poupa $\$115/\text{mês}$**<br>*($\$1.380/\text{ano}$)* | **$-55\%$ TCO** |
| **20.000 inscrições/mês** | **$\$185/\text{mês}$**<br>*(1 DB: $\$85$ + Broker: $\$55$ + Compute: $\$30$ + Logs: $\$15$)* | **$\$440/\text{mês}$**<br>*(4 DBs físicos dedicados: $3 \times \$65 + \$85 = \$280$ + Broker: $\$55$ + Compute 24/7: $\$85$ + Logs: $\$20$)* | **Poupa $\$255/\text{mês}$**<br>*($\$3.060/\text{ano}$)* | **$-58\%$ TCO** |

### 6.2 Os 3 Pilares Arquiteturais que Explicam Essa Economia

1. **Pilar 1 — `ADR-002` (1 Cluster PostgreSQL = 4 Bancos Lógicos Isolados):**
   * Preserva 100% dos benefícios arquiteturais do padrão *Database per Service* (credenciais independentes por serviço, migrações Flyway/Alembic separadas, zero *foreign keys* ou *joins* entre serviços).
   * Elimina **75% do custo fixo de instâncias de banco de dados** (paga-se por 1 *buffer pool* compartilhado e 1 conjunto de processos de background do PostgreSQL em vez de 4).
   * Além disso, o **Frozen Admission Snapshot** (`ADR-002`) evita consultas síncronas repetitivas ao banco do `Event Management` a cada tentativa de reserva, reduzindo o consumo de CPU do banco do catálogo.
2. **Pilar 2 — `RQ02` + `ADR-001` + `ADR-004` (Escalabilidade Seletiva e *Scale-to-Zero*):**
   * Como a especificação destaca em `RQ02`, *"durante a abertura de inscrições, consultas e inscrições podem receber $20\times$ a carga normal, enquanto certificados permanecem quase ociosos"*.
   * Ao separar `Registration` (Java) de `Attendance & Certification` (Python) e `Reporting` (Python), os serviços de pós-evento permanecem em **Scale-to-Zero** durante a corrida de vendas, despertando apenas de forma assíncrona via RabbitMQ (`ADR-004`) para processar lotes rápidos de mensagens e voltando a custar $\$0,00$ logo em seguida.
3. **Pilar 3 — `ADR-006` + Ausência de API Gateway / Orquestrador de Saga (`README.md §7`):**
   * Rejeitar um *API Gateway* dedicado e uma plataforma externa de *Saga Orchestration* (como Camunda/Temporal) poupou pelo menos **2 contêineres/serviços adicionais rodando 24/7**.
   * Desenhar os serviços e a `Demo Web` como contêineres *stateless* permitiu o uso direto do **Google Cloud Run** e do **Azure Container Apps**, que já incluem balanceamento HTTPS e *auto-scaling* sem a cobrança fixa de $\approx \$22/\text{mês}$ do *Application Load Balancer (ALB)* exigido pelo AWS ECS Fargate.

---

## 7. Guia de Reprodução nas Calculadoras Oficiais

Qualquer avaliador ou membro da equipe pode reproduzir e auditar estas estimativas em menos de 5 minutos seguindo os passos abaixo:

1. **Para reproduzir os dados de uso de CPU/RAM do EventHub localmente:**
   ```sh
   # Executa a varredura de benchmark coletando CPU/RAM por contêiner em CSV
   bash scripts/benchmark.sh 1000 5000 20000 --scenario pico-inscricoes --connections 200
   ```
   Os arquivos `runs.csv` e `resources.csv` gerados em `docs/validation/benchmarks/<timestamp>/` trazem os segundos exatos de duração (`duration_s`) e os núcleos de CPU consumidos (`cpu_cores_avg`, `cpu_cores_peak`).
2. **Na Calculadora do Google Cloud (`https://cloud.google.com/products/calculator`):**
   * Adicione **Cloud Run**: selecione região `us-east1`, marque *CPU is only allocated during request processing*, insira o tempo ativo mensal (abaixo de $180.000\text{ vCPU-s}$ na faixa de 1k) e $1\text{ min-instance}$ para as faixas de 5k e 20k.
   * Adicione **Cloud SQL for PostgreSQL**: selecione *Single zone*, escolha `db-f1-micro` (1k), `db-g1-small` (5k) ou *Dedicated* $2\text{ vCPUs} / 8\text{ GiB}$ (20k) com SSD.
   * Adicione **Compute Engine**: $1\text{ instância}$ `e2-small` (1k), `e2-medium` (5k) ou `e2-standard-2` (20k) para o RabbitMQ.
3. **Na Calculadora do Microsoft Azure (`https://azure.microsoft.com/en-us/pricing/calculator/`):**
   * Adicione **Azure Container Apps** (*Consumption Workload Profile*, aplicando o *Free Grant* de $180.000\text{ vCPU-s}$ e $360.000\text{ GiB-s}$) + **Azure Database for PostgreSQL Flexible Server** (Burstable `B1ms`, `B2s` ou `B2ms`).
4. **Na Calculadora da AWS (`https://calculator.aws/`):**
   * Adicione **AWS Fargate** + **Elastic Load Balancing (Application Load Balancer)** + **Amazon RDS for PostgreSQL** (`db.t4g.micro`, `db.t4g.small` ou `db.t4g.medium`, *Single-AZ*) + **Amazon MQ for RabbitMQ** (`mq.t3.micro` ou `mq.m5.large`, *Single-Instance*).

---

## 8. Fontes Oficiais e Referências Verificadas

Todas as URLs abaixo foram verificadas e correspondem à documentação pública oficial de precificação dos respectivos provedores e fundações:

1. **Google Cloud — Cloud Run Pricing (Tarifas por vCPU-segundo, GiB-segundo, Min-Instance Idle e Free Tier):**
   * <https://cloud.google.com/run/pricing>
2. **Google Cloud — Cloud SQL for PostgreSQL Pricing (Instâncias Shared-Core, Dedicated vCPU e Armazenamento SSD):**
   * <https://cloud.google.com/sql/pricing>
3. **Google Cloud — Pricing Calculator Oficial:**
   * <https://cloud.google.com/products/calculator>
4. **Microsoft Azure — Azure Container Apps Pricing & Billing Documentation (Consumption Plan e Free Grants):**
   * <https://azure.microsoft.com/en-us/pricing/details/container-apps/>
   * <https://learn.microsoft.com/en-us/azure/container-apps/billing>
5. **Microsoft Azure — Azure Database for PostgreSQL (Flexible Server Burstable B-Series e General Purpose):**
   * <https://azure.microsoft.com/pt-br/pricing/details/postgresql/flexible-server/>
6. **Microsoft Azure — Pricing Calculator Oficial:**
   * <https://azure.microsoft.com/en-us/pricing/calculator/>
7. **Amazon Web Services — AWS Fargate Pricing (ECS Task vCPU/hora e GB/hora):**
   * <https://aws.amazon.com/fargate/pricing/>
8. **Amazon Web Services — Elastic Load Balancing Pricing (Custo fixo por hora de ALB e LCU):**
   * <https://aws.amazon.com/elasticloadbalancing/pricing/>
9. **Amazon Web Services — Amazon RDS for PostgreSQL Pricing (Instâncias `db.t4g` On-Demand e Armazenamento GP3):**
   * <https://aws.amazon.com/rds/postgresql/pricing/>
10. **Amazon Web Services — Amazon MQ for RabbitMQ Pricing (Instâncias `mq.t3` e `mq.m5`/`mq.m7g`):**
    * <https://aws.amazon.com/amazon-mq/pricing/>
11. **Amazon Web Services — AWS Pricing Calculator Oficial:**
    * <https://calculator.aws/>
12. **FinOps Foundation (Linux Foundation) — FinOps Framework & Cloud Unit Economics:**
    * <https://www.finops.org/framework/>