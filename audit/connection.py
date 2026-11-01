"""Read-only XML-RPC connection to Odoo.

Every call goes through ``OdooConnection.call``, which only lets a small
whitelist of *reading* methods through. Anything else (write, create, unlink,
action_*, button_*, ...) raises ``ReadOnlyViolation`` before a request is sent.
"""
from __future__ import annotations

import os
import xmlrpc.client
from typing import Any, Dict, Iterable, List, Optional

READ_ONLY_METHODS = frozenset(
    {"search", "search_read", "read", "search_count", "fields_get", "read_group"}
)


class ReadOnlyViolation(Exception):
    """Raised when code tries to call a non-reading method."""


class _HTTPTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: int):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class _HTTPSTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: int):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


def load_env(path: str = ".env") -> None:
    """Tiny .env loader (no dependency). Existing environment variables win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


class OdooConnection:
    def __init__(
        self,
        url: str,
        db: str,
        user: str,
        api_key: str,
        timeout: int = 120,
        company_id: Optional[int] = None,
    ):
        self.url = url.rstrip("/")
        self.db = db
        self.user = user
        self._api_key = api_key
        self.company_id = company_id
        transport_cls = _HTTPSTransport if self.url.startswith("https") else _HTTPTransport
        self._common = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/common", transport=transport_cls(timeout), allow_none=True
        )
        self._object = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/object", transport=transport_cls(timeout), allow_none=True
        )
        self.uid: Optional[int] = None
        self.version: str = "unknown"
        self.major: Optional[int] = None
        self._fields_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ setup
    def connect(self) -> None:
        info = self._common.version()
        self.version = str(info.get("server_version", "unknown"))
        try:
            self.major = int(info["server_version_info"][0])
        except (KeyError, IndexError, ValueError, TypeError):
            self.major = None
        uid = self._common.authenticate(self.db, self.user, self._api_key, {})
        if not uid:
            raise RuntimeError("Authentication failed: check ODOO_DB, ODOO_USER and ODOO_API_KEY.")
        self.uid = uid

    # ------------------------------------------------------------------- core
    def call(self, model: str, method: str, args: List[Any], kwargs: Optional[Dict[str, Any]] = None):
        if method not in READ_ONLY_METHODS:
            raise ReadOnlyViolation(
                f"Blocked call to '{method}' on '{model}': this toolkit is read-only."
            )
        if self.uid is None:
            raise RuntimeError("Not connected: call connect() first.")
        kwargs = dict(kwargs or {})
        context = dict(kwargs.get("context") or {})
        if self.company_id:
            context.setdefault("allowed_company_ids", [self.company_id])
        if context:
            kwargs["context"] = context
        return self._object.execute_kw(self.db, self.uid, self._api_key, model, method, args, kwargs)

    # --------------------------------------------------------------- helpers
    def search_read_all(
        self,
        model: str,
        domain: List[Any],
        fields: List[str],
        batch: int = 2000,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Fetch all matching records, paging by id so memory/time stay bounded."""
        out: List[Dict[str, Any]] = []
        last_id = 0
        while True:
            page_size = batch if limit is None else min(batch, limit - len(out))
            if page_size <= 0:
                break
            page = self.call(
                model,
                "search_read",
                [list(domain) + [("id", ">", last_id)]],
                dict(fields=fields, order="id", limit=page_size, **kwargs),
            )
            if not page:
                break
            out.extend(page)
            last_id = page[-1]["id"]
            if len(page) < page_size:
                break
        return out

    def read(self, model: str, ids: Iterable[int], fields: List[str], chunk: int = 500) -> List[Dict[str, Any]]:
        ids = list(dict.fromkeys(ids))
        out: List[Dict[str, Any]] = []
        for i in range(0, len(ids), chunk):
            out.extend(self.call(model, "read", [ids[i : i + chunk]], {"fields": fields}))
        return out

    def fields(self, model: str) -> Dict[str, Any]:
        """Field definitions of a model, or {} if the model is not installed."""
        if model not in self._fields_cache:
            try:
                self._fields_cache[model] = self.call(model, "fields_get", [], {"attributes": ["type", "string"]})
            except xmlrpc.client.Fault:
                self._fields_cache[model] = {}
        return self._fields_cache[model]

    def has_model(self, model: str) -> bool:
        return bool(self.fields(model))

    def has_field(self, model: str, field: str) -> bool:
        return field in self.fields(model)
