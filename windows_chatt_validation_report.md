# Windows 및 ChatT 교체 최종 검증 보고서

검증일: 2026-10-21

## 1. 결론

- M1~M4.5의 실제 3개 PDF 실행 결과와 PDF/DOCX/PPTX/XLSX 자동 테스트를 확인했다.
- M7~M8은 합성 분류·번역 결과를 넣어 집계, provenance, 요구사항 수, 2개 시트 XLSX 생성을 검증했다.
- 전체 자동 테스트 26개와 Ruff 정적 검사를 통과했고 설치 의존성 충돌도 없다.
- 소스 코드는 `pathlib`를 사용하므로 Windows 경로 구분자에 종속되지 않는다.
- ChatT가 아래의 OpenAI 호환 계약을 제공한다면 `.env`의 API key, 주소, 모델명을 입력하는 것만으로 M5/M6를 실행할 수 있다.
- 실제 ChatT 계정과 서버가 없으므로 인증, TLS 인증서, 모델 권한, 실제 응답 품질까지는 확인할 수 없다. 따라서 운영 전 1건 smoke test는 반드시 필요하다.

## 2. M1~M4.5 실제 문서 검증

대형 PDF를 제외하고 CAN, PMIC, 센서 데이터시트 3개(총 74페이지)를 사용했다.

| 단계 | 검증 결과 |
|---|---:|
| M1 문서 / 고유 doc_id | 3 / 3 |
| M2 파싱 블록 | 2,153 |
| M2 페이지 커버리지 | 74/74 (100%) |
| M2 빈 블록 | 0 |
| M2.5 PASS / SKIP | 2,153 / 0 |
| M3 청크 | 111 |
| M3 문서 경계 오병합 | 0 |
| M3 provenance 누락 | 0 |
| M4 배치 / 제외 후보 | 344 / 415 |
| M4.5 LLM 입력 | 581 |
| M4.5 중복 batch_id / cur_id | 0 / 0 |
| M4.5 빈 원문 | 0 |

PDF 본문의 `contents` 동사가 목차로 오인되던 문제를 제목 전체 일치 방식으로 수정했다. 재검사 결과 규범 신호가 있는 비목차 제외 항목은 0건이었다. 상세 내용은 `output/run_20261021_automotive_3_v3/m1_m4_validation_report.md`에 있다.

확장자 검증 범위는 다음과 같다.

- PDF: 실제 3개 문서 + 페이지/메타데이터 경계 회귀 테스트
- DOCX: 제목 계층과 표 행 원자성 테스트
- PPTX: 제목만 있는 슬라이드, 표 셀, 슬라이드 경계 테스트
- XLSX: 요구사항 행, ID, 지원용 시트 구분 테스트
- XLS: 구현과 의존성(`pandas`, `xlrd`)은 존재하지만 이번에 실 XLS 표본은 없었다.

## 3. M7~M8 검증 및 보강

M7은 요구사항이면서 전장품 관련인 교집합만 Sheet 2용 레코드로 만든다. M8의 Sheet 1 전체 요구사항 수는 `is_requirement=true` 전체이며, 전장품 요구사항 수는 `is_requirement=true AND is_ee_related=true`다.

검증 항목:

- `doc_id`, `cur_id`, 원문, 번역, 분류, 원본 파일·페이지 provenance 유지
- Sheet 1 이름 `문서 요약`, Sheet 2 이름 `전장 요구사항`
- Sheet 1 전체/전장 요구사항 수 계산
- Sheet 2 열: `doc_id / cur_id / 원문 / 한글 번역 / 비고 / 전장품 종류`
- M6 번역 누락·빈 번역·중복 번역 ID가 있으면 M7에서 중단
- Sheet 1의 전장 요구사항 집합과 Sheet 2 레코드가 불일치하면 M8에서 중단

따라서 API 실패로 번역이 비어 있는데도 완성된 XLSX처럼 저장되는 경로를 차단했다.

## 4. Windows 호환성

권장 환경은 64비트 Windows와 Python 3.11이다.

```powershell
conda create -n elecreq python=3.11 -y
conda activate elecreq
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m ee_pipeline.cli run --input "data\input" --output "output"
```

`cmd.exe`에서는 `Copy-Item` 대신 `copy .env.example .env`를 사용한다.

확인 사항:

- OS 전용 shell 명령, `/tmp`, `/Users`, `chmod`, symlink에 파이프라인 코드가 의존하지 않는다.
- 파일 입출력은 UTF-8과 `pathlib.Path`를 사용한다.
- PDF import를 `import pymupdf`로 변경해 Windows에서 별도 패키지 `fitz`와 충돌하는 문제를 피했다.
- `pymupdf>=1.24.3`으로 최소 버전을 맞췄다.
- PyMuPDF DLL 로딩 오류가 발생하는 일부 Windows PC는 [공식 설치 문서](https://pymupdf.readthedocs.io/en/latest/installation.html)에 따라 Microsoft Visual C++ Redistributable 설치가 필요할 수 있다.
- `.xlsx`는 Excel 설치 없이 `openpyxl`로 생성한다.

## 5. ChatT API 교체 조건

`.env`에는 다음 값만 입력한다.

```dotenv
CHATT_API_KEY=발급받은_API_KEY
CHATT_BASE_URL=https://실제_서버_주소
CHATT_MODEL=실제_모델명
CHATT_MAX_CONCURRENCY=4
CHATT_REQUESTS_PER_MINUTE=서비스_한도
```

주소는 서버 루트, `/v1`, 또는 완전한 `.../chat/completions` endpoint를 허용한다. 기본 동시 호출은 4를 유지하고, 관리자가 동시 8개를 보장한 경우에만 8로 높인다.

환경값만으로 작동하기 위한 서버 계약:

1. `POST .../chat/completions`
2. `Authorization: Bearer <API key>`
3. 요청 JSON에 `model`, `messages`, `temperature`, `max_tokens` 지원
4. 응답 JSON이 `choices[0].message.content` 형태
5. M5가 프롬프트에 지정된 JSON 필드를 반환하고 M6가 번역 문자열을 반환

이 계약은 모의 클라이언트 테스트로 확인했다. 필수 환경값이 없거나, boolean/category가 잘못됐거나, HTTP 요청·번역이 실패하면 재시도 후 산출물 작성을 중단한다. 실패를 `non-REQ/non-EE`로 저장하지 않는다.

ChatT가 API key를 다른 헤더에 넣거나, endpoint/응답 구조가 위와 다르면 `.env` 변경만으로는 부족하고 `src/ee_pipeline/chatt_client.py` 어댑터 한 곳을 수정해야 한다.

## 6. 최종 검증 결과

```text
pytest: 26 passed
ruff: All checks passed
pip check: No broken requirements found
CLI help: M1, M2, M2.5, M3, M4, M4.5, M5, M6, M7-M8 정상 등록
```

남은 불확실성은 실제 ChatT 서버 계약과 모델의 의미 분류 정확도뿐이다. 로컬 AI 장시간 작업은 중단했으며, 남아 있는 로컬 checkpoint 파일은 최종 M5/M6 검증 결과로 간주하지 않는다.
