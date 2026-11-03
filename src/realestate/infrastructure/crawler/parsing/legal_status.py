from __future__ import annotations
import re
from typing import Optional

LEGAL_STATUS_PATTERNS = [
    (re.compile(r"(?:sổ\s*đỏ\s*chính\s*chủ|sổ\s*hồng\s*chính\s*chủ)", re.IGNORECASE), "Sổ đỏ/hồng chính chủ"),
    (re.compile(r"(?:sổ\s*đỏ|sổ\s*hồng|sổ\s*riêng|sổ\s*vuông\s*đẹp)", re.IGNORECASE), "Sổ đỏ/hồng"),
    (re.compile(r"(?:hợp\s*đồng\s*mua\s*bán|hđmb)", re.IGNORECASE), "Hợp đồng mua bán"),
    (re.compile(r"(?:giấy\s*tờ\s*hợp\s*lệ|pháp\s*lý\s*chuẩn)", re.IGNORECASE), "Pháp lý hợp lệ"),
    (re.compile(r"(?:chờ\s*sổ|đang\s*chờ\s*cấp\s*sổ)", re.IGNORECASE), "Chờ cấp sổ"),
    (re.compile(r"(?:giấy\s*(?:tờ\s*)?tay|vi\s*bằng)", re.IGNORECASE), "Giấy viết tay/Vi bằng"),
]

def extract_legal_status(description: Optional[str]) -> Optional[str]:
    """Extract legal status from description using keyword patterns."""
    if not description:
        return None
    for pattern, label in LEGAL_STATUS_PATTERNS:
        if pattern.search(description):
            return label
    return None
