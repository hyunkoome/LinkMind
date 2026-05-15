"""
backend.ingest.pdf._detect_abstract 단위 테스트.

이번 세션의 regex 보강 (em-dash 라벨, 'I NTRODUCTION' 글자 사이 공백 종결,
'SUPPLEMENTARY MATERIAL' 종결, 라벨 없는 fallback 등) 이 모두 통과하는지 확인.

샘플 본문은 실제 ingest 한 논문 (FAST-LIVO2 / SLAM Multi-Camera ICRA 2020) 의 첫 페이지
구조를 단순화한 형태. 실제 PDF 가 필요한 게 아니라 _detect_abstract 의 입력 텍스트
형태를 그대로 모사. PDF 텍스트 추출은 pypdf/pymupdf 가 담당하니까 그 결과의 string
처리만 검증.
"""

from __future__ import annotations

from backend.ingest.pdf import _detect_abstract


# ── 1) IEEE/ICRA 스타일: "Abstract—" em-dash ─────────────────


FAST_LIVO2_LIKE = """\
FAST-LIVO2: Fast, Direct LiDAR-Inertial-Visual Odometry
Authors: A, B, C  Affiliations: ...

Abstract—This paper proposes FAST-LIVO2: a fast, direct
LiDAR-inertial-visual odometry framework to achieve accurate
and robust state estimation in Simultaneous Localization and
Mapping (SLAM) tasks and provide great potential in real-time,
onboard robotic applications. FAST-LIVO2 fuses the IMU, LiDAR
and image measurements efficiently through an error-state iterated
kalman filter (ESIKF).

†Corresponding author (email: foo@bar)

I. INTRODUCTION
As an important building block in robotics, ...
"""


def test_abstract_em_dash_label():
    """`Abstract—` em-dash 라벨이 있고 그 다음 `I. INTRODUCTION` 으로 끝나는 경우."""
    out = _detect_abstract(FAST_LIVO2_LIKE)
    assert out is not None
    assert "FAST-LIVO2" in out
    assert "ESIKF" in out
    # 종결 직후의 author 라인이 abstract 본문에 끌려들어가지 않아야 함.
    assert "Corresponding author" not in out
    assert "As an important building block" not in out


# ── 2) PDF 추출이 글자 사이에 공백 끼워넣는 케이스: "I NTRODUCTION" ───


SLAM_MULTI_CAM_LIKE = """\
This paper has been accepted for publication at ICRA 2020.

Redesigning SLAM for Arbitrary Multi-Camera Systems
Authors: J. Kuo, M. Muglikar, Z. Zhang, D. Scaramuzza

Abstract— Adding more cameras to SLAM systems improves
robustness and accuracy but complicates the design of the visual
front-end significantly. Thus, most systems in the literature are
tailored for specific camera configurations. In this work, we aim
at an adaptive SLAM system that works for arbitrary multi-camera
setups. We propose adaptive initialization, sensor-agnostic keyframe
selection, and a scalable voxel-based map.

SUPPLEMENTARY MATERIAL
Video: https://youtu.be/JGL4H93BiNw

I. I NTRODUCTION
As an important building block in robotics, ...
"""


def test_abstract_supplementary_material_end():
    """abstract 다음에 'SUPPLEMENTARY MATERIAL' 섹션이 오는 경우, 그 직전까지를 추출."""
    out = _detect_abstract(SLAM_MULTI_CAM_LIKE)
    assert out is not None
    assert "Adding more cameras" in out
    assert "scalable voxel-based map" in out
    assert "SUPPLEMENTARY MATERIAL" not in out
    assert "youtu.be" not in out


def test_abstract_spaced_introduction_end():
    """'I NTRODUCTION' (글자 사이 공백) 도 abstract 종결 신호로 인식."""
    sample = """\
Abstract—This is the abstract body that is sufficiently long for the
detector to accept it as a real abstract candidate, well over one
hundred characters to satisfy the minimum length requirement.

I NTRODUCTION
Body of intro starts here...
"""
    out = _detect_abstract(sample)
    assert out is not None
    assert "abstract body" in out
    assert "Body of intro" not in out


# ── 3) 다양한 라벨 변형 ──────────────────────────────────────


def test_abstract_colon_label():
    """`Abstract:` 콜론 스타일."""
    sample = """\
Title of the paper
Some author info

Abstract: This is the abstract body in colon-style label format. It needs
to be at least one hundred characters long for the regex to accept it,
so this sentence pads it out to be sure.

1. Introduction
"""
    out = _detect_abstract(sample)
    assert out is not None
    assert "colon-style label" in out
    assert "1. Introduction" not in out


def test_abstract_uppercase_label_on_its_own_line():
    """`ABSTRACT` 단독 라인 + 다음 줄부터 본문."""
    sample = """\
Title of the paper

ABSTRACT
This abstract starts on the line after the label. It contains enough
text to satisfy the 100 character minimum so the detector returns it.

Keywords: foo, bar, baz
"""
    out = _detect_abstract(sample)
    assert out is not None
    assert "starts on the line after" in out
    assert "Keywords" not in out


# ── 4) 라벨 없는 fallback ────────────────────────────────────


def test_abstract_fallback_without_label():
    """라벨 없이 첫 단락이 abstract — Introduction 직전 단락을 후보로."""
    sample = """\
A Workshop Paper Title
Authors: Foo Bar, Baz Qux

This paragraph is the de facto abstract. There is no explicit label such
as Abstract or ABSTRACT in this paper, which is common in some workshop
proceedings. The fallback heuristic should grab this paragraph because it
comes right before the Introduction section and has enough words.

1. Introduction
The actual introduction body starts here ...
"""
    out = _detect_abstract(sample)
    assert out is not None
    assert "de facto abstract" in out
    assert "Foo Bar" not in out
    assert "actual introduction body" not in out


def test_abstract_returns_none_when_too_short():
    """라벨 다음 본문이 100자 미만이면 None (다음 단락도 fallback 자격 없음)."""
    sample = """\
Title

Abstract— short.

1. Introduction
"""
    assert _detect_abstract(sample) is None


def test_abstract_returns_none_when_no_signal():
    """abstract/intro 표지가 전혀 없는 짧은 글은 None."""
    sample = "Just a tiny snippet of text without any structure."
    assert _detect_abstract(sample) is None
