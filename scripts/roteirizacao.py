"""
Roteirizacao.py — Otimizador de rotas para entregas em Uberlândia-MG

Uso:
    python scripts/roteirizacao.py
    python scripts/roteirizacao.py '["Endereço 1", "Endereço 2", ...]'

O primeiro endereço é sempre o ponto de partida (depósito/loja).
"""

import sys
import io
import time
import json
from urllib.parse import quote

# Força UTF-8 no stdout para compatibilidade com Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
from geopy.geocoders import Nominatim, Photon

# ─── CONFIGURE SEUS ENDEREÇOS AQUI ────────────────────────────────────────────
ENDERECOS = [
    "Av. Rondon Pacheco, 2345",       # ponto de partida (depósito/loja)
    "Rua Santos Dumont, 100",
    "Rua Olegário Maciel, 450",
    "Av. João Naves de Ávila, 1500",
    "Rua Espírito Santo, 720",
]
# ──────────────────────────────────────────────────────────────────────────────

SUFIXO_CIDADE = ", Uberlândia, MG, Brasil"


def _adicionar_sufixo(endereco: str) -> str:
    upper = endereco.upper()
    if "MG" in upper or "MINAS" in upper:
        return endereco.strip()
    if "UBERLANDIA" in upper or "UBERLÂNDIA" in upper:
        return endereco.strip() + ", MG, Brasil"
    return endereco.strip() + SUFIXO_CIDADE


def _tentar_geocode(geolocator, candidatos: list[str]) -> tuple[object, str] | tuple[None, None]:
    """Tenta geocodificar uma lista de variações do endereço, retorna (loc, variacao_usada)."""
    for candidato in candidatos:
        try:
            loc = geolocator.geocode(candidato, timeout=10)
            if loc:
                return loc, candidato
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    return None, None


def _via_cep(cep: str) -> dict | None:
    """Consulta ViaCEP e retorna dict com logradouro, localidade, uf. None se falhar."""
    cep_limpo = "".join(c for c in cep if c.isdigit())
    if len(cep_limpo) != 8:
        return None
    try:
        resp = requests.get(f"https://viacep.com.br/ws/{cep_limpo}/json/", timeout=10)
        data = resp.json()
        return None if data.get("erro") else data
    except Exception:
        return None


def _candidatos_de_cep(dados_cep: dict, numero: str = "") -> list[str]:
    """Gera candidatos de geocodificação a partir dos dados do ViaCEP."""
    logradouro = dados_cep.get("logradouro", "")
    localidade = dados_cep.get("localidade", "Uberlândia")
    uf         = dados_cep.get("uf", "MG")
    sufixo     = f", {localidade}, {uf}, Brasil"
    candidatos = []
    if logradouro:
        if numero:
            candidatos.append(f"{logradouro}, {numero}{sufixo}")
        candidatos.append(f"{logradouro}{sufixo}")
    # Último recurso: bairro + cidade
    bairro = dados_cep.get("bairro", "")
    if bairro:
        candidatos.append(f"{bairro}{sufixo}")
    candidatos.append(f"{localidade}, {uf}, Brasil")
    return candidatos


def geocodificar(entradas: list[str | dict]) -> list[dict]:
    """
    Geocodifica com cadeia de fallbacks:
      1. Nominatim — variações simplificadas do endereço
      2. Photon    — mesmo conjunto de candidatos (cobertura OSM diferente)
      3. CEP       — ViaCEP devolve nome oficial da rua → Nominatim → Photon
      4. Bairro/cidade — garante que o ponto entra na rota mesmo sem rua exata
    Aceita str ou dict {"endereco": str, "cep": str}.
    """
    nominatim = Nominatim(user_agent="mercos_rota_uberlandia")
    photon    = Photon(user_agent="mercos_rota_uberlandia_photon")
    resultado = []

    print("\nGeocodificando endereços...")
    for i, entrada in enumerate(entradas):
        if isinstance(entrada, dict):
            end = entrada.get("endereco", "")
            cep = entrada.get("cep", "")
        else:
            end = entrada
            cep = ""

        end_completo = _adicionar_sufixo(end)
        partes = [p.strip() for p in end.split(",")]

        # Candidatos progressivamente simplificados a partir do texto
        candidatos_texto = [end_completo]
        if len(partes) >= 3:
            candidatos_texto.append(_adicionar_sufixo(", ".join(partes[:2])))
        if len(partes) >= 2:
            candidatos_texto.append(_adicionar_sufixo(partes[0]))

        loc = link_end = usado = None

        # 1. Nominatim
        loc, usado = _tentar_geocode(nominatim, candidatos_texto)
        if loc:
            link_end = usado

        # 2. Photon (se Nominatim falhou)
        if not loc:
            loc, usado = _tentar_geocode(photon, candidatos_texto)
            if loc:
                link_end = usado
                usado = f"{usado} [Photon]"

        # 3. Fallback por CEP
        if not loc and cep:
            dados_cep = _via_cep(cep)
            if dados_cep:
                numero = partes[1].strip() if len(partes) >= 2 and partes[1].strip().isdigit() else ""
                candidatos_cep = _candidatos_de_cep(dados_cep, numero)

                loc, link_end = _tentar_geocode(nominatim, candidatos_cep)
                if loc:
                    usado = f"{link_end} [via CEP]"
                else:
                    loc, link_end = _tentar_geocode(photon, candidatos_cep)
                    if loc:
                        usado = f"{link_end} [via CEP+Photon]"

        # 4. Fallback final: só bairro + cidade (entra na rota com baixa precisão)
        if not loc:
            bairro_cidade = None
            if len(partes) >= 3:
                # "Rua X, 100, Bairro, Cidade" → tenta "Bairro, Cidade, MG, Brasil"
                bairro_cidade = _adicionar_sufixo(", ".join(partes[2:]))
            elif len(partes) >= 2 and not partes[1].strip().isdigit():
                bairro_cidade = _adicionar_sufixo(partes[1])

            if bairro_cidade:
                loc, link_end = _tentar_geocode(nominatim, [bairro_cidade])
                if not loc:
                    loc, link_end = _tentar_geocode(photon, [bairro_cidade])
                if loc:
                    usado = f"{link_end} [aproximado: bairro]"

        if loc:
            aviso = f" → {usado}" if usado and usado != end_completo else ""
            print(f"  OK [{i+1}] {end}{aviso}")
            resultado.append({"original": end, "completo": link_end, "lat": loc.latitude, "lon": loc.longitude})
        else:
            print(f"  FALHOU [{i+1}] {end} — ignorado")
            resultado.append(None)

        if i < len(entradas) - 1:
            time.sleep(1)

    return resultado


