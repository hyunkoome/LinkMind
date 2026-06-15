"""repair_surrogates — Docling 추출물의 split/lone surrogate 정리 회귀 테스트.

배경: arxiv PDF 2605.29583 가 수학 볼드 문자(U+1D400 영역)를 split surrogate
(U+D835 + U+DC00 두 code unit)로 흘려보내 sha256_text/DB write 의 utf-8
인코딩에서 ingest 가 통째로 죽었다. repair_surrogates 가 pair 는 복원(무손실),
lone 은 replace 한다.

주의: 소스에 astral 글리프(𝐀)를 직접 쓰면 .py 가 정상 U+1D400 로 저장돼 버그
재현이 안 된다. 깨진 입력은 반드시 chr(0xD835) 등 surrogate code unit 으로 구성.
"""

from __future__ import annotations

import pytest

from backend.utils.text import repair_surrogates

HIGH = chr(0xD835)       # split surrogate 의 high half
LOW = chr(0xDC00)        # split surrogate 의 low half — 둘이 붙으면 U+1D400 (𝐀)
BOLD_A = chr(0x1D400)    # 정상 단일 코드포인트 (𝐀)


def _is_utf8_encodable(s: str) -> bool:
    try:
        s.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def test_clean_text_unchanged():
    """surrogate 없는 평범한 텍스트(한글/이모지 포함)는 그대로 통과."""
    s = "보통 텍스트 with math A=B and 한국어 \U0001F600 ✓"
    assert repair_surrogates(s) == s


def test_split_pair_recovered_losslessly():
    """인접한 high+low surrogate 는 원래 astral 코드포인트로 복원(무손실)."""
    broken = "x" + HIGH + LOW + "y"
    assert not _is_utf8_encodable(broken)              # 입력은 깨진 상태
    assert len(broken) == 4                            # surrogate 2개 = 별도 code unit
    fixed = repair_surrogates(broken)
    assert fixed == "x" + BOLD_A + "y"                 # 단일 코드포인트로 복원
    assert len(fixed) == 3
    assert _is_utf8_encodable(fixed)


def test_lone_surrogate_replaced_not_crashing():
    """짝 없는 lone surrogate 는 U+FFFD 로 대체되어 인코딩 가능해진다."""
    broken = "before" + HIGH + "after"                 # high 뒤에 low 가 없음
    fixed = repair_surrogates(broken)
    assert _is_utf8_encodable(fixed)
    assert "before" in fixed and "after" in fixed
    assert HIGH not in fixed


def test_result_always_encodable_for_mixed():
    """pair + lone 이 섞인 최악의 케이스도 항상 utf-8 인코딩 가능해야."""
    broken = "a" + HIGH + LOW + "b" + HIGH + "c" + LOW + "d"   # 정상 pair + lone high + lone low
    fixed = repair_surrogates(broken)
    assert _is_utf8_encodable(fixed)
    assert BOLD_A in fixed                             # 정상 pair 는 보존


def test_real_world_position_offset():
    """원인 로그처럼 본문 중간(position 12042)에 surrogate 가 있어도 동작."""
    body = "라" * 12042 + HIGH + LOW + " 결론"
    fixed = repair_surrogates(body)
    assert _is_utf8_encodable(fixed)
    # sha256_text 가 더 이상 터지지 않음을 직접 확인
    from backend.utils.hashing import sha256_text

    assert len(sha256_text(fixed)) == 64


@pytest.mark.parametrize("s", ["", "plain", BOLD_A * 3 + " already-correct astral"])
def test_idempotent_and_safe(s):
    """이미 정상인 입력(빈 문자열/정상 astral 포함)에 두 번 적용해도 동일."""
    once = repair_surrogates(s)
    assert once == s
    assert repair_surrogates(once) == once
