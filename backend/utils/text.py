"""텍스트 정제 유틸 — 추출물(raw_content)을 저장/해싱 전에 안전한 str 로 만든다.

배경 (2026-06-15):
  Docling 이 PDF 의 수학 볼드/이탤릭 문자(U+1D400 영역 등 astral plane)를
  파이썬 str 에 **surrogate pair 두 조각**(예: '\\ud835\\udc00')으로 흘려보내는
  경우가 있다. 이런 lone/split surrogate 가 섞인 str 은 `.encode("utf-8")` 에서
  `UnicodeEncodeError: surrogates not allowed` 로 터진다 → sha256_text / asyncpg
  DB write 가 실패하고 ingest 가 통째로 죽는다 (arxiv PDF 2605.29583 사례).

처리 원칙 (§2 loss-less):
  - high+low 가 인접한 **정상 surrogate pair 는 원래 코드포인트로 복원** (무손실).
    예: '\\ud835\\udc00' → '\\U0001d400' (𝐀). utf-16 surrogatepass 왕복으로 재결합.
  - 짝 없는 **lone surrogate 만** U+FFFD(replacement)로 대체 — 이미 깨진 데이터라
    복원 불가, 버리지 않고 흔적만 남긴다.
"""

from __future__ import annotations


def repair_surrogates(text: str) -> str:
    """str 안의 surrogate 를 안전하게 정리해 utf-8 인코딩 가능한 str 로 만든다.

    정상이면(가장 흔함) 빠른 경로로 원본을 그대로 반환한다. surrogate 가 섞여
    있을 때만 utf-16 surrogatepass 왕복으로 pair 를 복원하고 lone 은 replace.
    """
    # fast path — 대부분의 텍스트는 surrogate 가 없다.
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        pass

    # utf-16 surrogatepass 로 인코딩하면 surrogate code unit 이 그대로 보존되고,
    # 다시 decode 할 때 인접한 high+low 가 astral 코드포인트로 재결합된다.
    # 짝이 안 맞는 lone surrogate 는 errors='replace' 로 U+FFFD 가 된다.
    repaired = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")

    # 안전망 — 그래도 남는 게 있으면(이론상 없음) 최후로 utf-8 replace.
    try:
        repaired.encode("utf-8")
    except UnicodeEncodeError:
        repaired = repaired.encode("utf-8", "replace").decode("utf-8")
    return repaired
