const path = require('node:path');
const fs = require('node:fs');

const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');

const { BattleGame } = require('./gameState');

const PROTO_PATH = path.resolve(__dirname, '../../proto/battle.proto');
const HOST = process.env.RPG_SERVER_HOST || '0.0.0.0';
const PORT = process.env.RPG_SERVER_PORT || '50051';
const ADDRESS = `${HOST}:${PORT}`;
const STORAGE_DIR = path.resolve(__dirname, '../storage');
const MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024;
const FILE_CHUNK_SIZE = 64 * 1024;
const INVALID_FILE_NAME_PATTERN = /[<>:"|?*\x00-\x1F]/;
const RESERVED_WINDOWS_FILE_NAMES = new Set([
  'CON',
  'PRN',
  'AUX',
  'NUL',
  'COM1',
  'COM2',
  'COM3',
  'COM4',
  'COM5',
  'COM6',
  'COM7',
  'COM8',
  'COM9',
  'LPT1',
  'LPT2',
  'LPT3',
  'LPT4',
  'LPT5',
  'LPT6',
  'LPT7',
  'LPT8',
  'LPT9',
]);

const packageDefinition = protoLoader.loadSync(PROTO_PATH, {
  keepCase: true,
  longs: String,
  enums: String,
  defaults: true,
  oneofs: true,
});

const protoDescriptor = grpc.loadPackageDefinition(packageDefinition);
const battlePackage = protoDescriptor.rpg.battle;
const game = new BattleGame();

function ensureStorageDir() {
  fs.mkdirSync(STORAGE_DIR, { recursive: true });
}

function safeFileName(fileName) {
  const value = String(fileName || '').trim();
  const baseName = value.split('.', 1)[0].toUpperCase();

  if (!value || value === '.' || value === '..' || value.length > 255) {
    return null;
  }

  if (value.includes('/') || value.includes('\\') || path.basename(value) !== value) {
    return null;
  }

  if (INVALID_FILE_NAME_PATTERN.test(value) || RESERVED_WINDOWS_FILE_NAMES.has(baseName)) {
    return null;
  }

  return value;
}

function handleRpc(fn) {
  return (call, callback) => {
    try {
      callback(null, fn(call.request));
    } catch (error) {
      callback({
        code: grpc.status.INTERNAL,
        message: error.message || 'Erro interno no servidor da batalha.',
      });
    }
  };
}

function grpcStreamError(code, message) {
  const error = new Error(message);
  error.code = code;
  error.details = message;
  return error;
}

function createServer() {
  const server = new grpc.Server();

  server.addService(battlePackage.BattleService.service, {
    getStatus: handleRpc(() => game.status()),
    attack: handleRpc((request) => game.attack(request.actor_name)),
    usePotion: handleRpc((request) => game.usePotion(request.actor_name)),
    resetBattle: handleRpc(() => game.resetBattle()),
  });

  server.addService(battlePackage.FileService.service, {
    uploadFile,
    listFiles: handleRpc(() => listStoredFiles()),
    downloadFile,
  });

  return server;
}

function uploadFile(call, callback) {
  let fileName = null;
  let totalBytes = 0;
  const chunks = [];
  let responded = false;

  function fail(code, message) {
    if (!responded) {
      responded = true;
      callback({ code, message });
    }
  }

  call.on('data', (chunk) => {
    if (responded) {
      return;
    }

    const incomingName = safeFileName(chunk.file_name || fileName);
    if (!incomingName) {
      fail(grpc.status.INVALID_ARGUMENT, 'Nome de arquivo invalido.');
      return;
    }

    if (!fileName) {
      fileName = incomingName;
    }

    if (incomingName !== fileName) {
      fail(grpc.status.INVALID_ARGUMENT, 'Todos os chunks precisam usar o mesmo nome de arquivo.');
      return;
    }

    const content = Buffer.isBuffer(chunk.content)
      ? chunk.content
      : Buffer.from(chunk.content || []);

    totalBytes += content.length;
    if (totalBytes > MAX_FILE_SIZE_BYTES) {
      fail(grpc.status.INVALID_ARGUMENT, 'Arquivo excede o limite de 5 MB para a demonstracao.');
      return;
    }

    chunks.push(content);
  });

  call.on('end', () => {
    if (responded) {
      return;
    }

    if (!fileName) {
      fail(grpc.status.INVALID_ARGUMENT, 'Envie ao menos um chunk com nome de arquivo.');
      return;
    }

    try {
      ensureStorageDir();
      fs.writeFileSync(path.join(STORAGE_DIR, fileName), Buffer.concat(chunks));
      responded = true;
      callback(null, {
        success: true,
        message: `Arquivo ${fileName} recebido pelo servidor gRPC.`,
        file_name: fileName,
        size_bytes: totalBytes,
      });
    } catch (error) {
      fail(grpc.status.INTERNAL, error.message || 'Falha ao salvar arquivo no servidor.');
    }
  });

  call.on('error', (error) => {
    fail(grpc.status.INTERNAL, error.message || 'Falha durante upload gRPC.');
  });
}

function listStoredFiles() {
  ensureStorageDir();

  const files = fs.readdirSync(STORAGE_DIR, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => {
      const stats = fs.statSync(path.join(STORAGE_DIR, entry.name));
      return {
        file_name: entry.name,
        size_bytes: stats.size,
      };
    })
    .sort((left, right) => left.file_name.localeCompare(right.file_name));

  return { files };
}

function downloadFile(call) {
  const fileName = safeFileName(call.request.file_name);
  if (!fileName) {
    call.destroy(grpcStreamError(grpc.status.INVALID_ARGUMENT, 'Nome de arquivo invalido.'));
    return;
  }

  const filePath = path.join(STORAGE_DIR, fileName);
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    call.destroy(grpcStreamError(grpc.status.NOT_FOUND, `Arquivo ${fileName} nao encontrado no servidor.`));
    return;
  }

  const readStream = fs.createReadStream(filePath, { highWaterMark: FILE_CHUNK_SIZE });
  readStream.on('data', (content) => {
    call.write({ file_name: fileName, content });
  });
  readStream.on('end', () => {
    call.end();
  });
  readStream.on('error', (error) => {
    call.destroy(grpcStreamError(grpc.status.INTERNAL, error.message || 'Falha ao ler arquivo no servidor.'));
  });
}

function main() {
  const server = createServer();
  ensureStorageDir();

  server.bindAsync(
    ADDRESS,
    grpc.ServerCredentials.createInsecure(),
    (error, boundPort) => {
      if (error) {
        throw error;
      }

      console.log(`Servidor RPG gRPC rodando em ${HOST}:${boundPort}`);
      console.log('Contrato battle.proto carregado.');
      console.log(`Arquivos recebidos serao salvos em ${STORAGE_DIR}.`);
    },
  );

  process.on('SIGINT', () => {
    console.log('\nEncerrando servidor...');
    server.tryShutdown((error) => {
      if (error) {
        server.forceShutdown();
      }
      process.exit(error ? 1 : 0);
    });
  });
}

if (require.main === module) {
  main();
}

module.exports = {
  createServer,
};
