class SicoobError(Exception):
    """Erro genérico do módulo SICOOB."""
    pass


class SicoobConfigError(SicoobError):
    """Credenciais ou configuração ausentes."""
    pass


class BoletoError(SicoobError):
    """Erro em operação de boleto."""
    pass


class BoletoNaoEncontrado(BoletoError):
    """Boleto não encontrado na API SICOOB."""
    pass
