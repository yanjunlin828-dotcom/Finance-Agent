"""只允许通过DocumentManifest中的ID读取文档元数据。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from finresearch.contracts import DocumentManifestRecord


class UnknownDocumentError(KeyError):
    """工具请求引用未登记文档时抛出。"""


class DocumentIntegrityError(RuntimeError):
    """登记文件缺失、越界或指纹不一致时抛出。"""


class RegisteredDocumentTools:
    """按文档ID执行最小只读工具，不接收模型提供的路径。"""

    def __init__(self, project_root: Path, records: list[dict[str, Any]]) -> None:
        self.project_root = project_root.resolve()
        validated = [DocumentManifestRecord.model_validate(record) for record in records]
        ids = [record.document_id for record in validated]
        if len(ids) != len(set(ids)):
            raise ValueError("document_id不得重复")
        self._records = {record.document_id: record for record in validated}

    def get_document_page_count(self, document_id: str) -> dict[str, int | str]:
        """验证文件身份后返回登记页数。

        输入只有文档ID，路径来自受信清单。哈希不一致时拒绝返回元数据，避免模型
        借同一ID使用被替换的文件。
        """

        record = self._records.get(document_id)
        if record is None:
            raise UnknownDocumentError(f"未知document_id: {document_id}")
        path = (self.project_root / record.local_path).resolve()
        if not path.is_relative_to(self.project_root):
            raise DocumentIntegrityError("登记路径超出项目根目录")
        if not path.is_file():
            raise DocumentIntegrityError("登记文件不存在")
        if _sha256(path) != record.sha256:
            raise DocumentIntegrityError("登记文件SHA-256不匹配")
        return {"document_id": document_id, "page_count": record.page_count}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
