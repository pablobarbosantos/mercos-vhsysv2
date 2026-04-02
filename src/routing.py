import requests
import time
import re
import numpy as np
import networkx as nx
from geopy.geocoders import Nominatim, Photon

# --- CONFIGURAÇÕES ---
USER_AGENT = "mercos_udi_v4"
UBERLANDIA_BBOX = [-19.15, -48.65, -18.55, -47.8]

def normalizar_endereco(end):
    if isinstance(end, dict):
        end = end.get("endereco", "")
    # Limpeza básica manual para evitar depender de libs de URL
    substituicoes = {r"\bR\b": "Rua", r"\bAv\b": "Avenida", r"\bMG\b": "Minas Gerais"}
    for sigla, extensao in substituicoes.items():
        end = re.sub(sigla, extensao, end, flags=re.IGNORECASE)
    return end

def geocodificar(enderecos):
    geolocator = Nominatim(user_agent=USER_AGENT, timeout=10)
    photon = Photon(user_agent=USER_AGENT, timeout=10)
    resultados = []
    for item in enderecos:
        end_original = item if isinstance(item, str) else item.get("endereco", "")
        end_limpo = normalizar_endereco(end_original)
        if "Uberlandia" not in end_limpo: end_limpo += ", Uberlandia, MG"
        
        loc = None
        try:
            loc = geolocator.geocode(end_limpo)
            if not loc:
                loc = photon.geocode(end_limpo)
        except:
            try:
                loc = photon.geocode(end_limpo)
            except:
                loc = None

        if loc:
            resultados.append({"lat": loc.latitude, "lon": loc.longitude, "label": end_original})
        else:
            resultados.append(None)
        time.sleep(0.5)
    return resultados

def obter_matriz_osrm(coords):
    coords_str = ";".join([f"{lon},{lat}" for lat, lon in coords])
    url = f"https://router.project-osrm.org/table/v1/driving/{coords_str}?annotations=duration"
    resp = requests.get(url, timeout=15)
    if resp.status_code != 200:
        raise Exception("Erro OSRM")
    return resp.json()["durations"]

def resolver_tsp(matriz):
    dist_matrix = np.array(matriz)
    G = nx.from_numpy_array(dist_matrix, create_using=nx.DiGraph)
    from networkx.algorithms.approximation import traveling_salesman_problem
    caminho = traveling_salesman_problem(G.to_undirected(), cycle=True)
    ordem = caminho[:-1]
    duracao = sum(matriz[ordem[i]][ordem[i+1]] for i in range(len(ordem)-1))
    duracao += matriz[ordem[-1]][ordem[0]]
    return ordem, duracao

def gerar_link_google_maps(pontos, ordem):
    """Gera link usando coordenadas (mais seguro que nomes/quote)"""
    if not pontos or not ordem: return ""
    base = "https://www.google.com/maps/dir/"
    # Criamos a sequência de coordenadas lat,lon/lat,lon...
    pts = [f"{pontos[i]['lat']},{pontos[i]['lon']}" for i in ordem]
    # Adicionamos a volta ao início
    pts.append(f"{pontos[ordem[0]]['lat']},{pontos[ordem[0]]['lon']}")
    return base + "/".join(pts)

# --- FUNÇÃO CHAMADA PELO ADMIN_ROUTES ---
def otimizar_rota(enderecos, *args, **kwargs):
    try:
        # Se vier 'origem' como argumento separado (comum no seu admin_routes)
        origem = kwargs.get('origem') or (args[0] if args and isinstance(args[0], str) else None)
        
        lista_preparada = []
        if origem:
            lista_preparada.append(origem)
            
        if isinstance(enderecos, list):
            lista_preparada.extend(enderecos)
        else:
            lista_preparada.append(enderecos)

        # 1. GPS
        pontos_gps = geocodificar(lista_preparada)
        validos = [p for p in pontos_gps if p is not None]
        
        if len(validos) < 2:
            return {"status": "erro", "erro": "Endereços não encontrados"}
        
        # 2. Matriz e Rota
        coords = [(p["lat"], p["lon"]) for p in validos]
        matriz = obter_matriz_osrm(coords)
        ordem, duracao = resolver_tsp(matriz)
        
        # 3. Resposta
        return {
            "status": "sucesso",
            "ordem": [int(i) for i in ordem],
            "link_maps": gerar_link_google_maps(validos, ordem),
            "duracao_segundos": float(duracao),
            "pontos": validos
        }
    except Exception as e:
        return {"status": "erro", "erro": str(e)}