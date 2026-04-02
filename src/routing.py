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

# Bounding box de Uberlândia-MG
# Usado como bias (viewbox sem bounded) + validação de coordenada pós-retorno
_LAT_MIN, _LAT_MAX = -19.15, -18.55
_LON_MIN, _LON_MAX = -48.65, -47.80
# Nominatim viewbox: [(lon_NW, lat_NW), (lon_SE, lat_SE)]
_UDI_VIEWBOX = [(_LON_MIN, _LAT_MAX), (_LON_MAX, _LAT_MIN)]
# Photon bbox: [(lat_NW, lon_NW), (lat_SE, lon_SE)]
_UDI_BBOX    = [(_LAT_MAX, _LON_MIN), (_LAT_MIN, _LON_MAX)]

def _em_uberlandia(loc) -> bool:
    if loc is None:
        return False
    return _LAT_MIN <= loc.latitude <= _LAT_MAX and _LON_MIN <= loc.longitude <= _LON_MAX


def _adicionar_sufixo(endereco: str) -> str:
    upper = endereco.upper()
    if "MG" in upper or "MINAS" in upper:
        return endereco.strip()
    if "UBERLANDIA" in upper or "UBERLÂNDIA" in upper:
        return endereco.strip() + ", MG, Brasil"
    return endereco.strip() + SUFIXO_CIDADE


def _tentar_geocode(nominatim: Nominatim, photon: Photon, candidatos: list[str]):
    """
    Para cada candidato: Nominatim primeiro (rate-limit respeitado), depois Photon.
    Valida que o resultado está dentro da área de Uberlândia.
    """
    for candidato in candidatos:
        # 1. Nominatim com viewbox como bias
        try:
            loc = nominatim.geocode(candidato, viewbox=_UDI_VIEWBOX, bounded=False, timeout=10)
            time.sleep(0.35)   # respeitar 1 req/s do Nominatim
            if _em_uberlandia(loc):
                return loc, candidato
        except Exception:
            time.sleep(0.35)

        # 2. Photon (sem rate limit estrito)
        try:
            loc = photon.geocode(candidato, bbox=_UDI_BBOX, timeout=10)
            if _em_uberlandia(loc):
                return loc, candidato
        except Exception:
            pass

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
    Geocodifica com cadeia de fallback, bbox restrito a Uberlândia:
      1. Nominatim + Photon em paralelo — variações simplificadas do endereço
      2. CEP via ViaCEP → nome oficial da rua → Nominatim + Photon em paralelo
      3. Bairro + cidade como último recurso
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

        loc, link_end = _tentar_geocode(nominatim, photon, candidatos_texto)

        # Fallback CEP
        if not loc and cep:
            dados_cep = _via_cep(cep)
            if dados_cep:
                numero = partes[1].strip() if len(partes) >= 2 and partes[1].strip().isdigit() else ""
                loc, link_end = _tentar_geocode(nominatim, photon, _candidatos_de_cep(dados_cep, numero))

        # Fallback bairro + cidade
        if not loc:
            bairro_cidade = None
            if len(partes) >= 3:
                bairro_cidade = _adicionar_sufixo(", ".join(partes[2:]))
            elif len(partes) >= 2 and not partes[1].strip().isdigit():
                bairro_cidade = _adicionar_sufixo(partes[1])
            if bairro_cidade:
                loc, link_end = _tentar_geocode(nominatim, photon, [bairro_cidade])

        if loc:
            resultado.append({"label": label, "completo": link_end, "lat": loc.latitude, "lon": loc.longitude})
        else:
            logger.warning(f"[Routing] Não geocodificado: '{end}'")
            resultado.append(None)

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
    ends.append(pontos[ordem[0]]["completo"])
    return "https://www.google.com/maps/dir/" + "/".join(quote(e) for e in ends)


def otimizar_rota(entradas: list, origem: str) -> dict:
    """Pipeline completo: geocodifica → OSRM → TSP → link Google Maps."""
    todas = [{"endereco": origem, "label": "Depósito"}] + list(entradas)
    geocodificados = geocodificar(todas)

    if geocodificados[0] is None:
        raise ValueError("Não foi possível geocodificar o endereço de origem.")

    pontos_validos = [p for p in geocodificados if p is not None]
    falhas = [todas[i] for i, p in enumerate(geocodificados) if p is None and i > 0]

    if len(pontos_validos) < 2:
        raise ValueError("Endereços insuficientes para calcular rota.")

    coords  = [(p["lat"], p["lon"]) for p in pontos_validos]
    matriz  = obter_matriz_osrm(coords)
    ordem, duracao = resolver_tsp(matriz)

    paradas = [
        {"seq": seq + 1, "label": pontos_validos[idx]["label"],
         "lat": pontos_validos[idx]["lat"], "lon": pontos_validos[idx]["lon"]}
        for seq, idx in enumerate(ordem)
    ]

    return {
        "link": gerar_link_google_maps(pontos_validos, ordem),
        "paradas": paradas,
        "tempo_min": int(duracao // 60),
        "falhas": [f.get("label", f.get("endereco", "?")) if isinstance(f, dict) else f for f in falhas],
    }
