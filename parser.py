"""OCR 텍스트에서 날짜, 이름, 회차를 파싱"""
import re
from dataclasses import dataclass


@dataclass
class PhotoInfo:
    date_raw: str        # "260331"
    year: int            # 2026
    month: int           # 3
    day: int             # 31
    patient_name: str    # "김익명"
    visit_number: int    # 3
    confidence: float    # OCR 평균 confidence
    source_path: str     # 원본 파일 경로


# 6자리 날짜 패턴 (YYMMDD)
DATE_PATTERN = re.compile(r'(\d{6})')

# 한글 이름 + 숫자 + 회 패턴
NAME_VISIT_PATTERN = re.compile(r'([가-힣]{2,5})\s*(\d{1,3})\s*회')

# "회" 오인식 대응: 희, 휘, 획 등
NAME_VISIT_PATTERN_ALT = re.compile(r'([가-힣]{2,5})\s*(\d{1,3})\s*[희휘획]')


def parse_date(text):
    """
    텍스트에서 YYMMDD 형식의 날짜를 추출합니다.

    Returns:
        tuple: (date_raw, year, month, day) 또는 None
    """
    match = DATE_PATTERN.search(text)
    if not match:
        return None

    date_str = match.group(1)
    yy = int(date_str[0:2])
    mm = int(date_str[2:4])
    dd = int(date_str[4:6])

    # 유효성 검사
    if mm < 1 or mm > 12:
        return None
    if dd < 1 or dd > 31:
        return None

    year = 2000 + yy
    return (date_str, year, mm, dd)


def parse_name_visit(text):
    """
    텍스트에서 환자 이름과 회차를 추출합니다.

    Returns:
        tuple: (name, visit_number) 또는 None
    """
    # 기본 패턴 시도
    match = NAME_VISIT_PATTERN.search(text)
    if match:
        return (match.group(1), int(match.group(2)))

    # "회" 오인식 대응
    match = NAME_VISIT_PATTERN_ALT.search(text)
    if match:
        return (match.group(1), int(match.group(2)))

    return None


def parse_photo(lines, confidence, source_path):
    """
    OCR 텍스트 줄들에서 PhotoInfo를 파싱합니다.

    Args:
        lines: OCR로 추출된 텍스트 줄 리스트
        confidence: 평균 OCR confidence
        source_path: 원본 이미지 경로

    Returns:
        PhotoInfo 또는 None
    """
    if not lines:
        return None

    # 모든 줄을 합쳐서 파싱 시도
    full_text = " ".join(lines)

    date_result = parse_date(full_text)
    name_result = parse_name_visit(full_text)

    if date_result is None or name_result is None:
        # 줄 단위로 개별 파싱 시도
        for line in lines:
            if date_result is None:
                date_result = parse_date(line)
            if name_result is None:
                name_result = parse_name_visit(line)

    if date_result is None or name_result is None:
        return None

    date_raw, year, month, day = date_result
    patient_name, visit_number = name_result

    return PhotoInfo(
        date_raw=date_raw,
        year=year,
        month=month,
        day=day,
        patient_name=patient_name,
        visit_number=visit_number,
        confidence=confidence,
        source_path=source_path,
    )
