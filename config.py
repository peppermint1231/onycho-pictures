"""설정값 모음"""
import os

# 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, "input")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
REVIEW_DIR = os.path.join(BASE_DIR, "_review")

# 지원 확장자
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# OCR 설정
OCR_LANGUAGES = ["ko", "en"]
CONFIDENCE_THRESHOLD = 0.3

# 파일 처리 모드: False = 이동, True = 복사
COPY_MODE = False

# 속도 최적화: 이미지 리사이즈 (긴 변 기준 픽셀, None이면 원본 사용)
IMAGE_MAX_SIZE = None

# 속도 최적화: 텍스트 영역 크롭 (이미지 비율 기준)
# 예: 하단 40%만 스캔 → CROP_TOP=0.6, CROP_BOTTOM=1.0
# None이면 전체 이미지 스캔
CROP_TOP = 0.0      # 상단부터
CROP_BOTTOM = 0.25  # 위쪽 25%까지
CROP_LEFT = 0.7     # 오른쪽 30%부터
CROP_RIGHT = 1.0    # 우측 끝까지

# 날짜 폴더 형식
DATE_FOLDER_FORMAT = "{year}년{month:02d}월{day:02d}일"
