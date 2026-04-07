import requests
import os
import json

# --- CONFIGURAÇÕES DO AMBIENTE ---
# Pega o caminho da pasta onde este script está (C:\mercos_vhsys_git\SICOOB)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Configurações do Aplicativo Sicoob
CLIENT_ID = "2c7510fc-8938-4297-b183-1b641bfc8bdd"
URL_TOKEN = "https://auth.sicoob.com.br/auth/realms/cooperado/protocol/openid-connect/token"

# Caminhos dos Certificados (Baseados no seu 'ls' de hoje)
CERT_PATH = os.path.join(BASE_DIR, "data", "certs", "certificado_novo.pem")
KEY_PATH = os.path.join(BASE_DIR, "data", "certs", "chave_nova.key")

def gerar_token():
    """
    Realiza a autenticação mTLS no Sicoob e retorna o Access Token.
    """
    
    # Payload para Client Credentials (Server-to-Server)
    # Importante: O scope deve estar habilitado para seu Client ID no portal do Sicoob
    payload = {
        'grant_type': 'client_credentials',
        'client_id': CLIENT_ID,
        'scope': 'boletos_inclusao boletos_consulta boletos_alteracao'  # Scopes da API Cobrança Bancária v3
    }
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    # Par de arquivos para autenticação mTLS
    cert_dupla = (CERT_PATH, KEY_PATH)

    print("--- INICIANDO AUTENTICAÇÃO SICOOB ---")
    print(f"Usando Certificado: {CERT_PATH}")
    
    # Validação simples se os arquivos existem antes de tentar a conexão
    if not os.path.exists(CERT_PATH) or not os.path.exists(KEY_PATH):
        print("❌ ERRO: Arquivos de certificado ou chave não encontrados nos caminhos acima!")
        return None

    try:
        # Faz a requisição POST com o certificado digital
        response = requests.post(
            URL_TOKEN, 
            data=payload, 
            headers=headers, 
            cert=cert_dupla, 
            timeout=20
        )
        
        # Verifica se a requisição foi bem sucedida (Status 200)
        if response.status_code == 200:
            token_data = response.json()
            print("✅ SUCESSO: Token gerado com sucesso!")
            print(f"Expira em: {token_data.get('expires_in')} segundos")
            return token_data['access_token']
        else:
            print(f"❌ ERRO SICOOB ({response.status_code}):")
            print(response.text)
            return None

    except requests.exceptions.SSLError as e:
        print(f"❌ ERRO DE SSL: Verifique se o certificado é válido para produção.\nDetalhe: {e}")
    except Exception as e:
        print(f"❌ ERRO CRÍTICO: {e}")
    
    return None

if __name__ == "__main__":
    access_token = gerar_token()
    
    if access_token:
        # Exibe apenas o início do token por segurança
        print(f"\nSeu Access Token pronto para uso:\n{access_token[:50]}...")
        
        # Opcional: Salvar o token em um arquivo temporário para outros scripts usarem
        # with open("token_atual.txt", "w") as f:
        #     f.write(access_token)