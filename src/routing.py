"""
src/routing.py — Core de roteirização TSP (importável pelo FastAPI e pelo CLI)
"""
import time
import logging
from urllib.parse import quote

import requests
from geopy.geocoders import Nominatim, Photon

logger = logging.getLogger(__name__)

SUFIXO_CIDADE = ", Uberlândia, MG, Brasil"


def _adicionar_sufixo(endereco: str) -> str:
    upper = endereco.upper()
    if "MG" in upper or "MINAS" in upper:
        return endereco.strip()
    if "UBERLANDIA" in upper or "UBERLÂNDIA" in upper:
        return endereco.strip() + ", MG, Brasil"
    return endereco.strip() + SUFIXO_CIDADE


def _tentar_geocode(geolocator, candidatos: list[str]):
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
    logradouro = dados_cep.get("logradouro", "")
    localidade = dados_cep.get("localidade", "Uberlândia")
    uf         = dados_cep.get("uf", "MG")
    sufixo     = f", {localidade}, {uf}, Brasil"
    candidatos = []
    if logradouro:
        if numero:
            candidatos.append(f"{logradouro}, {numero}{sufixo}")
        candidatos.append(f"{logradouro}{sufixo}")
    bairro = dados_cep.get("bairro", "")
    if bairro:
        candidatos.append(f"{bairro}{sufixo}")
    candidatos.append(f"{localidade}, {uf}, Brasil")
    return candidatos


def geocodificar(entradas: list) -> list[dict | None]:
    """
    Geocodifica com cadeia: Nominatim → Photon → CEP/ViaCEP → bairro.
    Cada entrada: str ou dict {"endereco": str, "cep": str, "label": str (opcional)}.
    Retorna lista com None para pontos não encontrados.
    """
    nominatim = Nominatim(user_agent="mercos_rota_uberlandia")
    photon    = Photon(user_agent="mercos_rota_uberlandia_photon")
    resultado = []

    for i, entrada in enumerate(entradas):
        if isinstance(entrada, dict):
            end   = entrada.get("endereco", "")
            cep   = entrada.get("cep", "")
            label = entrada.get("label", end)
        else:
            end = label = entrada
            cep = ""

        end_completo = _adicionar_sufixo(end)
        partes = [p.strip() for p in end.split(",")]

        candidatos_texto = [end_completo]
        if len(partes) >= 3:
            candidatos_texto.append(_adicionar_sufixo(", ".join(partes[:2])))
        if len(partes) >= 2:
            candidatos_texto.append(_adicionar_sufixo(partes[0]))

        loc = link_end = usado = None

        loc, usado = _tentar_geocode(nominatim, candidatos_texto)
        if loc:
            link_end = usado

        if not loc:
            loc, usado = _tentar_geocode(photon, candidatos_texto)
            if loc:
                link_end = usado

        if not loc and cep:
            dados_cep = _via_cep(cep)
            if dados_cep:
                numero = partes[1].strip() if len(partes) >= 2 and partes[1].strip().isdigit() else ""
                candidatos_cep = _candidatos_de_cep(dados_cep, numero)
                loc, link_end = _tentar_geocode(nominatim, candidatos_cep)
                if not loc:
                    loc, link_end = _tentar_geocode(photon, candidatos_cep)

        if not loc:
            bairro_cidade = None
            if len(partes) >= 3:
                bairro_cidade = _adicionar_sufixo(", ".join(partes[2:]))
            elif len(partes) >= 2 and not partes[1].strip().isdigit():
                bairro_cidade = _adicionar_sufixo(partes[1])
            if bairro_cidade:
                loc, link_end = _tentar_geocode(nominatim, [bairro_cidade])
                if not loc:
                    loc, link_end = _tentar_geocode(photon, [bairro_cidade])

        if loc:
            resultado.append({"label": label, "completo": link_end, "lat": loc.latitude, "lon": loc.longitude})
        else:
            logger.warning(f"[Routing] Não geocodificado: '{end}'")
            resultado.append(None)

        if i < len(entradas) - 1:
            time.sleep(1)

    return resultado


def obter_matriz_osrm(coords: list[tuple]) -> list[list[float]]:
    pontos = ";".join(f"{lon},{lat}" for lat, lon in coords)
    resp = requests.get(
        f"http://router.project-osrm.org/table/v1/driving/{pontos}?annotations=duration",
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM erro: {data.get('code')}")
    return data["durations"]


def resolver_tsp(matriz: list[list[float]]) -> tuple[list[int], float]:
    import numpy as np
    n = len(matriz)
    dist = np.array(matriz)
    if n <= 2:
        return list(range(n)), float(dist[0][1] + dist[1][0]) if n == 2 else 0.0
    if n <= 11:
        from python_tsp.exact import solve_tsp_dynamic_programming
        permutacao, distancia = solve_tsp_dynamic_programming(dist)
    else:
        from python_tsp.heuristics import solve_tsp_simulated_annealing
        permutacao, distancia = solve_tsp_simulated_annealing(dist)
    idx_zero = list(permutacao).index(0)
    permutacao = list(permutacao[idx_zero:]) + list(permutacao[:idx_zero])
    return permutacao, distancia


def gerar_link_google_maps(pontos: list[dict], ordem: list[int]) -> str:
    ends = [pontos[i]["completo"] for i in ordem]
    ends.append(pontos[ordem[0]]["completo"])  # retorno à origem
    return "https://www.google.com/maps/dir/" + "/".join(quote(e) for e in ends)


def otimizar_rota(entradas: list, origem: str) -> dict:
    """
    Pipeline completo: geocodifica → OSRM → TSP → link Google Maps.
    'origem' é o ponto de partida (depósito), inserido na posição 0.
    Retorna dict com link, paradas ordenadas, tempo_min e falhas.
    """
    todas = [{"endereco": origem, "label": "Depósito"}] + list(entradas)

    geocodificados = geocodificar(todas)

    # Separa origem dos destinos
    origem_geo = geocodificados[0]
    if origem_geo is None:
        raise ValueError("Não foi possível geocodificar o endereço de origem.")

    pontos_validos = [p for p in geocodificados if p is not None]
    falhas = [todas[i] for i, p in enumerate(geocodificados) if p is None and i > 0]

    if len(pontos_validos) < 2:
        raise ValueError("Endereços insuficientes para calcular rota.")

    coords  = [(p["lat"], p["lon"]) for p in pontos_validos]
    matriz  = obter_matriz_osrm(coords)
    ordem, duracao = resolver_tsp(matriz)

    paradas = []
    for seq, idx in enumerate(ordem):
        paradas.append({
            "seq": seq + 1,
            "label": pontos_validos[idx]["label"],
            "lat": pontos_validos[idx]["lat"],
            "lon": pontos_validos[idx]["lon"],
        })

    link = gerar_link_google_maps(pontos_validos, ordem)

    return {
        "link": link,
        "paradas": paradas,
        "tempo_min": int(duracao // 60),
        "falhas": [f.get("label", f.get("endereco", "?")) if isinstance(f, dict) else f for f in falhas],
    }
