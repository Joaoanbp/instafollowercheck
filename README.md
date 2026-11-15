# InstaFollowCheck

Monitor local em Python para Ubuntu. Usa uma sessão existente do Instaloader,
guarda snapshots completos no SQLite e gera relatórios TXT em português. Não faz
login durante a coleta nem altera relações no Instagram.

O relatório inclui seguidores, seguindo, mútuos, quem não segue você de volta,
quem você não segue de volta, seguidores perdidos, contas que você deixou de
seguir e possíveis soft blocks. As sugestões de unfollow são a lista de relações
não recíprocas, com links para você revisar manualmente.

## Início rápido

No terminal normal do Ubuntu, execute:

```bash
bash "$HOME/instafollowcheck/iniciar.sh"
```

O iniciador pergunta seu usuário do Instagram e o navegador no terminal. Digite
o usuário sem `@`. Na primeira execução, informe o navegador em que sua conta
já está aberta. Nas próximas, pressione Enter na pergunta do navegador para
reutilizar a sessão salva. Também é possível passar ambos diretamente, por
exemplo: `bash iniciar.sh SEU_USUARIO chrome`.

Primeiro entre na conta pelo navegador escolhido e conclua as verificações do
Instagram.
Se o acesso estiver bloqueado, recupere-o pelo aplicativo oficial antes de executar
qualquer comando de autenticação ou coleta. Confirmar uma tentativa de login não
garante que uma restrição já foi removida.

O iniciador instala os componentes necessários, prepara o ambiente, importa a sessão
do navegador, executa a coleta e, após sucesso, configura o cron diário às 08:15.
Ele nunca tenta autenticação com senha do Instagram. A senha do Ubuntu pode ser solicitada para
instalar pacotes ou ativar o cron. Não execute o iniciador inteiro com `sudo`.
Nas próximas execuções, `bash iniciar.sh SEU_USUARIO` reutiliza a sessão salva.
Outras contas podem usar `bash iniciar.sh outro_usuario edge`. Tarefas existentes do
cron são preservadas; a tarefa marcada para essa conta é atualizada sem duplicação.
As instruções manuais abaixo permitem executar cada etapa separadamente.

### Se aparecer `Checkpoint required`

O Instagram exige uma verificação da conta. A mensagem inicial de que o arquivo
de sessão não existe é esperada na primeira autenticação; o checkpoint é o que
interrompe o login. Se houve bloqueio, interrompa as tentativas do script e conclua
a recuperação no aplicativo oficial. Após normalizar o acesso, abra
<https://www.instagram.com/> no navegador escolhido, entre em
`SEU_USUARIO` e conclua as verificações solicitadas pelo Instagram. Se necessário,
abra o endereço de verificação indicado no terminal no domínio oficial do Instagram.
Depois use a sessão autenticada do navegador:

```bash
bash "$HOME/instafollowcheck/iniciar.sh" SEU_USUARIO firefox
```

O iniciador aceita Mozilla Firefox (`firefox`), Google Chrome (`chrome`),
Microsoft Edge (`edge`), LibreWolf (`librewolf`), Chromium (`chromium`) e Brave
(`brave`) como segundo argumento. Não é necessário repetir o login
com senha pelo Instaloader. A importação ainda depende de uma sessão válida e das
permissões locais do navegador; ela não remove uma restrição imposta pelo Instagram.
O cron só é configurado depois que a coleta completa termina com sucesso.
Mesmo com cookies do navegador, o Instagram pode detectar a automação e restringir
novamente o acesso; esta ferramenta não garante ausência de bloqueios.

Se o navegador usa um perfil alternativo ou o Firefox Snap e os cookies não forem
encontrados, informe o caminho do arquivo de cookies como terceiro argumento:

```bash
bash "$HOME/instafollowcheck/iniciar.sh" SEU_USUARIO firefox /caminho/do/perfil/cookies.sqlite
```

### Chrome: `Failed to find cookies for Chrome browser`

Esse erro indica que a biblioteca não localizou o arquivo local de cookies;
nessa etapa a sessão ainda não foi testada no Instagram. O iniciador procura os
locais conhecidos do Chrome/Chromium e passa o arquivo explicitamente à biblioteca.
Se houver vários perfis ou nenhum arquivo encontrado, ele solicita o caminho correto
no terminal interativo, sem testar várias sessões no Instagram.

No Chrome em que você acessa a conta, abra `chrome://version` e copie **Caminho
do perfil / Profile Path**. Você também pode informar esse diretório diretamente:

```bash
bash "$HOME/instafollowcheck/iniciar.sh" SEU_USUARIO chrome "/caminho/copiadodo/Chrome/Default"
```

