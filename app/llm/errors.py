"""LLM 呼叫層自訂例外。"""


class LLMError(Exception):
    """LLM 呼叫失敗：重試耗盡、連線例外、或降級後仍無法解析回應。"""
