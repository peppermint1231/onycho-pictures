"""OCR 텍스트에서 날짜, 이름, 회차를 파싱"""
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from itertools import combinations

# === 데이터 구조 ===

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
    visit_raw: str = ""  # "3회" 또는 "fu10" 등 원본 표기
    corrected: bool = False  # 보정 적용 여부


# === 정규식 패턴 ===

DATE_PATTERN = re.compile(r'(\d{6})')
# 이름 + (영문 노이즈 허용) + 숫자 + 회 (예: "김명재F10회")
NAME_VISIT_PATTERN = re.compile(r'([가-힣]{2,5})\s*[a-zA-Z]?\s*(\d{1,3})\s*회')
# "회" 오인식 대응 (희, 휘, 획)
NAME_VISIT_PATTERN_ALT = re.compile(r'([가-힣]{2,5})\s*[a-zA-Z]?\s*(\d{1,3})\s*[희휘획]')
# fu(follow-up) 패턴
NAME_FU_PATTERN = re.compile(r'([가-힣]{2,5})\s+[fF][uU]\s*(\d{1,3})')
# 파일명 타임스탬프 패턴
FILENAME_TS_PATTERN = re.compile(r'(\d{8})_(\d{6})')

# 일반 회차 유효 범위
VISIT_MIN, VISIT_MAX = 1, 10
# 파일명-OCR 날짜 허용 오차 (일)
DATE_TOLERANCE_DAYS = 5


# === 학습 데이터 관리 ===

_LEARN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_learned_corrections.json")
_learned_data = None
_EMPTY_LEARNED = {"name_corrections": {}, "visit_corrections": {}, "date_corrections": {}}


def _load_learned():
    """학습된 보정 패턴을 로드합니다 (싱글턴)."""
    global _learned_data
    if _learned_data is not None:
        return _learned_data
    if os.path.exists(_LEARN_FILE):
        try:
            with open(_LEARN_FILE, "r", encoding="utf-8") as f:
                _learned_data = json.load(f)
        except Exception:
            _learned_data = dict(_EMPTY_LEARNED)
    else:
        _learned_data = dict(_EMPTY_LEARNED)
    return _learned_data


