# 무좀 사진 자동 분류기 (Onycho Pictures Organizer)

## 프로젝트 개요
무좀(조갑진균증) 환자의 발톱 follow-up 사진을 OCR로 인식하여 자동으로 폴더 분류하는 프로그램.

## 워크플로우
1. 삼성 갤러리에서 사진 우측 상단에 방문날짜(YYMMDD)와 이름/회차를 수기로 기입
2. 프로그램이 OCR로 텍스트를 인식하여 자동 분류
3. 분류 구조: `output/{YYYY년MM월DD일}/{YYMMDD 이름 N회 또는 fuN}/사진.jpg`

## 기술 스택
- Python 3.11
- EasyOCR (한글+영문 OCR, GPU 미사용 - CPU 모드)
- tkinter + tkinterdnd2 (GUI + 드래그앤드롭)
- Pillow (이미지 전처리)

## 파일 구조
```
config.py          # 설정값 (경로, OCR 크롭 영역, 임계값 등)
ocr_engine.py      # EasyOCR 래퍼 (이미지 크롭 + 텍스트 추출)
parser.py          # OCR 텍스트 → 날짜/이름/회차 파싱 + 보정 + 학습
file_manager.py    # 폴더 생성, 파일 이동/복사, 리뷰 폴더 처리
organize.py        # CLI 실행 진입점
gui.py             # tkinter GUI (메인 + 리뷰 + 학습 관리 다이얼로그)
```

## 주요 설정 (config.py)
- `CROP_TOP=0.0, CROP_BOTTOM=0.30, CROP_LEFT=0.7, CROP_RIGHT=1.0` → 우측 상단 30%×30% 영역만 OCR
- `IMAGE_MAX_SIZE=None` → 리사이즈 비활성화 (크롭만 사용)
- `CONFIDENCE_THRESHOLD=0.3` → OCR 신뢰도 임계값
- 폴더명 형식: 띄어쓰기 없음 (`2026년03월31일`)

## OCR 보정 로직 (parser.py)
- **날짜 7→1 보정**: OCR이 1을 7로 오인식 → 유효한 날짜 후보가 유일할 때만 보정
- **파일명 날짜 힌트**: 파일명(YYYYMMDD_HHMMSS)에서 촬영 날짜 추출, 보정 후보 중 ±5일 이내 가장 가까운 후보 선택
- **회차 범위 제한**: 일반 회차 1~10, fu(follow-up)는 제한 없음
- **영문 노이즈 허용**: "김명재F10회" → "김명재 10회"로 파싱
- **"회" 오인식 대응**: 희, 휘, 획 등도 인식
- **연속 촬영 그룹 보완**: 5분 이내 연속 촬영 사진 그룹핑, 1장 성공하면 나머지에 동일 정보 적용
- **학습 기반 보정**: 리뷰에서 수정한 결과를 `_learned_corrections.json`에 저장, 동일 OCR 결과 재발 시 자동 적용

## GUI 기능
1. 미리보기 → OCR 결과 확인 (보정/그룹 태그 표시)
2. 실패 사진 리뷰 → 크롭 영역 확인 + OCR 자동 채우기 + 수기 입력
3. 개별 리뷰 → 우클릭 또는 실패 항목 더블클릭
4. 분류 실행 → OCR 성공 + 수기 입력 모두 분류 (OCR 재실행 안 함)
5. 일시정지/재개 → 진행 중 일시정지, 일시정지 중 분류실행 가능
6. 학습 보정 관리 → 학습된 보정 목록 조회/삭제
7. 드래그앤드롭 → 사진 파일을 입력 폴더로 복사
8. 폴더 열기 → 입력/출력/리뷰 폴더 탐색기로 열기
9. 진행률 + 예상 완료시간 표시

## 실행 방법
```bash
pip install easyocr Pillow tkinterdnd2
python gui.py       # GUI 실행
python organize.py  # CLI 실행
```

## 참고사항
- Windows 환경에서 SSL 인증서 오류 우회 적용됨 (ocr_engine.py)
- cp949 인코딩 오류 방지를 위해 stdout/stderr UTF-8 강제 설정
- EasyOCR 모델은 최초 1회 다운로드 후 `~/.EasyOCR/model/`에 캐시
- GTX 1060 GPU 있으나 CUDA 미설치 상태 → CPU 모드 사용 중