O exemplo é um marcador: use o caminho real mostrado pelo seu Chrome. O iniciador
aceita o diretório do perfil ou o próprio arquivo `Cookies`/`Network/Cookies`.
Essa descoberta verifica apenas os caminhos dos arquivos; não imprime seus cookies.
Não execute com sudo nem altere as permissões dos arquivos do navegador. A leitura
e descriptografia ainda podem falhar por bloqueio do arquivo ou acesso ao chaveiro.
Referência: [localizar o perfil do Chrome](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md#current-location).

### Ubuntu no WSL e navegador no Windows

Um Profile Path iniciado por `C:\Users\...` é de um navegador Windows. Mesmo
que o arquivo esteja visível em `/mnt/c/Users/...`, o código de descriptografia
do Chrome no Linux não equivale ao do Windows. O iniciador informa essa
incompatibilidade antes de tentar importar um caminho Windows explícito.

Uma alternativa é usar Firefox ou LibreWolf no Windows e passar o arquivo
`cookies.sqlite` diretamente ao iniciador no Ubuntu. Com a conta já acessível
normalmente, entre pelo navegador escolhido e feche-o normalmente para concluir
a gravação dos cookies. Exemplo para um perfil Firefox:

```bash
bash iniciar.sh SEU_USUARIO firefox "/mnt/c/Users/Usuario/AppData/Roaming/Mozilla/Firefox/Profiles/SEU_PERFIL/cookies.sqlite"
```

Substitua `SEU_PERFIL` pelo perfil real. No Firefox, `about:profiles` mostra o
diretório raiz de cada perfil. Essa alternativa não exige desativar a proteção
dos cookies do Chrome; permanece sujeita à validade da sessão e às restrições
do Instagram. Navegadores instalados no próprio Ubuntu também podem ser usados.
No WSL, o cron depende de a distribuição estar em execução no horário agendado.

## 1. Instalar

Python 3.10 ou superior. Execute no terminal do Ubuntu, sem rodar o monitor como root:

```bash
sudo apt update
sudo apt install python3-venv python3-pip cron
cd "$HOME/instafollowcheck"
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

O código está em `src/instafollowcheck/`; testes offline em `tests/`.
SQLite, arquivos e travas usam a biblioteca padrão. Instaloader é a dependência
de execução; a faixa compatível está em `pyproject.toml`.

## 2. Criar uma sessão inicial

Escolha **uma** alternativa abaixo. Substitua `SEU_USUARIO` pelo seu @ sem o sinal.
A conta autenticada deve ser a própria conta monitorada.

### Alternativa A: sessão já aberta no navegador

Entre no Instagram pelo navegador escolhido e conclua os desafios/2FA no navegador.
Depois execute como seu usuário normal:

```bash
cd "$HOME/instafollowcheck"
.venv/bin/python -m pip install browser-cookie3
umask 077
mkdir -p "$HOME/.config/instaloader"
chmod 700 "$HOME/.config/instaloader"
.venv/bin/instaloader --load-cookies firefox --sessionfile "$HOME/.config/instaloader/session-SEU_USUARIO"
chmod 600 "$HOME/.config/instaloader/session-SEU_USUARIO"
```

Não combine `--load-cookies` com `--login`. O comando importa os cookies do perfil
principal do navegador e salva uma sessão Instaloader; perfis alternativos podem
precisar de `--cookiefile /caminho/para/cookies.sqlite`. Chrome pode substituir
`firefox`, dependendo do acesso local ao armazenamento e ao chaveiro do navegador.
Se a importação não funcionar, use a alternativa B.

### Alternativa B: autenticação interativa uma vez

```bash
cd "$HOME/instafollowcheck"
umask 077
mkdir -p "$HOME/.config/instaloader"
chmod 700 "$HOME/.config/instaloader"
.venv/bin/instaloader --login SEU_USUARIO --sessionfile "$HOME/.config/instaloader/session-SEU_USUARIO"
chmod 600 "$HOME/.config/instaloader/session-SEU_USUARIO"
```

Digite a senha no prompt oculto e o código 2FA quando solicitado. A senha não é
gravada pelo monitor. Não use `--password`, variáveis com senha ou senha no cron.
Se uma sessão expirada impedir a renovação, remova somente seu arquivo
`session-SEU_USUARIO` e repita a alternativa escolhida.

**O arquivo de sessão permite acesso à conta.** Mantenha-o fora do repositório,
com permissão `600`; nunca carregue sessões recebidas de terceiros: o formato
usado pelo Instaloader é pickle. Dados locais não são criptografados; proteja sua
conta Linux e backups. O monitor recusa sessão com permissões abertas ou symlink.

## 3. Executar e consultar

```bash
cd "$HOME/instafollowcheck"
.venv/bin/python -m instafollowcheck --usuario SEU_USUARIO
ls -lt "$HOME/.local/share/instafollowcheck/reports/"
```

Opções: `--session-file /caminho/absoluto`, `--data-dir /caminho/privado` e
`--quiet` (omite sucesso; erros continuam no stderr). Os caminhos padrão são
resolvidos a partir do diretório pessoal, independentemente do diretório atual.

O banco fica em `~/.local/share/instafollowcheck/history.sqlite3` e os TXT em
`reports/relatorio_USUARIO_AAAA-MM-DD_ID.txt` dentro desse diretório. O ID evita
sobrescrever relatórios em execuções no mesmo dia. Diretórios de dados têm modo
`700`; banco e relatórios novos, `600`. O histórico permanece até você o apagar;
faça backups somente com o processo parado.

A primeira coleta estabelece a linha de base. Cada coleta posterior compara com
a última coleta **completa**, exibindo as datas reais, mesmo após dias sem execução.
IDs estáveis evitam falsos unfollows por mudança de nome. Cada perda lista o último
@ conhecido, que pode já não corresponder ao nome atual.

Uma perda nas duas direções é somente um **possível soft block**. As listas não
identificam quem iniciou a mudança nem se foi involuntária. Unfollows manuais,
bloqueios, exclusões e desativações também podem explicar perdas. Eventos ocorridos
e revertidos entre duas coletas não são detectados.

Falhas de sessão, rede, restrições, listas incompletas ou contagens que mudam durante
a coleta descartam o snapshot e preservam o histórico anterior. A coleta não é
atômica no Instagram: a conferência de contagens reduz, mas não elimina, inconsistências.
Não há tentativas repetidas após falhas; os intervalos do Instaloader são mantidos.
Se somente a exportação falhar após salvar o snapshot, reexporte sem rede:

```bash
"$HOME/instafollowcheck/.venv/bin/python" -m instafollowcheck --usuario SEU_USUARIO --report-only
```

Instaloader usa interfaces não oficiais: mudanças no Instagram podem interromper
a coleta, mesmo com sessão válida. Reutilizar cookies não garante ausência de
bloqueios. Em desafios ou restrições, confira o aplicativo oficial e aguarde;
renove a sessão manualmente quando necessário.

## 4. Agendar diariamente com cron

Configure somente após uma coleta manual bem-sucedida. Como seu usuário normal, execute:

```bash
crontab -e
```

Adicione **uma vez** a linha abaixo, trocando `SEU_USUARIO`. Executa diariamente às
08:15 no fuso configurado no sistema, sem saída de sucesso; erros ficam em log privado:

```cron
15 8 * * * umask 077; "$HOME/instafollowcheck/.venv/bin/python" -m instafollowcheck --usuario SEU_USUARIO --quiet >> "$HOME/.local/share/instafollowcheck/cron.log" 2>&1
```

A coleta manual anterior cria o diretório do log. Para silêncio total, substitua
o redirecionamento por `>/dev/null 2>&1` (você perderá os avisos de sessão expirada).
Confira `crontab -l`, `timedatectl` e `systemctl is-active cron`; se necessário,
inicie o serviço com `sudo systemctl enable --now cron`. A máquina precisa estar
ligada e conectada; cron não recupera execuções perdidas durante desligamentos.
Uma trava local impede sobreposição no mesmo diretório de dados. Consulte o log
periodicamente; ele registra apenas erros e não tem rotação automática.

## Desenvolvimento e verificação

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

Sem dependências instaladas, os testes também funcionam com
`PYTHONPATH=src python3 -m unittest discover -s tests -v`. Usam API simulada, sem
credenciais ou acesso à rede; não comprovam disponibilidade da coleta real.
Use quatro espaços, funções em `snake_case` e classes em `PascalCase`.
Configure Ruff conforme `pyproject.toml`; não há meta numérica de cobertura.
Não versione banco, cookies, logs ou relatórios reais. Mantenha `AGENTS.md` como
guia original e este README como referência dos comandos e da estrutura atual.

## Referências

- [Sessões, login interativo e importação do navegador](https://instaloader.github.io/cli-options.html#login-download-private-profiles)
- [API Instaloader: carregar sessão e testar autenticação](https://instaloader.github.io/module/instaloader.html)
- [Exceções e interrupção por restrições](https://instaloader.github.io/module/exceptions.html)