def save_learned(ocr_text, corrected_info):
    """
    사용자가 수기로 수정한 결과를 학습합니다.
    OCR 인식값과 수정값이 다를 때만 저장합니다 (임의 수정 방지).
    """
    data = _load_learned()
    changed = False

    name_match = re.search(r'([가-힣]{2,5})', ocr_text)
    if name_match and name_match.group(1) != corrected_info.patient_name:
        data["name_corrections"][name_match.group(1)] = corrected_info.patient_name
        changed = True

    visit_match = re.search(r'(\d{1,3})\s*[회희휘획]', ocr_text)
    if visit_match and visit_match.group(1) != str(corrected_info.visit_number):
        data["visit_corrections"][visit_match.group(1)] = str(corrected_info.visit_number)
        changed = True

    date_match = DATE_PATTERN.search(ocr_text)
    if date_match and date_match.group(1) != corrected_info.date_raw:
        data["date_corrections"][date_match.group(1)] = corrected_info.date_raw
        changed = True

    if not changed:
        return
    try:
        with open(_LEARN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        global _learned_data
        _learned_data = data
    except Exception:
        pass


def _get_learned(category, key):
    """학습된 보정값을 조회합니다."""
    return _load_learned()[category].get(key)


# === 파일명 유틸리티 ===

def _extract_timestamp(filepath):
    """파일명에서 촬영 시각을 추출합니다. 예: "20251107_142732.jpg" → datetime"""
    if not filepath:
        return None
    match = FILENAME_TS_PATTERN.match(os.path.basename(filepath))
    if match:
        try:
            return datetime.strptime(match.group(1) + match.group(2), '%Y%m%d%H%M%S')
        except Exception:
            return None
    return None


def _extract_date_from_filename(filepath):
    """파일명에서 날짜를 추출합니다. 예: "20251107_142732.jpg" → "251107" """
    if not filepath:
        return None
    match = re.match(r'(\d{8})', os.path.basename(filepath))
    if match:
        return match.group(1)[2:]  # YYYYMMDD → YYMMDD
    return None


def _date_distance(date1, date2):
    """두 YYMMDD 날짜의 일수 차이를 계산합니다."""
    try:
        d1 = date(2000 + int(date1[0:2]), int(date1[2:4]), int(date1[4:6]))
        d2 = date(2000 + int(date2[0:2]), int(date2[2:4]), int(date2[4:6]))
        return abs((d1 - d2).days)
    except Exception:
        return 9999


def group_consecutive_photos(image_paths, max_gap_seconds=300):
    """
    연속 촬영된 사진을 그룹으로 묶습니다 (기본 5분 이내).
    Returns: list of list (각 그룹의 파일 경로 리스트)
    """
    if not image_paths:
        return []

    max_dt = datetime.max
    stamped = [(p, _extract_timestamp(p)) for p in image_paths]
    stamped.sort(key=lambda x: x[1] if x[1] else max_dt)

    groups = []
    current_group = [stamped[0][0]]
    prev_ts = stamped[0][1]

    for path, ts in stamped[1:]:
        if ts and prev_ts and (ts - prev_ts).total_seconds() <= max_gap_seconds:
            current_group.append(path)
        else:
            groups.append(current_group)
            current_group = [path]
        prev_ts = ts

    groups.append(current_group)
    return groups


# === 날짜 보정 ===

def _fix_ocr_7_to_1(date_str, filename_date=None):
    """
    OCR 1→7 오인식 보정. 7→1 방향으로만 보정합니다.
    - 학습된 보정 우선 적용
    - 후보가 복수면 파일명 날짜에 가장 가까운 후보 선택 (±5일 이내)
    - 확신 없으면 None 반환 (실패 처리)
    """
    learned = _get_learned("date_corrections", date_str)
    if learned:
        return learned

    positions = [i for i, c in enumerate(date_str) if c == '7']
    if not positions:
        return None

    # 7→1 치환 조합으로 유효한 날짜 후보 수집
    candidates = []
    for count in range(1, len(positions) + 1):
        for combo in combinations(positions, count):
            chars = list(date_str)
            for pos in combo:
                chars[pos] = '1'
            candidate = ''.join(chars)
            mm, dd = int(candidate[2:4]), int(candidate[4:6])
            if 1 <= mm <= 12 and 1 <= dd <= 31:
                candidates.append(candidate)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # 파일명 날짜로 가장 가까운 후보 선택
    if filename_date:
        scored = sorted(candidates, key=lambda c: _date_distance(c, filename_date))
        if _date_distance(scored[0], filename_date) <= DATE_TOLERANCE_DAYS:
            return scored[0]

    # 최소 변환 후보 필터링
    change_counts = {c: sum(a != b for a, b in zip(date_str, c)) for c in candidates}
    min_changes = min(change_counts.values())
    min_candidates = [c for c, n in change_counts.items() if n == min_changes]
    if len(min_candidates) == 1:
        return min_candidates[0]

    return None


# === 파싱 함수 ===

def parse_date(text, source_path=None):
    """
    텍스트에서 YYMMDD 날짜를 추출합니다.
    파일명 날짜를 힌트로 7→1 보정을 시도합니다.
    Returns: (date_raw, year, month, day, corrected) 또는 None
    """
    match = DATE_PATTERN.search(text)
    if not match:
        return None

    date_str = match.group(1)
    yy, mm, dd = int(date_str[0:2]), int(date_str[2:4]), int(date_str[4:6])
    corrected = False
    filename_date = _extract_date_from_filename(source_path)

    if mm < 1 or mm > 12 or dd < 1 or dd > 31:
        fixed = _fix_ocr_7_to_1(date_str, filename_date)
        if fixed:
            date_str = fixed
            yy, mm, dd = int(date_str[0:2]), int(date_str[2:4]), int(date_str[4:6])
            corrected = True
        else:
            return None
    elif '7' in date_str and filename_date:
        # 유효한 날짜라도 7→1 보정이 파일명에 더 가까우면 적용
        current_dist = _date_distance(date_str, filename_date)
        fixed = _fix_ocr_7_to_1(date_str, filename_date)
        if fixed:
            fixed_dist = _date_distance(fixed, filename_date)
            if fixed_dist < current_dist and fixed_dist <= DATE_TOLERANCE_DAYS:
                date_str = fixed
                yy, mm, dd = int(date_str[0:2]), int(date_str[2:4]), int(date_str[4:6])
                corrected = True

    return (date_str, 2000 + yy, mm, dd, corrected)


def _apply_learned_to_match(name, visit_num):
    """학습된 이름/회차 보정을 적용합니다."""
    learned_name = _get_learned("name_corrections", name)
    if learned_name:
        name = learned_name
    learned_visit = _get_learned("visit_corrections", str(visit_num))
    if learned_visit:
        visit_num = int(learned_visit)
    return name, visit_num


def parse_name_visit(text):
    """
    텍스트에서 환자 이름과 회차를 추출합니다.
    Returns: (name, visit_number, visit_raw) 또는 None
    """
    # 이름 + 숫자 + 회
    match = NAME_VISIT_PATTERN.search(text)
    if match:
        name, visit_num = _apply_learned_to_match(match.group(1), int(match.group(2)))
        if VISIT_MIN <= visit_num <= VISIT_MAX:
            return (name, visit_num, f"{visit_num}회")
        return None

    # "회" 오인식 대응
    match = NAME_VISIT_PATTERN_ALT.search(text)
    if match:
        name, visit_num = _apply_learned_to_match(match.group(1), int(match.group(2)))
        if VISIT_MIN <= visit_num <= VISIT_MAX:
            return (name, visit_num, f"{visit_num}회")
        return None

    # fu(follow-up) 패턴 (범위 제한 없음)
    match = NAME_FU_PATTERN.search(text)
    if match:
        name, visit_num = _apply_learned_to_match(match.group(1), int(match.group(2)))
        return (name, visit_num, f"fu{visit_num}")

    return None


def parse_photo(lines, confidence, source_path):
    """OCR 텍스트 줄들에서 PhotoInfo를 파싱합니다."""
    if not lines:
        return None

    full_text = " ".join(lines)
    date_result = parse_date(full_text, source_path)
    name_result = parse_name_visit(full_text)

    # 줄 단위 개별 파싱 시도
    if date_result is None or name_result is None:
        for line in lines:
            if date_result is None:
                date_result = parse_date(line, source_path)
            if name_result is None:
                name_result = parse_name_visit(line)

    if date_result is None or name_result is None:
        return None

    date_raw, year, month, day, corrected = date_result
    patient_name, visit_number, visit_raw = name_result

    return PhotoInfo(
        date_raw=date_raw, year=year, month=month, day=day,
        patient_name=patient_name, visit_number=visit_number,
        confidence=confidence, source_path=source_path,
        visit_raw=visit_raw, corrected=corrected,
    )
