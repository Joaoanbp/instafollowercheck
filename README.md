# InstaFollowCheck

Painel local para acompanhar suas relações do Instagram no próprio computador. Mostra
seguidores, contas que você segue, relações não recíprocas e quem deixou de seguir na
janela escolhida. A coleta usa uma janela do Chrome controlada pelo app, com um perfil
exclusivo; não é uma extensão de terceiros e não envia dados para nuvem.

O app é **somente leitura**: ele não segue, deixa de seguir, bloqueia nem envia mensagens.

## Início rápido

No terminal normal do Ubuntu, dentro da pasta do projeto:

```bash
bash iniciar.sh
```

Na primeira execução, o iniciador cria o ambiente Python e instala Flask, Playwright e
Waitress. Depois ele abre o painel no navegador e exibe o link local no terminal.

1. No painel, clique em **Conectar conta** e informe seu usuário sem `@`.
2. O app abre uma janela do Chrome com um perfil separado do seu navegador pessoal.
3. Entre no Instagram nessa janela e conclua 2FA ou verificações. O app detecta a sessão
   e continua a coleta sozinho.
4. Aguarde a leitura de seguidores e seguindo. O primeiro snapshot é a linha de base; os
   deltas aparecem a partir da coleta seguinte.

O login fica salvo no perfil do app. Nas próximas execuções, basta clicar em
**Atualizar dados**. O perfil pessoal do Chrome não é lido nem copiado.

Se o Chrome não estiver instalado, o iniciador tenta baixar o Chromium do Playwright.
Também é possível instalar o Google Chrome normalmente no Ubuntu.

## O que o painel mostra

- **Seguidores** e **seguindo** no snapshot mais recente.
- **Não te seguem de volta**: você segue, mas a conta não segue você.
- **Você não segue de volta**: a conta segue você, mas você não a segue.
- **Deixaram de seguir**: contas que estavam no histórico e não estão na lista atual.
- **Novos seguidores**, **você deixou de seguir** e **novos seguindo**.
- **Mútuos** e **possíveis soft blocks** (perda nas duas direções).
- Busca por usuário, paginação e exportação CSV de cada lista.
- Histórico de snapshots com contagens e variações.
- Ajuste da janela de análise e do intervalo mínimo entre coletas.

A janela padrão é de **10 dias**. A comparação usa o snapshot mais antigo disponível
dentro da janela como referência. Se não houver nenhum snapshot dentro dela, o app usa a
coleta anterior mais próxima e mostra um aviso com a janela efetiva. Assim, os números
nunca escondem que o histórico é menor ou maior que o período pedido.

Uma perda não confirma a causa. Unfollow manual, bloqueio, conta desativada e exclusão
podem produzir o mesmo resultado. Eventos que acontecem e são revertidos entre duas
coletas não são detectados.

## Importar a exportação oficial

Quando a coleta pelo navegador não for possível, use **Baixar suas informações** no
Instagram. O painel aceita:

- o ZIP completo da exportação; ou
- os dois arquivos JSON de seguidores e seguindo selecionados juntos.

No painel, clique em **Importar exportação**, informe o usuário e selecione os arquivos.
A importação é local, não acessa o Instagram e entra no mesmo histórico da conta. Se a
exportação foi importada antes da primeira coleta pelo navegador, o app une os registros
quando o ID real da conta aparece.

Também é possível importar pelo terminal:

```bash
bash iniciar.sh import-export \
  --username seu.usuario \
  --file ~/Downloads/instagram-seu.usuario.zip
```

Uma exportação incompleta, um ZIP corrompido ou um único JSON de seguidores é recusado:
sem as duas listas não é seguro calcular deltas.

## Privacidade e segurança

- O servidor escuta apenas em `127.0.0.1` e exige um token local exibido no terminal.
- POSTs usam token CSRF, verificação de `Host` e de `Origin`.
- Não há CDN, fonte externa, telemetria ou requisição para servidores do projeto.
- O perfil do Chrome, o banco SQLite e os logs ficam em um diretório com permissão
  privada do seu usuário. O banco também é criado com permissão `600`.
- A senha do Instagram é digitada apenas na janela do Chrome, no domínio do Instagram.
  O app não armazena senha.
- Os dados locais não são criptografados. Proteja sua conta do Ubuntu e seus backups.
- Não compartilhe o link do terminal: ele contém o token de acesso ao painel local.

Diretório padrão:

```text
~/.local/share/instafollowcheck/
├── history.sqlite3       # snapshots completos
├── browser/<conta>/      # perfil exclusivo do Chrome
└── logs/app.log
```

Use **Configurações** para apagar o histórico de uma conta ou redefinir apenas o perfil
do Chrome. O banco antigo do InstaFollowCheck é migrado automaticamente quando tem o
formato anterior; o histórico antigo não é apagado.

## Comandos

```bash
bash iniciar.sh                          # abre o painel (padrão)
bash iniciar.sh serve --no-browser       # só inicia o servidor e mostra o link
bash iniciar.sh collect --username user  # coleta manual pelo Chrome
bash iniciar.sh accounts                 # lista os históricos locais
bash iniciar.sh import-export --username user --file export.zip
```

O pacote também pode ser chamado direto:

```bash
.venv/bin/python -m instafollowcheck serve
.venv/bin/python -m instafollowcheck --help
```

Não há cron nem coleta em segundo plano por padrão. A coleta é feita quando você pede,
o que deixa o ritmo sob seu controle.

## Se algo falhar

- **A janela do Chrome não abriu**: confira se o Chrome está instalado e se outra janela
  do app não está aberta. O perfil fica em `browser/<conta>`; feche a janela e tente de novo.
- **Login não concluído**: repita a coleta. Você tem cerca de 10 minutos para entrar na
  janela do Chrome. Se o perfil ficou inconsistente, use **Redefinir perfil do Chrome**.
- **Sessão em outra conta**: saia da conta errada na janela do app ou redefina o perfil
  daquela conta e entre com a conta correta.
- **O Instagram pediu verificação ou limitou solicitações**: resolva no aplicativo oficial
  e aguarde. Aumente o intervalo entre coletas em **Configurações**. O app não contorna
  bloqueios do Instagram.
- **Lista incompleta**: o snapshot é descartado para não gerar unfollows falsos. Tente
  novamente mais tarde; a contagem do perfil e a lista precisam bater dentro de 1%.
- **Porta ocupada**: o app procura automaticamente uma porta livre a partir de 8765.

O Instagram pode mudar as interfaces web ou restringir automação. Este app reduz o risco
em relação a extensões e serviços de terceiros, mas não garante disponibilidade permanente.

## Desenvolvimento

Requisitos: Python 3.10 ou superior, SQLite (biblioteca padrão) e Chrome/Chromium.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

Estrutura:

```text
src/instafollowcheck/
├── app.py          # Flask, token local, CSRF, APIs
├── collector.py    # Playwright e parsers de paginação
├── db.py           # SQLite, snapshots e migração
├── domain.py       # janela, relações e deltas
├── importer.py     # exportação oficial (ZIP/JSON)
├── jobs.py         # coleta em segundo plano
├── cli.py          # linha de comando
├── templates/      # painel, histórico, configurações
└── static/         # CSS e JavaScript locais
tests/              # testes offline, sem rede ou credenciais
```

Os testes usam banco temporário, exportações sintéticas e paginação simulada. Nenhum
teste abre o Instagram ou usa sua sessão real. A coleta real só pode ser validada com
login manual no perfil dedicado do Chrome.
