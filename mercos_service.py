"""
MercosService — recebe o webhook do Mercos e aciona o VhsysService.
====================================================================

Correções aplicadas:
  1. Mapeamento correto dos campos da API Mercos V2
  2. Idempotência thread-safe com lock por pedido
  3. Cache thread-safe de produtos
  4. Passa 'numero' e 'condicao_pagamento' para o VHSYS
  5. Passa dados completos do cliente para cadastro automático
  6. Integração com fluxo operacional (auditoria de fluxo)
  7. Confirmação pro cliente DESATIVADA (somente alertas internos)
"""

import logging
import threading

from vhsys_service import VhsysService
from src import database as db
from src.whatsapp import get_whatsapp

logger = logging.getLogger(__name__)


class MercosService:
    def __init__(self):
        self.vhsys = VhsysService()
        self._produtos_carregados = False

        self._cache_lock = threading.Lock()
        self._pedido_locks: dict[int, threading.Lock] = {}
        self._pedido_locks_meta = threading.Lock()

    def _get_lock_para_pedido(self, mercos_id: int) -> threading.Lock:
        with self._pedido_locks_meta:
            if mercos_id not in self._pedido_locks:
                self._pedido_locks[mercos_id] = threading.Lock()
            return self._pedido_locks[mercos_id]

    def processar_para_vhsys(self, dados_mercos: dict):
        mercos_id = dados_mercos.get("id")
        numero    = dados_mercos.get("numero", mercos_id)

        lock_pedido = self._get_lock_para_pedido(mercos_id)
        adquiriu = lock_pedido.acquire(blocking=True, timeout=30)
        if not adquiriu:
            logger.warning(
                f"[MercosService] Pedido #{numero} (id={mercos_id}) "
                f"ainda em processamento após 30s — abortando duplicata."
            )
            return None

        try:
            if db.pedido_ja_processado(mercos_id):
                logger.info(f"[MercosService] Pedido #{numero} (id={mercos_id}) já processado anteriormente.")
                return None

            self._garantir_cache_produtos()

            pedido_vhsys = self._traduzir_pedido(dados_mercos)
            if not pedido_vhsys:
                db.salvar_pedido_processado(mercos_id, "erro", status="erro")
                db.fluxo_marcar_erro(mercos_id)
                db.registrar_erro("pedidos", str(mercos_id), "Tradução falhou: sem CNPJ ou sem itens válidos (preco/qtd = 0)")
                return None

            resposta = self.vhsys.lancar_pedido_venda(pedido_vhsys)

            if resposta and resposta.get("code") == 200:
                pedido_data   = resposta.get("data", [{}])[0]
                vhsys_id      = str(pedido_data.get("id_ped") or "desconhecido")
                vhsys_numero  = str(pedido_data.get("id_pedido") or vhsys_id)
                valor_total   = float(pedido_data.get("valor_total_nota", 0) or 0)
                db.salvar_pedido_processado(mercos_id, vhsys_id, status="ok")
                db.fluxo_marcar_processado(mercos_id)
                logger.info(f"[MercosService] OK Pedido Mercos #{numero} → VHSYS #{vhsys_numero} (id_ped={vhsys_id})")

                # Salva endereço do cliente (vem do VHSys via buscar_ou_cadastrar_cliente)
                try:
                    cidade = pedido_data.get("_cidade_cliente", "")
                    bairro = pedido_data.get("_bairro_cliente", "")
                    if cidade or bairro:
                        db.fluxo_registrar_recebido(
                            mercos_id=mercos_id,
                            numero=str(numero or mercos_id),
                            cliente=dados_mercos.get("cliente_razao_social", ""),
                            valor=valor_total,
                            cidade=cidade,
                            bairro=bairro,
                        )
                except Exception as e:
                    logger.warning(f"[MercosService] Falha ao salvar endereço (não crítico): {e}")

                # Busca itens do VHSys (fonte autoritativa — webhook Mercos não inclui itens)
                try:
                    itens_vhsys = self.vhsys.buscar_itens_pedido(vhsys_id)
                    itens_para_salvar = [
                        {
                            "sku":          str(it.get("id_produto") or "").strip(),
                            "nome_produto": it.get("desc_produto") or "",
                            "quantidade":   float(it.get("qtde_produto") or 0),
                            "valor_unit":   float(it.get("valor_unit_produto") or 0),
                            "valor_total":  float(it.get("qtde_produto") or 0) * float(it.get("valor_unit_produto") or 0),
                        }
                        for it in itens_vhsys
                        if (it.get("desc_produto") or it.get("id_produto"))
                    ]
                    if itens_para_salvar:
                        db.salvar_itens_pedido(mercos_id, itens_para_salvar)
                except Exception as e:
                    logger.warning(f"[MercosService] Falha ao salvar itens do VHSys (não crítico): {e}")

                # Notificação WhatsApp — alerta interno
                try:
                    wa = get_whatsapp()
                    wa.notificar_pedido_ok(
                        numero_pedido=numero,
                        mercos_id=mercos_id,
                        vhsys_id=vhsys_numero,
                        cliente=dados_mercos.get("cliente_razao_social", ""),
                        valor=valor_total,
                        condicao=dados_mercos.get("condicao_pagamento", ""),
                    )
                    # ── Confirmação pro cliente DESATIVADA ──
                    # Quando quiser ativar, descomentar o bloco abaixo:
                    # telefones = dados_mercos.get("cliente_telefone", [])
                    # fone = telefones[0] if telefones else ""
                    # if fone:
                    #     wa.confirmar_pedido_cliente(
                    #         telefone=fone,
                    #         nome_cliente=dados_mercos.get("cliente_razao_social", ""),
                    #         numero_pedido=numero,
                    #         valor=valor_total,
                    #         condicao=dados_mercos.get("condicao_pagamento", ""),
                    #     )
                except Exception as e:
                    logger.warning(f"[WhatsApp] Falha na notificação (não crítico): {e}")

            else:
                db.salvar_pedido_processado(mercos_id, "erro", status="erro")
                db.fluxo_marcar_erro(mercos_id)
                erro_msg = f"VHSys retornou código {resposta.get('code') if resposta else 'None'}: {resposta}"
                db.registrar_erro("pedidos", str(mercos_id), erro_msg[:500])
                logger.error(f"[MercosService] Falha ao criar pedido VHSYS para #{numero}")

                # Notificação WhatsApp — alerta de erro
                try:
                    get_whatsapp().notificar_pedido_erro(
                        numero_pedido=numero,
                        mercos_id=mercos_id,
                        cliente=dados_mercos.get("cliente_razao_social", ""),
                        motivo="Falha ao criar pedido no VHSys",
                    )
                except Exception as e:
                    logger.warning(f"[WhatsApp] Falha na notificação de erro (não crítico): {e}")

            return resposta

        finally:
            lock_pedido.release()

    def _traduzir_pedido(self, dados: dict) -> dict | None:
        cliente_cnpj = dados.get("cliente_cnpj", "")
        if not cliente_cnpj:
            logger.error(f"[MercosService] Pedido id={dados.get('id')} sem cliente_cnpj.")
            return None

        itens = []
        for item in dados.get("itens", []):
            if item.get("excluido"):
                continue

            sku            = str(item.get("produto_codigo", "")).strip()
            quantidade     = float(item.get("quantidade", 0))
            valor_unitario = float(item.get("preco_liquido", 0))

            if quantidade <= 0 or valor_unitario <= 0:
                logger.warning(f"[MercosService] Item inválido: SKU={sku}, qtd={quantidade}, valor={valor_unitario}")
                continue

            if not sku:
                nome = item.get("produto_nome", "").strip()
                logger.warning(f"[MercosService] Item sem SKU: '{nome}' — será buscado por nome no VHSys.")

            itens.append({
                "codigo_referencia": sku,
                "quantidade":        quantidade,
                "valor_unitario":    valor_unitario,
                "_descricao":        item.get("produto_nome", ""),
            })

        if not itens:
            logger.error(f"[MercosService] Pedido id={dados.get('id')} sem itens válidos.")
            return None

        obs_partes = []
        if dados.get("observacoes"):
            obs_partes.append(dados["observacoes"])
        obs_partes.append(f"Origem Mercos - Pedido #{dados.get('numero', dados.get('id'))}")

        return {
            "cliente_cnpj":       cliente_cnpj,
            "data":               dados.get("data_emissao") or dados.get("data_criacao", "")[:10],
            "observacoes":        " | ".join(obs_partes),
            "numero":             dados.get("numero") or dados.get("id", "sem numero"),
            "condicao_pagamento": dados.get("condicao_pagamento", "Não informada"),
            "itens":              itens,
            "transportadora_nome":   dados.get("transportadora_nome", ""),
            "cliente_razao_social":  dados.get("cliente_razao_social", ""),
            "cliente_nome_fantasia": dados.get("cliente_nome_fantasia", ""),
            "cliente_telefone":      dados.get("cliente_telefone", []),
            "cliente_rua":           dados.get("cliente_rua", ""),
            "cliente_numero":        dados.get("cliente_numero", ""),
            "cliente_bairro":        dados.get("cliente_bairro", ""),
            "cliente_cidade":        dados.get("cliente_cidade", ""),
            "cliente_estado":        dados.get("cliente_estado", ""),
        }

    def limpar_locks_antigos(self):
        """Remove locks de pedidos que não estão mais em processamento ativo."""
        with self._pedido_locks_meta:
            ids_para_remover = [
                pid for pid, lock in self._pedido_locks.items()
                if not lock.locked()
            ]
            for pid in ids_para_remover:
                del self._pedido_locks[pid]
            if ids_para_remover:
                logger.debug(f"[MercosService] {len(ids_para_remover)} lock(s) liberados.")

    def _garantir_cache_produtos(self):
        if self._produtos_carregados:
            return

        with self._cache_lock:
            if self._produtos_carregados:
                logger.debug("[MercosService] Cache já carregado por outra thread.")
                return

            logger.info("[MercosService] Carregando cache de produtos...")
            self.vhsys.carregar_todos_produtos()
            self._produtos_carregados = True
            logger.info(f"[MercosService] Cache pronto: {len(self.vhsys.cache_produtos)} produtos.")
