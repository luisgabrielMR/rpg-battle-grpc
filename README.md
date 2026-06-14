# Sistema de Batalha RPG Distribuido

Projeto academico de Programacao Distribuida usando gRPC para comunicacao
Backend-Backend. O tema escolhido e uma batalha RPG em que um backend Node.js
mantem o estado do jogo e um backend consumidor Python chama os metodos remotos.

O mesmo contrato gRPC tambem possui um servico de arquivos para demonstrar
upload, listagem e download via streaming.

## Estrutura

```text
.
|-- proto/
|   `-- battle.proto
|-- server-node/
|   |-- package.json
|   `-- src/
|       |-- gameState.js
|       `-- server.js
|-- client-python/
|   |-- requirements.txt
|   |-- generated-requests/
|   |-- sample-files/
|   |   `-- ficha_heroi.txt
|   `-- src/rpg_client/
|       |-- client.py
|       `-- generated/
`-- scripts/
    |-- setup.ps1
    |-- run_server.ps1
    `-- run_client.ps1
```

## Servicos gRPC

O arquivo `proto/battle.proto` define dois servicos:

- `BattleService`: `GetStatus`, `Attack`, `UsePotion` e `ResetBattle`.
- `FileService`: `UploadFile`, `ListFiles` e `DownloadFile`.

`UploadFile` recebe um stream de chunks do cliente para o servidor.
`DownloadFile` devolve um stream de chunks do servidor para o consumidor.

## Como rodar no Windows

No PowerShell, dentro da pasta do projeto:

```powershell
.\scripts\setup.ps1
```

Se o Windows bloquear scripts PowerShell por Execution Policy, use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Abra um terminal para o servidor gRPC:

```powershell
.\scripts\run_server.ps1
```

Alternativa com bypass:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_server.ps1
```

Abra outro terminal para o backend consumidor Python:

```powershell
.\scripts\run_client.ps1
```

## Demo automatica

Com o servidor rodando, execute:

```powershell
.\scripts\run_client.ps1 --demo
```

Alternativa com bypass:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_client.ps1 --demo
```

Esse comando faz:

1. Reinicia a batalha.
2. Executa um ataque via `BattleService`.
3. Envia `client-python/sample-files/ficha_heroi.txt` via `FileService.UploadFile`.
4. Lista os arquivos salvos no servidor via `FileService.ListFiles`.
5. Baixa o arquivo de volta para `client-python/downloads/` via `FileService.DownloadFile`.
6. Gera `client-python/generated-requests/acao_batalha.txt`.
7. Envia esse arquivo de acao para o servidor.
8. O servidor executa a acao no backend e gera `resultado_acao_batalha.txt`.
9. O cliente baixa o resultado processado para `client-python/downloads/`.

## Como funciona a transferencia de arquivos

A transferencia foi implementada no servico gRPC `FileService`, definido em
`proto/battle.proto`. O cliente Python e o backend consumidor; ele abre o
arquivo local, divide o conteudo em blocos de 64 KB e envia esses blocos pelo
RPC `UploadFile(stream FileChunk)`. Cada `FileChunk` carrega o nome do arquivo
e os bytes daquele pedaco.

O servidor Node.js recebe o stream, valida se o nome do arquivo e seguro,
limita o tamanho da demonstracao a 5 MB e salva o resultado em
`server-node/storage/`. Para consultar o que esta salvo, o cliente chama
`ListFiles`, que retorna nome e tamanho de cada arquivo. Para baixar, o cliente
chama `DownloadFile(FileRequest)`; o servidor le o arquivo salvo em blocos e
devolve outro stream de `FileChunk`, que o Python grava em
`client-python/downloads/`.

Esse fluxo atende ao foco Backend-Backend do PDF: a troca acontece entre dois
processos de backend, em linguagens diferentes, usando o contrato `.proto`
compartilhado.

## Demo com arquivo de acao processado no servidor

Para deixar explicito que o cliente apenas envia uma requisicao e que o
calculo acontece no servidor, existe o comando:

```powershell
.\scripts\run_client.ps1 --action-file-demo
```

Alternativa com bypass:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_client.ps1 --action-file-demo
```

Esse comando gera o arquivo `client-python/generated-requests/acao_batalha.txt`
com conteudo parecido com:

```text
tipo=requisicao_batalha
acao=attack
ator=Guerreiro
```

O Python envia esse arquivo por `UploadFile`. O servidor Node.js reconhece a
linha `acao=attack`, executa o calculo no `gameState.js` e salva um novo
arquivo no servidor:

```text
server-node/storage/resultado_acao_batalha.txt
```

Depois o Python baixa esse resultado para:

```text
client-python/downloads/resultado_acao_batalha.txt
```

Tambem e possivel trocar a acao:

```powershell
.\scripts\run_client.ps1 --action-file-demo --action use_potion
.\scripts\run_client.ps1 --action-file-demo --action status
.\scripts\run_client.ps1 --action-file-demo --action reset
```

Para demonstrar somente a transferencia de arquivos:

```powershell
.\scripts\run_client.ps1 --file-demo
```

Comandos individuais:

```powershell
.\scripts\run_client.ps1 --upload client-python\sample-files\ficha_heroi.txt
.\scripts\run_client.ps1 --list-files
.\scripts\run_client.ps1 --download ficha_heroi.txt
```

## Comandos manuais equivalentes

Servidor:

```powershell
cd server-node
npm install
npm start
```

Cliente/consumidor:

```powershell
cd ..
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r client-python\requirements.txt
.\.venv\Scripts\python.exe client-python\src\rpg_client\client.py --demo
```

## O que demonstra Programacao Distribuida

- O contrato `.proto` e compartilhado entre dois backends em linguagens
  diferentes.
- O servidor Node.js registra os servicos gRPC e fica escutando na porta
  `50051`.
- O consumidor Python roda em outro processo, cria stubs gRPC e chama os
  metodos remotos.
- A transferencia de arquivos acontece por mensagens gRPC com `bytes`, usando
  streaming para dividir o arquivo em chunks.
- Erros basicos sao tratados: servidor indisponivel, arquivo local inexistente,
  nome de arquivo invalido e arquivo remoto inexistente.
