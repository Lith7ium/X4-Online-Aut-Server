"""Correlate a pilot command and its receipt within one admission."""


class CompanyCommandWindow:
    """Correlate every terminal response with the request from this admission."""

    def __init__(self, request_parser, receipt_parser, id_field):
        self._request_parser = request_parser
        self._receipt_parser = receipt_parser
        self._id_field = id_field
        self.pending = None
        self._seen = set()

    def receipt(self, value):
        if self.pending is None and value is None:
            return None
        receipt = self._receipt_parser(value)
        if self.pending is None or any(receipt[key] != expected for key, expected in self.pending.items()):
            raise ValueError('Company receipt differs from pending command')
        return receipt

    def next_request(self, value):
        if value is None:
            return None
        request = self._request_parser(value)
        if request[self._id_field] in self._seen or len(self._seen) >= 4096:
            raise ValueError('duplicate or exhausted Company command ID')
        return request

    def advance(self, request):
        self.pending = dict(request) if request is not None else None
        if request is not None:
            self._seen.add(request[self._id_field])
