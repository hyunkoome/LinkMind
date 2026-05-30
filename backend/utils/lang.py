"""
backend/utils/lang.py — 텍스트 언어 판별 유틸.

위키 본문은 한국어/영어로만 작성되어야 한다 (writer_v1.yaml 의 "언어 규칙").
하지만 본문 합성 모델(Qwen2.5-7B)은 Alibaba(중국) 모델이라, source 자료가
중국어/일본어면 "중국어 금지" 프롬프트 규칙을 어기고 그 언어로 본문을 써버리는
native bias 가 있다. temperature 를 낮춰도 이 bias 는 안 사라진다.

→ 생성 직후 이 모듈로 외국어(중국어 한자 / 일본어 가나) 혼입을 감지해
  WriterAgent 가 자동 재생성한다 (backend/agents/writer.py). 이미 저장된
  오염 위키는 backend/jobs/regenerate_foreign_wikis.py 가 일괄 재생성.

판정 기준:
  - 한글(U+AC00–U+D7A3)·한글 자모는 외국어로 치지 않는다 (한국어는 정상).
  - 한자는 한국어에도 드물게 쓰이므로(고유명사·약어 등) 절대 수
    임계값(threshold)으로 판단 — 정상 한국어 위키엔 한자가 0~2 수준이고,
    중국어 본문엔 수십~수백 자 나오므로 잘 갈린다.
"""

from __future__ import annotations

import re

# 중국어 한자 + 일본어 가나 (한글 U+AC00–U+D7A3 은 의도적으로 제외 — 한국어는 정상).
# ⚠️ 반드시 명시적 \u 코드포인트로 — 리터럴 CJK 문자로 범위를 쓰면 복붙/인코딩 과정에서
#    경계가 어긋나 한글 음절까지 매칭되는 버그가 실제로 났었다 (2026-05-30).
#   ぀–ヿ : 히라가나 + 가타카나
#   㐀–䶿 : CJK Ext-A
#   一–鿿 : CJK Unified Ideographs
#   豈–﫿 : CJK Compatibility Ideographs
_FOREIGN_CJK_RE = re.compile(
    "[぀-ヿ㐀-䶿一-鿿豈-﫿]"
)

# SQL(Postgres) 1차 후보 필터용 — 같은 문자 범위 (한자/가나 1자라도 있으면 매칭).
# job 에서 이 패턴으로 후보를 좁힌 뒤 count_foreign_cjk 로 threshold 재검증한다.
# Python 문자열이 실제 유니코드 문자로 평가되고 asyncpg 가 그대로 전달 →
# Postgres POSIX regex `~` 의 char range 로 동작 (DB 가 UTF-8).
FOREIGN_CJK_SQL_PATTERN = "[぀-ヿ㐀-䶿一-鿿豈-﫿]"

# 한국어 위키 본문에 허용되는 한자/가나 최대 수. 이걸 초과하면 외국어 오염으로
# 보고 재생성한다. 정상 한국어 위키는 0~2 수준, 중국어 본문은 수십+.
FOREIGN_THRESHOLD = 8


def count_foreign_cjk(text: str) -> int:
    """text 안의 중국어 한자 + 일본어 가나 문자 수. 한글은 세지 않음."""
    if not text:
        return 0
    return len(_FOREIGN_CJK_RE.findall(text))


def has_foreign_script(text: str, threshold: int = FOREIGN_THRESHOLD) -> bool:
    """본문에 중국어/일본어가 임계(threshold) 초과로 섞였는지."""
    return count_foreign_cjk(text) > threshold
