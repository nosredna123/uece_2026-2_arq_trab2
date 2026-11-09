# Configuração do ambiente de desenvolvimento

Este guia cobre os pré-requisitos da máquina para o EventHub: Git, Docker e o
plugin Docker Compose para a CLI. Verifique o que já existe antes de instalar
ou atualizar qualquer componente. Prefira os pacotes oficiais ou mantidos pela
distribuição; não remova nem substitua uma instalação funcional para seguir um
exemplo genérico.

## Requisitos

- **Git**: versão 2.x mantida pela distribuição ou fornecedor. O Git é usado
  para obter o projeto e registrar alterações.
- **Docker**: Docker Engine e Docker CLI em Linux, ou Docker Desktop em macOS e
  Windows. Use uma versão atualmente suportada pelo fornecedor e compatível com
  a versão do sistema operacional.
- **Compose**: plugin compatível com Docker Compose v2, invocado como
  `docker compose`. O comando legado `docker-compose` não substitui este
  requisito.
- **Linux**: distribuição e arquitetura suportadas pelo Docker. O usuário que
  executa os comandos precisa acessar o daemon por uma configuração autorizada.
- **macOS**: versão do macOS suportada pela versão atual do Docker Desktop e
  pelo menos 4 GB de memória.
- **Windows**: versão de Windows ainda suportada pela Microsoft e pela versão
  atual do Docker Desktop. O backend WSL 2 é a opção recomendada para a maioria
  das instalações; confira os requisitos atuais de WSL, edição e virtualização
  na documentação oficial.

Não há um número mínimo mais específico para Git, Docker ou Compose aprovado
pelas especificações do projeto. Use versões suportadas e registre as versões
exatas no gate de prontidão do ambiente; fixe limites adicionais apenas após
validá-los com o Compose e as imagens do projeto.

## Inventário antes de instalar

Execute os comandos aplicáveis no terminal:

```sh
git --version
docker version
docker context show
docker info
docker compose version
```

`docker version` deve mostrar as informações de cliente e servidor;
`docker info` confirma que o daemon está acessível no contexto selecionado.
Se o plugin não estiver instalado, `docker compose version` falhará — isso é
distinto de uma falha de acesso ao daemon. Registre sistema operacional,
arquitetura, versões e contexto antes de escolher uma instalação.

## Instalação por sistema operacional

### Linux

1. **Git**: use o gerenciador de pacotes da distribuição. Confira primeiro
   `git --version`; se necessário, instale pelo pacote oficial da distribuição.
   Em Debian/Ubuntu:

   ```sh
   sudo apt-get update
   sudo apt-get install git
   ```

   Em Fedora:

   ```sh
   sudo dnf install git
   ```

   Para outras distribuições, siga a documentação oficial do respectivo
   gerenciador de pacotes.
2. **Docker Engine e CLI**: siga a página oficial de instalação do Docker para
   a distribuição e versão exatas. Se o Engine já estiver instalado e
   funcional, não o reinstale para obter Compose.
3. **Compose**: se o repositório oficial do Docker já estiver configurado e
   for compatível com a distribuição, instale o pacote
   `docker-compose-plugin` pelo gerenciador de pacotes e verifique
   `docker compose version`. Em Debian/Ubuntu, a documentação do Docker usa:

   ```sh
   sudo apt-get update
   sudo apt-get install docker-compose-plugin
   ```

   Se esse pacote não estiver disponível, não adicione repositórios de outra
   distribuição nem misture pacotes. Consulte as instruções oficiais do Docker
   para configurar o repositório correto ou instale o plugin apenas para o
   usuário a partir de uma versão oficial cuja soma de verificação tenha sido
   validada. A instalação manual requer atualização manual; o método pelo
   repositório é preferível quando disponível.
4. **Distribuições derivadas**: Linux Mint e outras derivadas devem seguir as
   instruções da distribuição-base somente quando a versão correspondente for
   compatível com o Docker. Não presuma que um pacote Ubuntu é compatível apenas
   porque o sistema é baseado em Ubuntu.

Não execute instaladores remotos com `curl | sh`, não remova pacotes existentes
às cegas e não altere repositórios, firewall, portas ou serviços sem verificar
o impacto e obter autorização. Pertencer ao grupo `docker` concede privilégios
equivalentes a root; não adicione usuários a esse grupo automaticamente. Se o
daemon negar acesso, peça ao administrador uma configuração de acesso apropriada
em vez de enfraquecer permissões.

### macOS

1. Verifique `git --version`. Se necessário, instale Git pelas ferramentas de
   linha de comando do Xcode (`xcode-select --install`) ou por um gerenciador de
   pacotes de confiança, seguindo as instruções do projeto Git.
2. Instale Docker Desktop pelo instalador oficial para Apple silicon ou Intel.
   Docker Desktop inclui o Engine, a CLI e Compose; não instale um Compose
   standalone em paralelo.
3. Inicie Docker Desktop, conclua a configuração/licença exibida pelo
   aplicativo e valide com os comandos de inventário.

Docker Desktop suporta a versão atual e as duas versões principais anteriores
do macOS, conforme a documentação vigente. Confira ali os requisitos do chip e
as condições de licença aplicáveis.

### Windows

1. Verifique `git --version`. Se necessário, baixe e instale Git for Windows
   pelo projeto oficial; mantenha a configuração de instalação padrão, salvo
   necessidade conhecida.
2. Instale Docker Desktop pelo instalador oficial. A instalação por usuário é
   recomendada quando disponível; use WSL 2 conforme os requisitos vigentes.
   Docker Desktop inclui Compose, portanto não instale uma cópia standalone.
3. Inicie Docker Desktop e valide a CLI no PowerShell ou no terminal usado pelo
   projeto:

   ```powershell
   git --version
   docker version
   docker context show
   docker info
   docker compose version
   ```

Docker Desktop requer uma versão de Windows ainda suportada e recursos de
virtualização/WSL compatíveis. Consulte a matriz oficial antes de instalar; o
Docker Desktop não é suportado em Windows Server.

## Validação após a instalação

Repita os comandos de inventário. A configuração está pronta para a próxima
verificação quando Git e Docker respondem, `docker info` alcança o daemon no
contexto esperado e `docker compose version` identifica o plugin Compose. O
gate da Fase 0 ainda deve verificar execução de containers, rede, volumes,
recursos e portas; a simples presença dos comandos não aprova a prontidão.

## Documentação oficial

- [Git: instalação por sistema operacional](https://git-scm.com/install/)
- [Docker Engine: plataformas e instruções de instalação](https://docs.docker.com/engine/install/)
- [Docker Compose: instalação do plugin](https://docs.docker.com/compose/install/)
- [Docker Desktop para macOS](https://docs.docker.com/desktop/setup/install/mac-install/)
- [Docker Desktop para Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
