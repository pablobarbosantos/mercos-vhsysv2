"""
roteirizacao.py — CLI de roteirização TSP para entregas em Uberlândia-MG

Uso:
    python scripts/roteirizacao.py
    python scripts/roteirizacao.py '["Endereço 1", "Endereço 2", ...]'
    python scripts/roteirizacao.py '[{"endereco":"Rua X, 100","cep":"38400000"}, ...]'

O primeiro endereço é sempre o ponto de partida (depósito/loja).
"""
import sys
import io
import json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
from src.routing import geocodificar, obter_matriz_osrm, resolver_tsp, gerar_link_google_maps

ENDERECOS = [
    "Av. Rondon Pacheco, 2345",
    "Rua Santos Dumont, 100",
    "Rua Olegário Maciel, 450",
    "Av. João Naves de Ávila, 1500",
    "Rua Espírito Santo, 720",
]


def main():
    if len(sys.argv) > 1:
        try:
            enderecos = json.loads(sys.argv[1])
            if not isinstance(enderecos, list) or len(enderecos) < 2:
                print("ERRO: Passe um JSON com ao menos 2 endereços.")
                sys.exit(1)
        except json.JSONDecodeError:
            print("ERRO: Argumento inválido. Esperado JSON com lista de strings ou dicts.")
            sys.exit(1)
    else:
        enderecos = ENDERECOS

    print("\nGeocodificando endereços...")
    geocodificados = geocodificar(enderecos)

    pontos_validos = [p for p in geocodificados if p is not None]
    falhas = [enderecos[i] for i, p in enumerate(geocodificados) if p is None]

    if geocodificados[0] is None:
        print("\nERRO: Ponto de partida não pôde ser geocodificado.")
        sys.exit(1)
    if len(pontos_validos) < 2:
        print("\nERRO: Endereços insuficientes para calcular rota.")
        sys.exit(1)

    for i, (entrada, geo) in enumerate(zip(enderecos, geocodificados)):
        label = entrada if isinstance(entrada, str) else entrada.get("endereco", "")
        if geo:
            print(f"  OK [{i+1}] {label}")
        else:
            print(f"  FALHOU [{i+1}] {label}")

    coords = [(p["lat"], p["lon"]) for p in pontos_validos]

    print("\nConsultando OSRM...")
    try:
        matriz = obter_matriz_osrm(coords)
    except Exception as e:
        print(f"ERRO OSRM: {e}")
        sys.exit(1)

    print("Calculando rota ótima (TSP)...")
    ordem, duracao = resolver_tsp(matriz)

    print("\n" + "=" * 50)
    print("ROTA OTIMIZADA")
    print("=" * 50)
    for seq, idx in enumerate(ordem, 1):
        label = pontos_validos[idx].get("label") or pontos_validos[idx].get("original", "")
        print(f"  {seq}. {label}" + (" (partida)" if idx == 0 else ""))
    print(f"  {len(ordem)+1}. {pontos_validos[ordem[0]].get('label','origem')} (retorno)")
    print(f"\nTempo estimado: ~{int(duracao//60)} minutos")
    print("=" * 50)

    link = gerar_link_google_maps(pontos_validos, ordem)
    print(f"\nLINK GOOGLE MAPS:\n{link}\n")

    if falhas:
        print(f"Não geocodificados ({len(falhas)}): {falhas}")


if __name__ == "__main__":
    main()