def obter_matriz_osrm(coords: list[tuple]) -> list[list[float]]:
    pontos = ";".join(f"{lon},{lat}" for lat, lon in coords)
    url = f"http://router.project-osrm.org/table/v1/driving/{pontos}?annotations=duration"

    print("\nConsultando OSRM para matriz de distâncias...")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM retornou erro: {data.get('code')} — {data.get('message')}")

    return data["durations"]


def resolver_tsp(matriz: list[list[float]]) -> tuple[list[int], float]:
    import numpy as np

    n = len(matriz)
    dist = np.array(matriz)

    if n <= 2:
        return list(range(n)), float(dist[0][1] + dist[1][0]) if n == 2 else 0.0

    if n <= 11:
        from python_tsp.exact import solve_tsp_dynamic_programming
        print(f"  Usando DP exato ({n} pontos)...")
        permutacao, distancia = solve_tsp_dynamic_programming(dist)
    else:
        from python_tsp.heuristics import solve_tsp_simulated_annealing
        print(f"  Usando Simulated Annealing ({n} pontos)...")
        permutacao, distancia = solve_tsp_simulated_annealing(dist)

    # Rotacionar para que o depósito (índice 0) seja sempre o primeiro
    idx_zero = list(permutacao).index(0)
    permutacao = list(permutacao[idx_zero:]) + list(permutacao[:idx_zero])

    return permutacao, distancia


def gerar_link_google_maps(pontos_validos: list[dict], ordem: list[int]) -> str:
    enderecos_ordenados = [pontos_validos[i]["completo"] for i in ordem]
    # Fechar o ciclo voltando ao ponto de partida
    enderecos_ordenados.append(pontos_validos[ordem[0]]["completo"])

    partes = [quote(e) for e in enderecos_ordenados]
    return "https://www.google.com/maps/dir/" + "/".join(partes)


def main():
    # Lê endereços da linha de comando (JSON) ou usa a lista padrão
    if len(sys.argv) > 1:
        try:
            enderecos = json.loads(sys.argv[1])
            if not isinstance(enderecos, list) or len(enderecos) < 2:
                print("ERRO: Passe um JSON com ao menos 2 endereços.")
                sys.exit(1)
        except json.JSONDecodeError:
            print("ERRO: Argumento inválido. Esperado JSON com lista de strings.")
            sys.exit(1)
    else:
        enderecos = ENDERECOS

    if len(enderecos) < 2:
        print("ERRO: Precisa de ao menos 2 endereços (ponto de partida + 1 destino).")
        sys.exit(1)

    # Geocodificar
    geocodificados = geocodificar(enderecos)

    # Filtrar inválidos, preservando posição do depósito
    pontos_validos = []
    indices_originais = []
    for i, p in enumerate(geocodificados):
        if p is not None:
            pontos_validos.append(p)
            indices_originais.append(i)

    if len(pontos_validos) < 2:
        print("\nERRO: Não foi possível geocodificar endereços suficientes.")
        sys.exit(1)

    if geocodificados[0] is None:
        print("\nERRO: Ponto de partida não pôde ser geocodificado.")
        sys.exit(1)

    coords = [(p["lat"], p["lon"]) for p in pontos_validos]

    # Matriz OSRM
    try:
        matriz = obter_matriz_osrm(coords)
    except Exception as e:
        print(f"\nERRO ao consultar OSRM: {e}")
        sys.exit(1)

    # Resolver TSP
    print("\nCalculando rota ótima...")
    try:
        ordem, distancia_total = resolver_tsp(matriz)
    except Exception as e:
        print(f"\nERRO no cálculo TSP: {e}")
        sys.exit(1)

    # Exibir resultado
    distancia_min = int(distancia_total // 60)
    print("\n" + "=" * 50)
    print("ROTA OTIMIZADA")
    print("=" * 50)
    for seq, idx in enumerate(ordem, start=1):
        label = " (ponto de partida)" if idx == 0 else ""
        print(f"  {seq}. {pontos_validos[idx]['original']}{label}")
    print(f"  {len(ordem) + 1}. {pontos_validos[ordem[0]]['original']} (retorno)")
    print(f"\nTempo estimado total: ~{distancia_min} minutos")
    print("=" * 50)

    link = gerar_link_google_maps(pontos_validos, ordem)
    print(f"\nLINK GOOGLE MAPS:\n{link}\n")


if __name__ == "__main__":
    main()
