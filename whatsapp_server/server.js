const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const express = require('express');

const app = express();
app.use(express.json());

const PORT = 3000;
let status = 'desconectado';

const client = new Client({
    authStrategy: new LocalAuth({ dataPath: './auth_info' }),
    puppeteer: { headless: true, args: ['--no-sandbox'] }
});

client.on('qr', (qr) => {
    console.log('\n📱 Escaneie o QR Code abaixo com seu WhatsApp:\n');
    qrcode.generate(qr, { small: true });
    status = 'aguardando_qr';
});

client.on('ready', () => {
    console.log('[WA] ✅ Conectado ao WhatsApp!');
    status = 'conectado';
});

client.on('disconnected', (reason) => {
    console.log(`[WA] Desconectado: ${reason}`);
    status = 'desconectado';
    client.initialize();
});

client.initialize();

// Endpoints
app.get('/status', (req, res) => {
    res.json({ status });
});

app.post('/send', async (req, res) => {
    const { numero, mensagem } = req.body;

    if (!numero || !mensagem) {
        return res.status(400).json({ erro: 'numero e mensagem são obrigatórios' });
    }

    if (status !== 'conectado') {
        return res.status(503).json({ erro: `WhatsApp não conectado. Status: ${status}` });
    }

    try {
        let fone = numero.replace(/\D/g, '');
        if (!fone.startsWith('55')) fone = '55' + fone;

        // Tenta com o número como veio
        let numeroId = await client.getNumberId(fone);

        // Se não achou e tem 13 dígitos (com 9 extra), tenta sem o 9
        if (!numeroId && fone.length === 13) {
            const semNove = fone.slice(0, 4) + fone.slice(5);
            numeroId = await client.getNumberId(semNove);
            if (numeroId) fone = semNove;
        }

        // Se não achou e tem 12 dígitos (sem 9 extra), tenta com o 9
        if (!numeroId && fone.length === 12) {
            const comNove = fone.slice(0, 4) + '9' + fone.slice(4);
            numeroId = await client.getNumberId(comNove);
            if (numeroId) fone = comNove;
        }

        if (!numeroId) {
            console.warn(`[WA] ⚠️ Número ${fone} não encontrado no WhatsApp — mensagem não enviada.`);
            return res.status(404).json({ erro: `Número ${fone} não encontrado no WhatsApp.` });
        }

        await client.sendMessage(numeroId._serialized, mensagem);
        console.log(`[WA] ✅ Mensagem enviada para ${fone}`);
        res.json({ ok: true, para: fone });
    } catch (e) {
        console.error(`[WA] ❌ Erro ao enviar: ${e.message}`);
        res.status(500).json({ erro: e.message });
    }
});

app.listen(PORT, () => {
    console.log(`[WA] Servidor rodando na porta ${PORT}`);
    console.log(`[WA] Status: http://localhost:${PORT}/status`);
});
