"""EasyOCR을 이용한 이미지 텍스트 추출"""
import os
import sys
import io
import ssl
import easyocr
from PIL import Image
from config import (
    OCR_LANGUAGES, CONFIDENCE_THRESHOLD,
    IMAGE_MAX_SIZE, CROP_TOP, CROP_BOTTOM, CROP_LEFT, CROP_RIGHT,
)

# SSL 인증서 검증 우회 (Windows 환경 인증서 문제 대응)
ssl._create_default_https_context = ssl._create_unverified_context

# Windows cp949 인코딩 오류 방지 - stdout/stderr를 UTF-8로 교체
if sys.platform == "win32":
    if sys.stdout is not None and hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    elif sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is not None and hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    elif sys.stderr is None:
        sys.stderr = io.StringIO()
    os.environ["PYTHONIOENCODING"] = "utf-8"

_reader = None


def get_reader():
    """EasyOCR 리더를 싱글턴으로 관리 (모델 로딩은 최초 1회만)"""
    global _reader
    if _reader is None:
        print("OCR 모델 로딩 중... (최초 1회, 잠시 기다려주세요)")
        _reader = easyocr.Reader(OCR_LANGUAGES, gpu=False, download_enabled=True)
        print("OCR 모델 로딩 완료!")
    return _reader


def preprocess_image(image_path):
    """
    이미지를 크롭 + 리사이즈하여 OCR 속도를 높입니다.
    numpy 배열로 반환합니다.
    """
    import numpy as np

    img = Image.open(image_path)
    w, h = img.size

    # 크롭: 텍스트 영역만 잘라내기
    if CROP_TOP is not None:
        left = int(w * CROP_LEFT)
        right = int(w * CROP_RIGHT)
        top = int(h * CROP_TOP)
        bottom = int(h * CROP_BOTTOM)
        img = img.crop((left, top, right, bottom))

    # 리사이즈: 긴 변 기준으로 축소
    if IMAGE_MAX_SIZE is not None:
        w, h = img.size
        if max(w, h) > IMAGE_MAX_SIZE:
            ratio = IMAGE_MAX_SIZE / max(w, h)
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

    return np.array(img)


def extract_text(image_path):
    """
    이미지에서 텍스트를 추출합니다.

    Returns:
        list of dict: [{"text": str, "confidence": float, "y_center": float}, ...]
    """
    reader = get_reader()
    img_array = preprocess_image(image_path)
    results = reader.readtext(img_array)

    extracted = []
    for bbox, text, confidence in results:
        if confidence < CONFIDENCE_THRESHOLD:
            continue
        # bbox는 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] 형태
        y_center = sum(point[1] for point in bbox) / 4
        extracted.append({
            "text": text.strip(),
            "confidence": confidence,
            "y_center": y_center,
        })

    # Y좌표 기준 정렬 (위에서 아래로)
    extracted.sort(key=lambda x: x["y_center"])
    return extracted


def get_text_lines(image_path):
    """
    이미지에서 텍스트를 추출하고 줄 단위로 그룹핑합니다.

    Returns:
        list of str: 줄 단위 텍스트 리스트 (위에서 아래 순서)
        float: 평균 confidence
    """
    blocks = extract_text(image_path)

    if not blocks:
        return [], 0.0

    # Y좌표 근접한 블록을 같은 줄로 그룹핑
    lines = []
    current_line_blocks = [blocks[0]]

    for block in blocks[1:]:
        prev_y = current_line_blocks[-1]["y_center"]
        # Y 차이가 작으면 같은 줄로 판단
        if abs(block["y_center"] - prev_y) < 30:
            current_line_blocks.append(block)
        else:
            line_text = " ".join(b["text"] for b in current_line_blocks)
            lines.append(line_text)
            current_line_blocks = [block]

    # 마지막 줄 추가
    line_text = " ".join(b["text"] for b in current_line_blocks)
    lines.append(line_text)

    avg_confidence = sum(b["confidence"] for b in blocks) / len(blocks)

    return lines, avg_confidence
