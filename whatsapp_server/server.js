const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const express = require('express');

const app = express();
app.use(express.json());

const PORT = 3000;
let status = 'desconectado';
let qrAtual = null;

const client = new Client({
    authStrategy: new LocalAuth({ dataPath: './auth_info' }),
    puppeteer: { headless: true, args: ['--no-sandbox'] }
});

client.on('qr', (qr) => {
    console.log('\n📱 Escaneie o QR Code abaixo com seu WhatsApp:\n');
    qrcode.generate(qr, { small: true });
    console.log('\n📱 Ou acesse http://localhost:3000/qr no navegador para escanear\n');
    qrAtual = qr;
    status = 'aguardando_qr';
});

client.on('ready', () => {
    console.log('[WA] ✅ Conectado ao WhatsApp!');
    status = 'conectado';
    qrAtual = null;
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

app.get('/qr', async (req, res) => {
    if (status === 'conectado') {
        return res.send(`<!DOCTYPE html><html><head><title>WhatsApp</title>
<style>body{font-family:sans-serif;text-align:center;padding:60px;background:#f0f0f0}</style>
</head><body><h1 style="color:#25D366">✅ WhatsApp Conectado!</h1>
<p>O servidor está funcionando normalmente.</p></body></html>`);
    }
    if (!qrAtual) {
        return res.send(`<!DOCTYPE html><html><head><title>WhatsApp QR</title>
<meta http-equiv="refresh" content="5">
<style>body{font-family:sans-serif;text-align:center;padding:60px;background:#f0f0f0}</style>
</head><body><h2>Aguardando QR Code...</h2>
<p>Esta página atualiza automaticamente a cada 5 segundos.</p></body></html>`);
    }
    try {
        const qrDataUrl = await QRCode.toDataURL(qrAtual, { width: 350, margin: 2 });
        res.send(`<!DOCTYPE html><html><head><title>WhatsApp QR</title>
<meta http-equiv="refresh" content="20">
<style>body{font-family:sans-serif;text-align:center;padding:40px;background:#f0f0f0}
img{border:8px solid #fff;border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,0.15)}</style>
</head><body>
<h2>📱 Escaneie com o WhatsApp</h2>
<p>Abra WhatsApp → Menu → Aparelhos conectados → Conectar um aparelho</p>
<img src="${qrDataUrl}" width="350" height="350" alt="QR Code"/>
<p style="color:#888;font-size:13px">Esta página atualiza a cada 20s. Após escanear, recarregue para confirmar.</p>
</body></html>`);
    } catch (e) {
        res.status(500).send('Erro ao gerar QR: ' + e.message);
    }
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
