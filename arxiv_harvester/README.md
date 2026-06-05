# arxiv-harvester

키워드 기반 arxiv 논문 수집 라이브러리 — 키워드/쿼리로 arxiv를 검색하고 기간·카테고리로
필터링한다. 외부 앱 의존이 전혀 없는 순수 라이브러리(검색 결과 `list[ArxivPaper]`를 돌려줄
뿐, 저장·다운로드는 호출자 몫).

> LinkMind에서 분리 예정인 독립 패키지입니다. 현재는 LinkMind repo 안에 함께 들어 있고,
> 안정화 후 별도 OSS repo + PyPI로 추출합니다.

## 설치 (추출 후)

```bash
pip install arxiv-harvester
```

## 사용

```python
import asyncio
from arxiv_harvester import search, build_query, apply_filters, load_keywords

async def main():
    keywords = load_keywords("keywords.yaml")          # yaml에서 키워드 (선택)
    query = build_query(keywords, match="OR")          # → 'all:"gaussian splatting" OR all:SLAM'
    papers = await search(query, max_results=30, sort_by="submittedDate")
    papers = apply_filters(papers, categories=["cs.CV", "cs.RO"], dedup=True)
    for p in papers:
        print(p.arxiv_id, p.published, p.title)

asyncio.run(main())
```

### keywords.yaml

```yaml
keywords:
  - gaussian splatting
  - multi-camera SLAM
  - LoRA fine-tuning
```

## API

- `search(query, *, max_results=10, sort_by='relevance', timeout=15.0) -> list[ArxivPaper]`
  — arxiv 검색. 빈 쿼리/네트워크 오류는 빈 리스트로 graceful.
- `build_query(keywords, *, match='OR') -> str` — 키워드 리스트 → arxiv search_query 문자열.
- `apply_filters(papers, *, date_from, date_to, categories, dedup=True)` — 순수 필터.
- `dedup_by_arxiv_id`, `filter_by_date`, `filter_by_category` — 개별 필터.
- `load_keywords(path) -> list[str]` — yaml 키워드 로더.
- `ArxivPaper` — `arxiv_id, title, summary, authors, published, categories, abs_url, pdf_url`.

## 라이선스

MIT — `LICENSE` 참조.
